"""S&P 500 constituent list — simple/low-fragility Wikipedia table fetch
(Phase 2's screener universe). Refresh weekly; writes into `instruments`."""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

import requests
import sqlite3
import pandas as pd

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_constituents() -> list[dict]:
    # Wikipedia's servers 403 the default urllib/pandas User-Agent — fetch
    # with `requests` and a real one, then hand pandas the HTML text directly.
    resp = requests.get(
        SP500_WIKI_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0 (finresearch-personal-tool/0.1)"}
    )
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    records = []
    for _, row in df.iterrows():
        # yfinance uses '-' where Wikipedia uses '.' (e.g. BRK.B -> BRK-B)
        symbol = str(row["Symbol"]).strip().replace(".", "-")
        records.append(
            {
                "symbol": symbol,
                "name": row.get("Security"),
                "sector": row.get("GICS Sector"),
                "industry": row.get("GICS Sub-Industry"),
            }
        )
    return records


def upsert_instruments(conn: sqlite3.Connection, records: list[dict], asset_type: str = "equity") -> int:
    if not records:
        return 0
    now = datetime.now(timezone.utc)
    conn.executemany(
        """
        INSERT INTO instruments (symbol, name, asset_type, exchange, sector, industry, currency, is_watchlist, added_at)
        VALUES (?, ?, ?, NULL, ?, ?, 'USD', FALSE, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            name = excluded.name, sector = excluded.sector, industry = excluded.industry
        """,
        [(r["symbol"], r.get("name"), asset_type, r.get("sector"), r.get("industry"), now) for r in records],
    )
    conn.commit()
    return len(records)
