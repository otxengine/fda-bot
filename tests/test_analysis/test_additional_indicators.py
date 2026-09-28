"""Unit tests for the supplementary (non-regime) macro indicators, added
after live use showed the chat had nothing to say about consumption,
production, housing, sentiment, or trade — only the 9 regime-score series."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from analysis.additional_indicators import get_additional_indicators_snapshot, load_additional_indicators_config
from ingestion.models import MacroPoint
from storage.db import write_connection
from storage.writers import upsert_macro_series


def test_config_lists_all_ten_added_series() -> None:
    config = load_additional_indicators_config()
    series_ids = {ind["series_id"] for ind in config}
    assert series_ids == {
        "FRED:RSAFS", "FRED:INDPRO", "FRED:HOUST", "FRED:UMCSENT", "FRED:PPIFIS",
        "FRED:PCEPI", "FRED:PCEPILFE", "FRED:BOPGSTB",
        "FRED:GACDISA066MSFRBNY", "FRED:GACDFSA066MSFRBPHI",
    }


def test_snapshot_reports_no_data_yet_without_crashing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        snapshot = get_additional_indicators_snapshot(conn)
        conn.close()

    assert len(snapshot) == 10
    assert all(row["value"] is None and row["as_of_date"] is None for row in snapshot)


def test_snapshot_reports_latest_value_and_as_of_date_when_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        upsert_macro_series(
            conn,
            [MacroPoint(series_id="FRED:HOUST", date=date(2026, 7, 1), value=1350.0, source="fred")],
        )
        snapshot = get_additional_indicators_snapshot(conn)
        conn.close()

    houst = next(row for row in snapshot if row["series_id"] == "FRED:HOUST")
    assert houst["value"] == 1350.0
    assert houst["as_of_date"] == "2026-07-01"
