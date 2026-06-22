"""
yfinance client for stock price history, market cap, and IV history.
Used to calculate IV Rank (52-week range).
"""
import logging
from typing import Optional
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceClient:
    def get_stock_info(self, ticker: str) -> dict:
        """Get stock price, market cap, and basic info."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return {
                "price": info.get("regularMarketPrice") or info.get("currentPrice") or 0,
                "market_cap": info.get("marketCap") or 0,
                "company_name": info.get("longName") or info.get("shortName") or ticker,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
            }
        except Exception as e:
            logger.error(f"yfinance info error for {ticker}: {e}")
            return {"price": 0, "market_cap": 0, "company_name": ticker}

    def get_iv_history(self, ticker: str) -> dict:
        """
        Estimate IV rank using historical price volatility as proxy.
        True IV history requires options data; we use realized vol as approximation.
        Returns iv_min, iv_max, iv_current (all as percentages).
        """
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y", interval="1d")
            if hist.empty or len(hist) < 20:
                return {"iv_min": 0, "iv_max": 100, "iv_current": 50}

            # Calculate rolling 30-day realized volatility (annualized)
            returns = hist["Close"].pct_change().dropna()
            rolling_vol = returns.rolling(window=21).std() * (252 ** 0.5) * 100  # annualized %

            iv_min = rolling_vol.min()
            iv_max = rolling_vol.max()
            iv_current = rolling_vol.iloc[-1] if not rolling_vol.empty else 50

            return {
                "iv_min": round(float(iv_min), 2),
                "iv_max": round(float(iv_max), 2),
                "iv_current": round(float(iv_current), 2),
            }
        except Exception as e:
            logger.error(f"yfinance IV history error for {ticker}: {e}")
            return {"iv_min": 0, "iv_max": 100, "iv_current": 50}

    def validate_ticker(self, ticker: str) -> bool:
        """Check if ticker exists and has sufficient market cap (>$50M)."""
        try:
            info = self.get_stock_info(ticker)
            market_cap = info.get("market_cap", 0)
            return market_cap > 50_000_000
        except Exception:
            return False

    def get_price(self, ticker: str) -> float:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
        except Exception:
            return 0.0

    def get_earnings_date(self, ticker: str):
        """Return next earnings date as a date object, or None if unavailable."""
        try:
            stock = yf.Ticker(ticker)
            cal = stock.calendar
            if cal is None:
                return None
            # calendar can be a dict or DataFrame depending on yfinance version
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed is None:
                    return None
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)[0]
                if hasattr(ed, "date"):
                    return ed.date()
                return None
            # DataFrame form
            if hasattr(cal, "loc"):
                try:
                    ed = cal.loc["Earnings Date"].iloc[0]
                    if hasattr(ed, "date"):
                        return ed.date()
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.debug(f"get_earnings_date error for {ticker}: {e}")
            return None

    def get_options_by_expiry(self, ticker: str, max_expirations: int = 8) -> dict:
        """
        Returns per-expiry options data for use in ExpirationAnalyzer.
        {
            "2026-06-21": {
                "call_volume": int,
                "put_volume": int,
                "call_oi": int,
                "put_oi": int,
                "premium_flow": float,
                "avg_call_iv": float,   # % annualized
                "strikes": [
                    {"strike": float, "type": "call"|"put",
                     "volume": int, "oi": int, "iv": float}
                ]
            }
        }
        """
        result = {}
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if not expirations:
                return {}

            current_price = self.get_price(ticker)

            for exp in expirations[:max_expirations]:
                try:
                    chain = stock.option_chain(exp)
                    calls = chain.calls
                    puts  = chain.puts

                    call_vol = int(calls["volume"].fillna(0).sum())
                    put_vol  = int(puts["volume"].fillna(0).sum())
                    call_oi  = int(calls["openInterest"].fillna(0).sum())
                    put_oi   = int(puts["openInterest"].fillna(0).sum())

                    # IV (yfinance returns as decimal, e.g. 2.5 = 250%)
                    call_ivs = calls["impliedVolatility"].dropna()
                    avg_call_iv = float(call_ivs.mean() * 100) if len(call_ivs) > 0 else 0

                    # Premium flow from calls
                    call_mid = (calls["bid"].fillna(0) + calls["ask"].fillna(0)) / 2
                    premium_flow = float((calls["volume"].fillna(0) * call_mid * 100).sum())

                    # Per-strike breakdown (top 10 by volume for efficiency)
                    strikes = []
                    for _, row in calls.nlargest(10, "volume").iterrows():
                        if row.get("volume", 0) > 0:
                            strikes.append({
                                "strike": float(row["strike"]),
                                "type": "call",
                                "volume": int(row.get("volume", 0) or 0),
                                "oi": int(row.get("openInterest", 0) or 0),
                                "iv": round(float(row.get("impliedVolatility", 0) or 0) * 100, 1),
                            })
                    for _, row in puts.nlargest(5, "volume").iterrows():
                        if row.get("volume", 0) > 0:
                            strikes.append({
                                "strike": float(row["strike"]),
                                "type": "put",
                                "volume": int(row.get("volume", 0) or 0),
                                "oi": int(row.get("openInterest", 0) or 0),
                                "iv": round(float(row.get("impliedVolatility", 0) or 0) * 100, 1),
                            })

                    result[exp] = {
                        "call_volume": call_vol,
                        "put_volume":  put_vol,
                        "call_oi":     call_oi,
                        "put_oi":      put_oi,
                        "premium_flow": round(premium_flow, 2),
                        "avg_call_iv":  round(avg_call_iv, 2),
                        "strikes":      strikes,
                    }

                except Exception as inner_e:
                    logger.debug(f"yfinance expiry {exp} for {ticker}: {inner_e}")
                    continue

        except Exception as e:
            logger.error(f"yfinance get_options_by_expiry error {ticker}: {e}")

        return result
