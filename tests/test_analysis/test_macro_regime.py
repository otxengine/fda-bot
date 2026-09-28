"""Unit tests for the rule-based macro regime scoring engine — synthetic
data with known expected output (plan's Verification section)."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from analysis.macro_regime import _classify, apply_transform, compute_regime
from ingestion.models import MacroPoint
from storage.db import write_connection
from storage.writers import upsert_macro_series


def test_apply_transform_none() -> None:
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]))
    assert apply_transform(s, "none") == 3.0


def test_apply_transform_yoy_pct() -> None:
    s = pd.Series([300.0, 305.0], index=pd.to_datetime(["2023-06-15", "2024-06-15"]))
    result = apply_transform(s, "yoy_pct")
    assert result == pytest.approx((305 - 300) / 300 * 100, rel=1e-6)


def test_apply_transform_mom_diff() -> None:
    s = pd.Series([5.50, 5.25], index=pd.to_datetime(["2024-04-15", "2024-06-15"]))
    result = apply_transform(s, "mom_diff")
    assert result == pytest.approx(5.25 - 5.50, rel=1e-6)


def test_apply_transform_empty_series_returns_none() -> None:
    assert apply_transform(pd.Series(dtype=float), "none") is None


def test_classify_green_if_above() -> None:
    cfg = {"green_if_above": 0.25, "red_if_below": -0.25}
    assert _classify(0.5, cfg) == 1
    assert _classify(0.0, cfg) == 0
    assert _classify(-0.5, cfg) == -1


def test_classify_green_if_below() -> None:
    cfg = {"green_if_below": 2.5, "red_if_above": 4.0}
    assert _classify(1.0, cfg) == 1
    assert _classify(3.0, cfg) == 0
    assert _classify(5.0, cfg) == -1


def _seed_all_green(conn) -> None:
    """One data point (or two, for yoy/mom transforms) per indicator in
    config/macro_thresholds.yaml, chosen to land solidly in the green zone."""
    points = [
        MacroPoint(series_id="FRED:T10Y2Y", date=date(2024, 6, 15), value=0.5, source="fred"),
        MacroPoint(series_id="FRED:FEDFUNDS", date=date(2024, 4, 15), value=5.50, source="fred"),
        MacroPoint(series_id="FRED:FEDFUNDS", date=date(2024, 6, 15), value=5.25, source="fred"),
        MacroPoint(series_id="FRED:CPIAUCSL", date=date(2023, 6, 15), value=300.0, source="fred"),
        MacroPoint(series_id="FRED:CPIAUCSL", date=date(2024, 6, 15), value=305.0, source="fred"),
        MacroPoint(series_id="FRED:CPILFESL", date=date(2023, 6, 15), value=300.0, source="fred"),
        MacroPoint(series_id="FRED:CPILFESL", date=date(2024, 6, 15), value=305.0, source="fred"),
        MacroPoint(series_id="FRED:UNRATE", date=date(2024, 6, 15), value=4.0, source="fred"),
        MacroPoint(series_id="FRED:PAYEMS", date=date(2024, 4, 15), value=155000.0, source="fred"),
        MacroPoint(series_id="FRED:PAYEMS", date=date(2024, 6, 15), value=155250.0, source="fred"),
        MacroPoint(series_id="FRED:GDPC1", date=date(2023, 6, 15), value=21500.0, source="fred"),
        MacroPoint(series_id="FRED:GDPC1", date=date(2024, 6, 15), value=22000.0, source="fred"),
    ]
    upsert_macro_series(conn, points)


def test_compute_regime_all_green_yields_expansion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "regime_test.db")
        _seed_all_green(conn)
        result = compute_regime(conn)
        conn.close()

    assert result["regime_label"] == "Expansion"
    for score in result["category_scores"].values():
        assert score == pytest.approx(1.0)
    assert result["overall_score"] == pytest.approx(1.0)
    assert result["coverage"] == pytest.approx(1.0)


def test_compute_regime_details_include_data_vintage_date() -> None:
    """Each indicator's 'as_of_date' is the date the underlying economic
    figure was published for — distinct from (and usually older than) when
    we fetched it — so the UI/chat can honestly answer "how current is
    this?" (see dashboard/pages/1_Macro.py and chat_tools.SYSTEM_PROMPT)."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "regime_asof_test.db")
        _seed_all_green(conn)
        result = compute_regime(conn)
        conn.close()

    rates_details = result["details"]["rates"]
    t10y2y = next(d for d in rates_details if d["series_id"] == "FRED:T10Y2Y")
    assert t10y2y["as_of_date"] == "2024-06-15"


def test_compute_regime_with_zero_data_is_insufficient_not_a_fake_label() -> None:
    """Regression test for a real bug found via live end-to-end testing: with
    zero macro_series rows (e.g. FRED/BLS/BEA keys not configured), the
    engine used to default every category to 0.0 and still confidently
    output a specific label ("Late-cycle") — a textbook false-precision
    failure. It must instead say plainly that there isn't enough data."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "regime_empty_test.db")
        result = compute_regime(conn)  # no macro_series rows seeded at all
        conn.close()

    assert result["regime_label"] == "Insufficient data"
    assert result["overall_score"] is None
    assert all(score is None for score in result["category_scores"].values())
    assert result["indicators_with_data"] == 0
    assert result["coverage"] == pytest.approx(0.0)


def test_compute_regime_with_partial_data_excludes_missing_categories_from_average() -> None:
    """A category with no data contributes None, not 0.0 — so it's excluded
    from the overall average rather than silently dragging it toward
    "neutral"."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "regime_partial_test.db")
        # Only seed the 'rates' category's two indicators, both green.
        upsert_macro_series(
            conn,
            [
                MacroPoint(series_id="FRED:T10Y2Y", date=date(2024, 6, 15), value=0.5, source="fred"),
                MacroPoint(series_id="FRED:FEDFUNDS", date=date(2024, 4, 15), value=5.50, source="fred"),
                MacroPoint(series_id="FRED:FEDFUNDS", date=date(2024, 6, 15), value=5.25, source="fred"),
            ],
        )
        result = compute_regime(conn)
        conn.close()

    assert result["category_scores"]["rates"] == pytest.approx(1.0)
    assert result["category_scores"]["inflation"] is None
    assert result["category_scores"]["employment"] is None
    assert result["category_scores"]["growth"] is None
    # Overall average is over the ONE available category only, not diluted by
    # three phantom 0.0s from the missing categories.
    assert result["overall_score"] == pytest.approx(1.0)
    assert result["regime_label"] == "Expansion"
