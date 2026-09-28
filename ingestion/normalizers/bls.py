from __future__ import annotations

from datetime import date

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import MacroPoint


def _period_to_date(year: str, period: str) -> date | None:
    # Monthly periods are 'M01'..'M12'; skip annual/other period codes (e.g. 'M13').
    if not period.startswith("M"):
        return None
    month = int(period[1:])
    if not 1 <= month <= 12:
        return None
    return date(int(year), month, 1)


class BlsNormalizer(BaseNormalizer):
    def normalize(self, result: ConnectorResult) -> list[MacroPoint]:
        if result.status != "ok" or not result.raw_payload:
            return []
        points: list[MacroPoint] = []
        for series in result.raw_payload.get("Results", {}).get("series", []):
            series_id = f"BLS:{series['seriesID']}"
            for obs in series.get("data", []):
                d = _period_to_date(obs["year"], obs["period"])
                if d is None:
                    continue
                try:
                    value = float(obs["value"])
                except (TypeError, ValueError):
                    continue
                points.append(MacroPoint(series_id=series_id, date=d, value=value, source="bls"))
        return points
