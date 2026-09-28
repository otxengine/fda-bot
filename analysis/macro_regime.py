"""Rule-based (NOT ML) macro regime scoring — see plan's Non-Goals: this is a
heuristic traffic-light label, never a forecast/probability, and the rule
table it produces is meant to be shown verbatim in the UI's "why" expander.

Each indicator -> green(1)/yellow(0)/red(-1) via config/macro_thresholds.yaml.
Per-category average -> one of 4 category scores. Overall average of those
four -> one regime label via a simple lookup table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import sqlite3
import pandas as pd
import yaml

from config.settings import get_settings

Light = int  # -1, 0, or 1


@lru_cache
def load_thresholds() -> dict[str, Any]:
    with open(get_settings().macro_thresholds_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_series(conn: sqlite3.Connection, series_id: str) -> pd.Series:
    """Fetch one macro_series as a date-sorted pandas Series, empty if absent."""
    rows = conn.execute(
        "SELECT date, value FROM macro_series WHERE series_id = ? ORDER BY date", [series_id]
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([r[1] for r in rows], index=idx).sort_index()


def apply_transform(series: pd.Series, transform: str) -> float | None:
    """Reduce a series to the single scalar the regime engine scores, per its
    configured transform. Uses date-based lookback (asof) rather than a fixed
    integer period, since series frequency varies (daily/monthly/quarterly)."""
    if series.empty:
        return None
    latest_date = series.index[-1]
    latest_value = float(series.iloc[-1])

    if transform == "none":
        return latest_value

    if transform == "yoy_pct":
        year_ago = series.asof(latest_date - pd.DateOffset(years=1))
        if year_ago is None or pd.isna(year_ago) or year_ago == 0:
            return None
        return (latest_value - float(year_ago)) / abs(float(year_ago)) * 100

    if transform == "mom_diff":
        month_ago = series.asof(latest_date - pd.DateOffset(months=1))
        if month_ago is None or pd.isna(month_ago):
            return None
        return latest_value - float(month_ago)

    raise ValueError(f"unknown transform: {transform}")


def _classify(value: float, ind_cfg: dict[str, Any]) -> Light:
    if "green_if_above" in ind_cfg:
        if value >= ind_cfg["green_if_above"]:
            return 1
        red_th = ind_cfg.get("red_if_below")
        if red_th is not None and value <= red_th:
            return -1
        return 0
    if "green_if_below" in ind_cfg:
        if value <= ind_cfg["green_if_below"]:
            return 1
        red_th = ind_cfg.get("red_if_above")
        if red_th is not None and value >= red_th:
            return -1
        return 0
    return 0


def _label_for_score(overall_score: float, regime_labels: list[dict[str, Any]]) -> str:
    for entry in sorted(regime_labels, key=lambda e: -e["min_avg_score"]):
        if overall_score >= entry["min_avg_score"]:
            return entry["label"]
    return "Unknown"


def compute_regime(conn: sqlite3.Connection) -> dict[str, Any]:
    """Pure computation (no writes) — used both by the scheduler job and by
    the dashboard as a read-through fallback if no stored regime exists yet.

    A category with zero indicators reporting data gets score=None, NOT 0.0.
    Treating "no data" as a neutral 0.0 silently produces a specific-looking
    regime label (e.g. "Late-cycle") from literally nothing — found during
    live end-to-end testing with FRED disabled: it still confidently
    labelled the regime, which directly contradicts the plan's own rule
    against false precision. Same reasoning at the overall level: if EVERY
    category is data-less, the label is "Insufficient data", not a lookup
    result computed from an average of zeros."""
    cfg = load_thresholds()
    category_scores: dict[str, float | None] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    total_indicators = 0
    indicators_with_data = 0

    for category, cat_cfg in cfg["categories"].items():
        lights: list[Light] = []
        cat_details: list[dict[str, Any]] = []
        for key, ind_cfg in cat_cfg["indicators"].items():
            total_indicators += 1
            series = get_series(conn, ind_cfg["series_id"])
            value = apply_transform(series, ind_cfg.get("transform", "none"))
            if value is None:
                continue
            indicators_with_data += 1
            light = _classify(value, ind_cfg)
            lights.append(light)
            # The economic DATA has its own publication lag (e.g. CPI for a
            # given month is released weeks later; GDP is quarterly) — this
            # is separate from and usually much older than when WE fetched
            # it (see connector_runs / the freshness row in the UI). Surface
            # both so "is this current?" has an honest answer either way.
            cat_details.append(
                {
                    "key": key,
                    "display_name": ind_cfg["display_name"],
                    "series_id": ind_cfg["series_id"],
                    "transform": ind_cfg.get("transform", "none"),
                    "value": round(value, 3),
                    "light": light,
                    "as_of_date": series.index[-1].date().isoformat(),
                }
            )
        category_scores[category] = (sum(lights) / len(lights)) if lights else None
        details[category] = cat_details

    available_scores = [s for s in category_scores.values() if s is not None]
    coverage = indicators_with_data / total_indicators if total_indicators else 0.0

    if not available_scores:
        overall: float | None = None
        label = "Insufficient data"
    else:
        overall = sum(available_scores) / len(available_scores)
        label = _label_for_score(overall, cfg["regime_labels"])

    return {
        "computed_at": datetime.now(timezone.utc),
        "regime_label": label,
        "overall_score": round(overall, 3) if overall is not None else None,
        "category_scores": {k: (round(v, 3) if v is not None else None) for k, v in category_scores.items()},
        "coverage": round(coverage, 3),
        "indicators_with_data": indicators_with_data,
        "total_indicators": total_indicators,
        "details": details,
    }


def compute_and_store_regime(conn: sqlite3.Connection) -> dict[str, Any]:
    result = compute_regime(conn)
    conn.execute(
        """
        INSERT INTO macro_regime_history
            (computed_at, regime_label, rates_score, inflation_score, employment_score, growth_score, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (computed_at) DO NOTHING
        """,
        (
            result["computed_at"],
            result["regime_label"],
            result["category_scores"].get("rates", 0.0),
            result["category_scores"].get("inflation", 0.0),
            result["category_scores"].get("employment", 0.0),
            result["category_scores"].get("growth", 0.0),
            json.dumps(result["details"]),
        ),
    )
    conn.commit()
    return result


def get_latest_regime(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT computed_at, regime_label, rates_score, inflation_score, employment_score, growth_score, details_json "
        "FROM macro_regime_history ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    details = json.loads(row[6])
    indicators_with_data = sum(len(v) for v in details.values())
    total_indicators = sum(
        len(cat_cfg["indicators"]) for cat_cfg in load_thresholds()["categories"].values()
    )
    return {
        "computed_at": row[0],
        "regime_label": row[1],
        "category_scores": {"rates": row[2], "inflation": row[3], "employment": row[4], "growth": row[5]},
        "coverage": round(indicators_with_data / total_indicators, 3) if total_indicators else 0.0,
        "indicators_with_data": indicators_with_data,
        "total_indicators": total_indicators,
        "details": details,
    }


def get_regime_history(conn: sqlite3.Connection, limit: int = 200) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT computed_at, regime_label, rates_score, inflation_score, employment_score, growth_score "
        "FROM macro_regime_history ORDER BY computed_at DESC LIMIT ?",
        [limit],
    ).fetchall()
    cols = ["computed_at", "regime_label", "rates_score", "inflation_score", "employment_score", "growth_score"]
    return pd.DataFrame(rows, columns=cols).iloc[::-1].reset_index(drop=True)
