from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import EarningsEstimate, FundamentalsSnapshot, NewsArticle, PriceBar, Quote


def _num(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class YahooFinanceNormalizer(BaseNormalizer):
    """Returns a flat, mixed-type list (PriceBar | Quote | FundamentalsSnapshot |
    NewsArticle). Callers dispatch by isinstance() when writing — see
    ingestion/dispatch.py. Kept flat rather than a grouped structure so this
    normalizer's interface matches every other source's `list[Any]` contract."""

    def normalize(self, result: ConnectorResult) -> list[object]:
        if result.status != "ok" or not result.raw_payload:
            return []
        records: list[object] = []
        now = datetime.now(timezone.utc)

        for symbol, data in result.raw_payload.items():
            info = data.get("info") or {}
            news = data.get("news") or []
            histories = {
                "1d": data.get("history_daily"),
                "1h": data.get("history_intraday"),
            }

            if info:
                price = _num(info, "currentPrice", "regularMarketPrice")
                if price is not None:
                    records.append(
                        Quote(
                            symbol=symbol,
                            price=price,
                            change_pct=_num(info, "regularMarketChangePercent"),
                            volume=int(_num(info, "regularMarketVolume") or 0) or None,
                            market_cap=_num(info, "marketCap"),
                            pe_ratio=_num(info, "trailingPE"),
                            pb_ratio=_num(info, "priceToBook"),
                            dividend_yield=_num(info, "dividendYield"),
                            fifty_two_wk_high=_num(info, "fiftyTwoWeekHigh"),
                            fifty_two_wk_low=_num(info, "fiftyTwoWeekLow"),
                            as_of=now,
                            source="yfinance",
                        )
                    )
                records.append(
                    FundamentalsSnapshot(
                        symbol=symbol,
                        as_of=now,
                        pe_ratio=_num(info, "trailingPE"),
                        forward_pe=_num(info, "forwardPE"),
                        peg_ratio=_num(info, "pegRatio", "trailingPegRatio"),
                        eps=_num(info, "trailingEps"),
                        revenue_ttm=_num(info, "totalRevenue"),
                        revenue_growth_yoy=_num(info, "revenueGrowth"),
                        profit_margin=_num(info, "profitMargins"),
                        debt_to_equity=_num(info, "debtToEquity"),
                        free_cash_flow=_num(info, "freeCashflow"),
                        roe=_num(info, "returnOnEquity"),
                        source="yfinance",
                    )
                )

            for metric, key in (("earnings", "earnings_estimate"), ("revenue", "revenue_estimate")):
                table = data.get(key)
                if table is None or table.empty:
                    continue
                for period, row in table.iterrows():
                    row_dict = row.to_dict()
                    avg_estimate = _num(row_dict, "avg")
                    if avg_estimate is None:
                        continue  # no consensus estimate for this period - nothing worth storing
                    analysts = _num(row_dict, "numberOfAnalysts")
                    records.append(
                        EarningsEstimate(
                            symbol=symbol,
                            as_of=now,
                            period=str(period),
                            metric=metric,
                            avg_estimate=avg_estimate,
                            growth_yoy=_num(row_dict, "growth"),
                            number_of_analysts=int(analysts) if analysts is not None else None,
                            source="yfinance",
                        )
                    )

            for interval, history in histories.items():
                if history is None or history.empty:
                    continue
                for ts, row in history.iterrows():
                    if any(
                        math.isnan(v)
                        for v in (row.get("Open"), row.get("High"), row.get("Low"), row.get("Close"))
                        if v is not None
                    ):
                        continue
                    # yfinance returns exchange-local tz-aware timestamps (e.g.
                    # America/New_York); normalize to UTC before storing so a
                    # 2-year daily history spanning EST/EDT DST transitions
                    # doesn't end up with mixed UTC offsets in the stored ISO
                    # strings — pandas 2.x's to_datetime() refuses to parse
                    # that mix on read (found via live end-to-end testing:
                    # "ValueError: Mixed timezones detected").
                    py_ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                    if py_ts.tzinfo is not None:
                        py_ts = py_ts.astimezone(timezone.utc)
                    records.append(
                        PriceBar(
                            symbol=symbol,
                            ts=py_ts,
                            interval=interval,
                            open=float(row["Open"]),
                            high=float(row["High"]),
                            low=float(row["Low"]),
                            close=float(row["Close"]),
                            volume=int(row.get("Volume") or 0),
                            source="yfinance",
                        )
                    )

            for item in news:
                # yfinance news shape has shifted across versions; handle both
                # a flat dict and a {'content': {...}} nested shape defensively.
                content = item.get("content", item) if isinstance(item, dict) else {}
                url = (
                    content.get("link")
                    or content.get("canonicalUrl", {}).get("url")
                    or item.get("link")
                )
                title = content.get("title") or item.get("title")
                if not url or not title:
                    continue
                pub_ts = item.get("providerPublishTime")
                published_at = (
                    datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else now
                )
                article_id = hashlib.sha256(url.encode()).hexdigest()
                records.append(
                    NewsArticle(
                        article_id=article_id,
                        symbol=symbol,
                        headline=title,
                        url=url,
                        published_at=published_at,
                        source="yfinance",
                    )
                )

        return records
