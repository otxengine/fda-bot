"""
Tradier API client for options chains, quotes, and expirations.
Free tier provides 15-minute delayed data.
"""
import os
import logging
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TRADIER_BASE_URL = "https://sandbox.tradier.com/v1"
TRADIER_PROD_URL = "https://api.tradier.com/v1"


class TradierClient:
    def __init__(self):
        self.token = os.getenv("TRADIER_TOKEN", "")
        # Use sandbox for free/paper accounts, prod for brokerage accounts
        self.base_url = os.getenv("TRADIER_BASE_URL", TRADIER_PROD_URL)
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        if not self.token:
            logger.warning("TRADIER_TOKEN not set - skipping API call")
            return None
        try:
            url = f"{self.base_url}/{endpoint}"
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Tradier HTTP error {e.response.status_code} for {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"Tradier request error for {endpoint}: {e}")
            return None

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Get current stock quote."""
        data = self._get("markets/quotes", params={"symbols": symbol, "greeks": "false"})
        if not data:
            return None
        quotes = data.get("quotes", {}).get("quote")
        if isinstance(quotes, list):
            return quotes[0] if quotes else None
        return quotes

    def get_expirations(self, symbol: str) -> list[str]:
        """Get available options expiration dates for a symbol."""
        data = self._get("markets/options/expirations", params={"symbol": symbol, "includeAllRoots": "true"})
        if not data:
            return []
        expirations = data.get("expirations", {})
        if not expirations:
            return []
        dates = expirations.get("date", [])
        if isinstance(dates, str):
            return [dates]
        return dates or []

    def get_options_chain(self, symbol: str, expiration: str) -> list[dict]:
        """Get full options chain for a symbol and expiration date."""
        data = self._get(
            "markets/options/chains",
            params={"symbol": symbol, "expiration": expiration, "greeks": "true"}
        )
        if not data:
            return []
        options = data.get("options", {})
        if not options:
            return []
        chain = options.get("option", [])
        if isinstance(chain, dict):
            return [chain]
        return chain or []

    def get_options_summary(self, symbol: str, max_expirations: int = 4) -> dict:
        """
        Aggregate options data across near-term expirations.
        Returns summary metrics used for signal calculation.
        """
        expirations = self.get_expirations(symbol)
        if not expirations:
            return {}

        # Focus on near-term expirations (most relevant for event plays)
        near_expirations = expirations[:max_expirations]

        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0
        total_premium_flow = 0.0
        iv_values = []

        for exp in near_expirations:
            chain = self.get_options_chain(symbol, exp)
            for option in chain:
                volume = option.get("volume") or 0
                oi = option.get("open_interest") or 0
                ask = option.get("ask") or 0
                bid = option.get("bid") or 0
                mid = (ask + bid) / 2
                iv = option.get("greeks", {}).get("mid_iv") if isinstance(option.get("greeks"), dict) else None
                if iv:
                    iv_values.append(iv)

                if option.get("option_type") == "call":
                    total_call_volume += volume
                    total_call_oi += oi
                    total_premium_flow += volume * mid * 100  # 1 contract = 100 shares
                else:
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
            "implied_volatility": round(avg_iv * 100, 2),  # as percentage
            "premium_flow": round(total_premium_flow, 2),
        }
