from __future__ import annotations

from datetime import date as date_cls

from connectors.base import BaseNormalizer, ConnectorResult
from connectors.fred import WATCHED_RELEASE_IDS
from ingestion.models import EconomicRelease


class FredCalendarNormalizer(BaseNormalizer):
    def normalize(self, result: ConnectorResult) -> list[EconomicRelease]:
        if result.status != "ok" or not result.raw_payload:
            return []
        releases: list[EconomicRelease] = []
        for entry in result.raw_payload.get("release_dates", []):
            release_id = entry.get("release_id")
            if release_id not in WATCHED_RELEASE_IDS:
                continue  # not one of our curated indicators — see WATCHED_RELEASE_IDS
            try:
                release_date = date_cls.fromisoformat(entry["date"])
            except (KeyError, ValueError):
                continue
            releases.append(
                EconomicRelease(
                    release_id=release_id,
                    release_name=WATCHED_RELEASE_IDS[release_id],
                    release_date=release_date,
                )
            )
        return releases
