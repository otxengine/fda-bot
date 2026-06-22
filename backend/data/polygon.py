"""
Polygon.io API client for options chains, quotes, and snapshots.
Free tier: stocks data. Options snapshots require Starter plan+.
Falls back gracefully if options endpoints are unavailable.
"""
import os
import logging
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"


class PolygonClient:
    def __init__(self):
        self.api_key = os.getenv("POLYGON_API_KEY", "")
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        if not self.api_key:
            logger.warning("POLYGON_API_KEY not set")
            return None
        try:
            p = params or {}
            p["apiKey"] = self.api_key
            url = f"{POLYGON_BASE}{endpoint}"
            response = self.session.get(url, params=p, timeout=15)
            if response.status_code == 403:
                logger.warning(f"Polygon 403 - endpoint may require higher plan: {endpoint}")
                return None
            if response.status_code == 429:
                logger.warning("Polygon rate limit hit")
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Polygon HTTP {e.response.status_code} for {endpoint}")
            return None
        except Exception as e:
            logger.error(f"Polygon request error: {e}")
            return None

    def get_snapshot(self, ticker: str) -> Optional[dict]:
        """Get current stock snapshot (price, volume, etc.)."""
        data = self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
        if not data:
            return None
        return data.get("ticker")

    def get_quote(self, ticker: str) -> Optional[dict]:
        """Get last quote for a stock."""
        data = self._get(f"/v2/last/trade/{ticker}")
        if not data:
            return None
        return data.get("results")

    def get_previous_close(self, ticker: str) -> Optional[dict]:
        """Get previous day's OHLCV data."""
        data = self._get(f"/v2/aggs/ticker/{ticker}/prev")
        if not data:
            return None
        results = data.get("results", [])
        return results[0] if results else None

    def get_options_snapshot(self, ticker: str) -> list[dict]:
        """
        Get options chain snapshot for a ticker.
        Requires Starter plan or above on Polygon.io.
        Returns list of option contract snapshots.
        """
        contracts = []
        url = f"/v3/snapshot/options/{ticker}"
        params = {"limit": 250}

        while True:
            data = self._get(url, params)
            if not data:
                break

            results = data.get("results", [])
            contracts.extend(results)

            # Pagination
            next_url = data.get("next_url")
            if not next_url or len(results) == 0:
                break

            # Extract cursor from next_url
            cursor = next_url.split("cursor=")[-1].split("&")[0] if "cursor=" in next_url else None
            if not cursor:
                break
            params = {"limit": 250, "cursor": cursor}

        return contracts

    def get_options_summary(self, ticker: str, max_pages: int = 3) -> dict:
        """
        Aggregate options data from Polygon snapshot endpoint.
        Falls back to yfinance if Polygon options aren't available.
        """
        contracts = self.get_options_snapshot(ticker)

        if not contracts:
            logger.info(f"Polygon options not available for {ticker} - using yfinance fallback")
            return self._yfinance_fallback(ticker)

        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0
        total_premium_flow = 0.0
        iv_values = []

        for contract in contracts:
            details = contract.get("details", {})
            day = contract.get("day", {})
            greeks = contract.get("greeks", {})

            contract_type = details.get("contract_type", "").lower()
            volume = day.get("volume") or 0
            oi = contract.get("open_interest") or 0
            iv = contract.get("implied_volatility")
            last_price = day.get("close") or day.get("last") or 0

            if iv:
                iv_values.append(float(iv) * 100)  # convert to percentage

            if contract_type == "call":
                total_call_volume += volume
                total_call_oi += oi
                total_premium_flow += volume * last_price * 100
            elif contract_type == "put":
                total_put_volume += volume
                total_put_oi += oi

        total_volume = total_call_volume + total_put_volume
        total_oi = total_call_oi + total_put_oi
        avg_iv = sum(iv_values) / len(iv_values) if iv_values else 0

        return {
            "call_volume": total_call_volume,
            "put_volume": total_put_volume,
            "total_volume": total_volume,
            "open_interest": total_oi,
            "implied_volatility": round(avg_iv, 2),
            "premium_flow": round(total_premium_flow, 2),
            "source": "polygon",
        }

    def _yfinance_fallback(self, ticker: str) -> dict:
        """Use yfinance options chain as fallback when Polygon options unavailable."""
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if not expirations:
                return {}

            total_call_volume = 0
            total_put_volume = 0
            total_call_oi = 0
            total_put_oi = 0
            total_premium_flow = 0.0
            iv_values = []

            for exp in expirations[:4]:  # near-term only
                try:
                    chain = stock.option_chain(exp)
                    calls = chain.calls
                    puts = chain.puts

                    total_call_volume += calls["volume"].fillna(0).sum()
                    total_put_volume += puts["volume"].fillna(0).sum()
                    total_call_oi += calls["openInterest"].fillna(0).sum()
                    total_put_oi += puts["openInterest"].fillna(0).sum()

                    # IV values
                    call_iv = calls["impliedVolatility"].dropna().tolist()
                    put_iv = puts["impliedVolatility"].dropna().tolist()
                    iv_values.extend([v * 100 for v in call_iv + put_iv if v > 0])

                    # Premium flow from calls
                    call_mid = (calls["bid"].fillna(0) + calls["ask"].fillna(0)) / 2
                    total_premium_flow += (calls["volume"].fillna(0) * call_mid * 100).sum()

                except Exception:
                    continue

            avg_iv = sum(iv_values) / len(iv_values) if iv_values else 0

            return {
                "call_volume": float(total_call_volume),
                "put_volume": float(total_put_volume),
                "total_volume": float(total_call_volume + total_put_volume),
                "open_interest": float(total_call_oi + total_put_oi),
                "implied_volatility": round(avg_iv, 2),
                "premium_flow": round(float(total_premium_flow), 2),
                "source": "yfinance_fallback",
            }
        except Exception as e:
            logger.error(f"yfinance fallback failed for {ticker}: {e}")
            return {}

    def get_options_by_expiry(self, ticker: str, max_pages: int = 3) -> dict:
        """
        Returns per-expiry options data using Polygon snapshot endpoint.
        Falls back to yfinance if Polygon options unavailable (403).
        Same structure as YFinanceClient.get_options_by_expiry().
        """
        contracts = self.get_options_snapshot(ticker)
        if not contracts:
            # Polygon options not available — delegate to yfinance
            from backend.data.yfinance_client import YFinanceClient
            return YFinanceClient().get_options_by_expiry(ticker)

        result = {}
        for contract in contracts:
            details  = contract.get("details", {})
            day      = contract.get("day", {})

            exp_str  = details.get("expiration_date")
            if not exp_str:
                continue

            ctype    = details.get("contract_type", "").lower()
            strike   = details.get("strike_price", 0)
            volume   = day.get("volume") or 0
            oi       = contract.get("open_interest") or 0
            iv_raw   = contract.get("implied_volatility") or 0
            iv_pct   = round(float(iv_raw) * 100, 1)
            last     = day.get("close") or day.get("last") or 0
            premium  = volume * last * 100 if ctype == "call" else 0

            if exp_str not in result:
                result[exp_str] = {
                    "call_volume": 0, "put_volume": 0,
                    "call_oi": 0,     "put_oi": 0,
                    "premium_flow": 0.0,
                    "avg_call_iv": 0.0,
                    "_call_iv_sum": 0.0, "_call_iv_n": 0,
                    "strikes": [],
                }

            entry = result[exp_str]
            if ctype == "call":
                entry["call_volume"] += volume
                entry["call_oi"]     += oi
                entry["premium_flow"] += premium
                if iv_pct > 0:
                    entry["_call_iv_sum"] += iv_pct
                    entry["_call_iv_n"]   += 1
            else:
                entry["put_volume"] += volume
                entry["put_oi"]     += oi

            if volume > 0 and len(entry["strikes"]) < 15:
                entry["strikes"].append({
                    "strike": float(strike),
                    "type": ctype,
                    "volume": int(volume),
                    "oi": int(oi),
                    "iv": iv_pct,
                })

        # Finalise avg_call_iv
        for exp_str, entry in result.items():
            n = entry.pop("_call_iv_n", 0)
            s = entry.pop("_call_iv_sum", 0)
            entry["avg_call_iv"] = round(s / n, 2) if n > 0 else 0.0
            entry["premium_flow"] = round(entry["premium_flow"], 2)

        return result

    def get_stock_price(self, ticker: str) -> float:
        """Get latest stock price."""
        snap = self.get_snapshot(ticker)
        if snap:
            day = snap.get("day", {})
            return float(day.get("c") or day.get("close") or 0)
        prev = self.get_previous_close(ticker)
        if prev:
            return float(prev.get("c") or 0)
        return 0.0
