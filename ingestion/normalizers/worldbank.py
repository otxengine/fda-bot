from __future__ import annotations

import re
from datetime import date

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import MacroPoint

_YEAR_RE = re.compile(r"(\d{4})")


class WorldBankNormalizer(BaseNormalizer):
    def normalize(self, result: ConnectorResult) -> list[MacroPoint]:
        if result.status != "ok" or not result.raw_payload:
            return []
        points: list[MacroPoint] = []
        for rec in result.raw_payload:
            value = rec.get("value")
            if value is None:
                continue
            m = _YEAR_RE.search(str(rec.get("time", "")))
            if not m:
                continue
            series_id = f"WB:{rec['series']}:{rec['economy']}"
            points.append(
                MacroPoint(
                    series_id=series_id,
                    date=date(int(m.group(1)), 1, 1),
                    value=float(value),
                    source="worldbank",
                )
            )
        return points
