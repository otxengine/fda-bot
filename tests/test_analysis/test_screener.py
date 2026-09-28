"""Unit tests for the screener's percentile-rank scoring — synthetic data
with known expected output (plan's Verification section: this locks in the
normalization/hard-filter logic the plan's Analysis Design specifically
calls for)."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.screener import score_candidates

CRITERIA = {
    "criteria": [
        {"field": "pe_ratio", "direction": "lower_is_better", "weight": 0.5, "hard_filter_max": 60},
        {"field": "market_cap", "direction": "higher_is_better", "weight": 0.5, "hard_filter_min": 2_000_000_000},
    ]
}


def test_hard_filter_excludes_symbols_outside_bounds() -> None:
    df = pd.DataFrame(
        [
            {"symbol": "A", "pe_ratio": 10, "market_cap": 5e9},
            {"symbol": "TOO_EXPENSIVE_PE", "pe_ratio": 70, "market_cap": 5e9},   # fails hard_filter_max
            {"symbol": "TOO_SMALL", "pe_ratio": 10, "market_cap": 1e9},          # fails hard_filter_min
        ]
    )
    scored = score_candidates(df, CRITERIA)
    assert set(scored["symbol"]) == {"A"}


def test_percentile_rank_scoring_is_robust_to_raw_scale() -> None:
    # Raw values live on wildly different scales (P/E ~10-20 vs market cap in
    # billions) — the score must come from percentile rank, not the raw sum.
    df = pd.DataFrame(
        [
            {"symbol": "A", "pe_ratio": 10, "market_cap": 5e9},
            {"symbol": "B", "pe_ratio": 20, "market_cap": 10e9},
            {"symbol": "C", "pe_ratio": 15, "market_cap": 15e9},
        ]
    )
    scored = score_candidates(df, CRITERIA).set_index("symbol")

    # C has the best market cap and a mid P/E -> should rank first.
    # B has the worst (highest) P/E and only a mid market cap -> should rank last.
    assert scored.loc["C", "rank"] == 1
    assert scored.loc["B", "rank"] == 3
    assert scored.loc["A", "score"] == pytest.approx(50.0, abs=0.1)
    assert scored.loc["B", "score"] == pytest.approx(33.33, abs=0.1)
    assert scored.loc["C", "score"] == pytest.approx(66.67, abs=0.1)


def test_missing_criterion_value_scores_zero_on_that_criterion_not_excluded() -> None:
    df = pd.DataFrame(
        [
            {"symbol": "A", "pe_ratio": 10, "market_cap": 5e9},
            {"symbol": "MISSING_PE", "pe_ratio": None, "market_cap": 10e9},
        ]
    )
    scored = score_candidates(df, CRITERIA)
    # Both rows survive (missing data isn't a hard filter failure) — MISSING_PE
    # just scores 0 on the pe_ratio criterion rather than being dropped.
    assert set(scored["symbol"]) == {"A", "MISSING_PE"}


def test_empty_input_returns_empty() -> None:
    assert score_candidates(pd.DataFrame(), CRITERIA).empty
