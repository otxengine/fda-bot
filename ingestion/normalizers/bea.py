from __future__ import annotations

from datetime import date

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import MacroPoint


def _quarter_to_date(period: str) -> date | None:
    # e.g. "2024Q1" -> first day of that quarter
    if "Q" not in period:
        return None
    year_s, q_s = period.split("Q")
    quarter = int(q_s)
    if not 1 <= quarter <= 4:
        return None
    month = (quarter - 1) * 3 + 1
    return date(int(year_s), month, 1)


class BeaNormalizer(BaseNormalizer):
    """Extracts the headline 'Gross domestic product' line (LineNumber 1) from
    NIPA Table T10106 and stores it as BEA:GDP."""

    def normalize(self, result: ConnectorResult) -> list[MacroPoint]:
        if result.status != "ok" or not result.raw_payload:
            return []
        rows = result.raw_payload.get("BEAAPI", {}).get("Results", {}).get("Data", [])
        points: list[MacroPoint] = []
        for row in rows:
            if row.get("LineNumber") != "1":
                continue
            d = _quarter_to_date(row.get("TimePeriod", ""))
            if d is None:
                continue
            try:
                value = float(str(row["DataValue"]).replace(",", ""))
            except (TypeError, ValueError, KeyError):
                continue
            points.append(MacroPoint(series_id="BEA:GDP", date=d, value=value, source="bea"))
        return points
