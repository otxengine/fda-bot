"""Regression test for a real crash found during live end-to-end testing:
`ValueError: Mixed timezones detected` when reading price_bars back out.
yfinance returns exchange-local tz-aware timestamps (America/New_York); a
2-year daily history crosses EST/EDT DST transitions, so the stored ISO
strings ended up with different UTC offsets, which pandas 2.x's
`pd.to_datetime()` refuses to parse as a mix. Fixed by normalizing to UTC
before storing (this test) and by defense-in-depth `utc=True` on read (see
tests/test_analysis/test_technicals.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from connectors.base import ConnectorResult
from ingestion.models import PriceBar
from ingestion.normalizers.yahoo_finance import YahooFinanceNormalizer


def _history_spanning_dst(tz_name: str = "America/New_York") -> pd.DataFrame:
    # One row in EST (winter), one in EDT (summer) — genuinely different UTC
    # offsets for the same exchange timezone, exactly what crossed a 2-year
    # daily history in production.
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-15 09:30:00", tz=ZoneInfo(tz_name)),  # EST, -05:00
            pd.Timestamp("2024-07-15 09:30:00", tz=ZoneInfo(tz_name)),  # EDT, -04:00
        ]
    )
    return pd.DataFrame(
        {"Open": [100.0, 110.0], "High": [101.0, 111.0], "Low": [99.0, 109.0],
         "Close": [100.5, 110.5], "Volume": [1000, 2000]},
        index=idx,
    )


def test_price_bars_are_normalized_to_utc_despite_dst_boundary() -> None:
    payload = {
        "AAPL": {
            "info": {},
            "history_daily": _history_spanning_dst(),
            "history_intraday": None,
            "news": [],
        }
    }
    result = ConnectorResult(
        connector_name="yahoo_finance", fetched_at=datetime.now(timezone.utc), status="ok", raw_payload=payload
    )
    records = YahooFinanceNormalizer().normalize(result)
    bars = [r for r in records if isinstance(r, PriceBar)]
    assert len(bars) == 2

    for bar in bars:
        assert bar.ts.tzinfo is not None
        assert bar.ts.utcoffset().total_seconds() == 0, (
            f"expected UTC-normalized timestamp, got offset {bar.ts.utcoffset()} for {bar.ts}"
        )

    # The whole point: storing these as ISO strings must never reproduce the
    # "mixed offsets" shape that crashed pd.to_datetime() on read.
    iso_strings = [bar.ts.isoformat() for bar in bars]
    assert all(s.endswith("+00:00") for s in iso_strings)
