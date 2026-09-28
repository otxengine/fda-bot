from __future__ import annotations

import math

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import MacroPoint


class FredNormalizer(BaseNormalizer):
    def normalize(self, result: ConnectorResult) -> list[MacroPoint]:
        if result.status != "ok" or not result.raw_payload:
            return []
        points: list[MacroPoint] = []
        for mnemonic, series in result.raw_payload.items():
            series_id = f"FRED:{mnemonic}"
            for dt, value in series.items():
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue
                points.append(
                    MacroPoint(
                        series_id=series_id,
                        date=dt.date() if hasattr(dt, "date") else dt,
                        value=float(value),
                        source="fred",
                    )
                )
        return points
