"""Supplementary macro indicators (config/additional_indicators.yaml) — NOT
part of the regime scoring engine (see analysis/macro_regime.py for that),
just broader coverage for the Macro page's "Additional Indicators" section
and the chat's list_available_indicators/get_indicator_history tools. Added
because the original 9-series regime set left the chat with almost nothing
to say about consumption, production, housing, sentiment, or trade."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Any

import yaml

from analysis.macro_regime import get_series
from config.settings import get_settings


@lru_cache
def load_additional_indicators_config() -> list[dict[str, str]]:
    with open(get_settings().additional_indicators_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["indicators"]


def get_additional_indicators_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per configured indicator: latest level + its data-vintage date
    (None/None if nothing's been fetched for it yet)."""
    rows = []
    for ind in load_additional_indicators_config():
        series = get_series(conn, ind["series_id"])
        if series.empty:
            rows.append({**ind, "value": None, "as_of_date": None})
        else:
            rows.append(
                {
                    **ind,
                    "value": round(float(series.iloc[-1]), 3),
                    "as_of_date": series.index[-1].date().isoformat(),
                }
            )
    return rows
