"""Sector rotation / relative-strength view — backs the chat's "where is
money flowing" questions and the Macro page's sector panel. Descriptive only:
relative return vs. SPY over a lookback window, and a volume-based "flow"
proxy (today's volume vs. its own 20-day average). Never framed as a
prediction — see plan's Non-Goals (no ML/forecast) and the chat's grounding
rule (analysis/chat_tools.py)."""

from __future__ import annotations

import sqlite3
import pandas as pd

from analysis.technicals import get_price_history
from universe.sector_etfs import BENCHMARK_SYMBOL, SECTOR_ETFS


def _return_over_lookback(df: pd.DataFrame, lookback_days: int) -> float | None:
    if df.empty or len(df) < 2:
        return None
    cutoff = df.index[-1] - pd.Timedelta(days=lookback_days)
    window = df[df.index >= cutoff]
    if len(window) < 2:
        return None
    start, end = window["close"].iloc[0], window["close"].iloc[-1]
    if start == 0:
        return None
    return (end - start) / start * 100


def compute_sector_rotation(
    conn: sqlite3.Connection, lookback_days: int = 21
) -> pd.DataFrame:
    """One row per sector ETF: return_pct, relative_strength_vs_spy,
    volume_vs_avg_pct. Empty frame if SPY/sector data isn't loaded yet."""
    spy_df = get_price_history(conn, BENCHMARK_SYMBOL, interval="1d")
    spy_return = _return_over_lookback(spy_df, lookback_days)

    rows = []
    for symbol, sector_name in SECTOR_ETFS.items():
        df = get_price_history(conn, symbol, interval="1d")
        if df.empty:
            continue
        ret = _return_over_lookback(df, lookback_days)
        relative_strength = (ret - spy_return) if (ret is not None and spy_return is not None) else None

        volume_vs_avg_pct = None
        if len(df) >= 20:
            avg_vol_20d = df["volume"].rolling(20).mean().iloc[-1]
            latest_vol = df["volume"].iloc[-1]
            if avg_vol_20d and avg_vol_20d > 0:
                volume_vs_avg_pct = (latest_vol - avg_vol_20d) / avg_vol_20d * 100

        rows.append(
            {
                "symbol": symbol,
                "sector": sector_name,
                "return_pct": ret,
                "relative_strength_vs_spy": relative_strength,
                "volume_vs_avg_pct": volume_vs_avg_pct,
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("relative_strength_vs_spy", ascending=False).reset_index(drop=True)
    return out
