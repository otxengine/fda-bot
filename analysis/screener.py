"""Broad-universe screener. Every criterion is percentile-ranked (0-100)
within the current run's candidates before being combined as a weighted sum —
raw values are never combined directly (different units/scales; percentile
rank is robust to outliers like negative-earnings P/E). See plan's Analysis
Design section for the rationale."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import sqlite3
import pandas as pd
import yaml

from analysis.technicals import get_price_history, latest_snapshot, with_technicals
from config.settings import get_settings


@lru_cache
def load_criteria() -> dict:
    with open(get_settings().screener_criteria_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_candidate_table(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per instrument: the raw feature matrix the scorer ranks."""
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM instruments").fetchall()]
    rows = []
    for symbol in symbols:
        q = conn.execute(
            "SELECT price, market_cap, pe_ratio FROM quotes_latest WHERE symbol = ?", [symbol]
        ).fetchone()
        if q is None:
            continue
        f = conn.execute(
            "SELECT revenue_growth_yoy FROM fundamentals_history WHERE symbol = ? "
            "ORDER BY as_of DESC LIMIT 1",
            [symbol],
        ).fetchone()
        df = get_price_history(conn, symbol, interval="1d")
        tech = latest_snapshot(with_technicals(df)) if not df.empty else {}
        rows.append(
            {
                "symbol": symbol,
                "pe_ratio": q[2],
                "market_cap": q[1],
                "rsi_14": tech.get("rsi_14"),
                "pct_above_sma50": tech.get("pct_above_sma50"),
                "avg_volume_20d": tech.get("avg_volume_20d"),
                "revenue_growth_yoy": f[0] if f else None,
            }
        )
    return pd.DataFrame(rows)


def score_candidates(df: pd.DataFrame, criteria_cfg: dict) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()

    for c in criteria_cfg["criteria"]:
        field = c["field"]
        if field not in work.columns:
            continue
        if "hard_filter_min" in c:
            work = work[work[field].isna() | (work[field] >= c["hard_filter_min"])]
        if "hard_filter_max" in c:
            work = work[work[field].isna() | (work[field] <= c["hard_filter_max"])]

    if work.empty:
        return work

    score = pd.Series(0.0, index=work.index)
    total_weight = 0.0
    for c in criteria_cfg["criteria"]:
        field = c["field"]
        if field not in work.columns:
            continue
        pct = work[field].rank(pct=True) * 100  # 0-100, robust to outliers/scale mismatch
        if c["direction"] == "lower_is_better":
            pct = 100 - pct
        pct = pct.fillna(0)  # missing data scores 0 on that criterion rather than excluding the row
        score = score + pct * c["weight"]
        total_weight += c["weight"]

    work["score"] = score / total_weight if total_weight else score
    work = work.sort_values("score", ascending=False).reset_index(drop=True)
    work["rank"] = work.index + 1
    return work


def run_screener(conn: sqlite3.Connection) -> pd.DataFrame:
    cfg = load_criteria()
    candidates = build_candidate_table(conn)
    scored = score_candidates(candidates, cfg)
    if scored.empty:
        return scored

    run_id = str(uuid.uuid4())
    run_ts = datetime.now(timezone.utc)
    criteria_json = json.dumps(cfg["criteria"])
    conn.executemany(
        """
        INSERT INTO screener_snapshots (run_id, run_ts, symbol, rank, score, criteria_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, symbol) DO NOTHING
        """,
        [(run_id, run_ts, row.symbol, int(row.rank), float(row.score), criteria_json) for row in scored.itertuples()],
    )
    _flag_new_entrants(conn, run_id, scored, cfg.get("new_entrant_rank_threshold", 25))
    conn.commit()
    return scored


def _top_symbols(conn: sqlite3.Connection, run_id: str, threshold: int) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT symbol FROM screener_snapshots WHERE run_id = ? AND rank <= ?", [run_id, threshold]
        ).fetchall()
    }


def _flag_new_entrants(conn: sqlite3.Connection, run_id: str, scored: pd.DataFrame, threshold: int) -> None:
    """Only flags a symbol once it's held a top-N spot for TWO consecutive
    runs — avoids noisy single-run churn (see plan's Analysis Design)."""
    prior_run_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT run_id FROM screener_snapshots WHERE run_id != ? "
            "ORDER BY run_ts DESC LIMIT 2",
            [run_id],
        ).fetchall()
    ]
    if not prior_run_ids:
        return  # first run ever — nothing to compare against

    top_now = set(scored[scored["rank"] <= threshold]["symbol"])
    prev_top = _top_symbols(conn, prior_run_ids[0], threshold)
    prev_prev_top = _top_symbols(conn, prior_run_ids[1], threshold) if len(prior_run_ids) > 1 else set()

    new_two_run_entrants = (top_now & prev_top) - prev_prev_top
    if not new_two_run_entrants:
        return

    now = datetime.now(timezone.utc)
    rows = []
    for symbol in new_two_run_entrants:
        flag_id = hashlib.sha256(f"screener_new_hit:{symbol}:{run_id}".encode()).hexdigest()
        rows.append((flag_id, "screener_new_hit", symbol, "watch", f"{symbol} entered the top {threshold} for 2 consecutive runs", now))
    conn.executemany(
        """
        INSERT INTO flags (flag_id, flag_type, symbol, severity, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (flag_id) DO NOTHING
        """,
        rows,
    )
