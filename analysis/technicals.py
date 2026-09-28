"""Technical indicators via pandas-ta, computed on read from price_bars.
Note (see plan's library table): pandas-ta is unmaintained upstream — pinned
in pyproject.toml; if it breaks on a future pandas release, swap for the
`pandas-ta-classic` fork or the `ta` package behind this same module."""

from __future__ import annotations

import sqlite3
import pandas as pd


def get_price_history(
    conn: sqlite3.Connection, symbol: str, interval: str = "1d"
) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM price_bars "
        "WHERE symbol = ? AND interval = ? ORDER BY ts",
        [symbol, interval],
    ).fetchall()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df
    # utc=True: defense in depth against mixed UTC offsets in the stored ISO
    # strings (normalizers are expected to store UTC already — see
    # ingestion/normalizers/yahoo_finance.py — but pandas 2.x hard-fails on a
    # mixed-offset parse rather than degrading, so this must never be assumed).
    df = df.set_index(pd.to_datetime(df.pop("ts"), utc=True))
    # Defense in depth, take 2: two DIFFERENT raw ts strings (e.g. "...-04:00"
    # exchange-local vs "...+00:00" already-UTC) can represent the SAME instant
    # once parsed, producing duplicate index entries pandas_ta then crashes on
    # ("cannot reindex on an axis with duplicate labels") — found live in the
    # running background service from stale pre-fix rows left over from
    # before the normalizer started UTC-normalizing consistently (since the
    # SQL PRIMARY KEY is the raw string, those never upsert-merged with the
    # new correctly-formatted rows). The root cause is now fixed at the write
    # path AND the stale rows were cleaned up, but this stays as a permanent
    # safety net against any future write-path regression: keep the last
    # (most recently written) row for any date that still collides.
    return df[~df.index.duplicated(keep="last")]


def with_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """Adds SMA20/50/200, RSI14, MACD, Bollinger Bands columns. Returns df
    unchanged (empty) if there's not enough history yet."""
    if df.empty:
        return df
    import pandas_ta as ta  # imported lazily — optional heavy dep, only needed here

    out = df.copy()
    out["sma20"] = ta.sma(out["close"], length=20)
    out["sma50"] = ta.sma(out["close"], length=50)
    out["sma200"] = ta.sma(out["close"], length=200)
    out["rsi14"] = ta.rsi(out["close"], length=14)

    macd = ta.macd(out["close"])
    if macd is not None:
        out = out.join(macd)

    bbands = ta.bbands(out["close"], length=20)
    if bbands is not None:
        out = out.join(bbands)

    out["avg_volume_20d"] = out["volume"].rolling(20).mean()
    return out


def latest_snapshot(df_with_technicals: pd.DataFrame) -> dict:
    """Small dict of the most recent row's key technical values — used by the
    screener (analysis/screener.py) and the chat tools, not just the UI."""
    if df_with_technicals.empty:
        return {}
    last = df_with_technicals.iloc[-1]
    sma50 = last.get("sma50")
    close = last.get("close")
    pct_above_sma50 = None
    if sma50 and not pd.isna(sma50) and sma50 != 0:
        pct_above_sma50 = (close - sma50) / sma50 * 100
    return {
        "close": _f(close),
        "rsi_14": _f(last.get("rsi14")),
        "sma20": _f(last.get("sma20")),
        "sma50": _f(sma50),
        "sma200": _f(last.get("sma200")),
        "pct_above_sma50": _f(pct_above_sma50),
        "avg_volume_20d": _f(last.get("avg_volume_20d")),
    }


def _f(v) -> float | None:
    if v is None or pd.isna(v):
        return None
    return float(v)
