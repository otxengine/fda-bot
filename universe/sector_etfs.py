"""The 11 SPDR Select Sector ETFs + the SPY benchmark, used by
analysis/sector_rotation.py. Fetched through the same yahoo_finance
connector/price_bars table as everything else — no separate connector."""

SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

BENCHMARK_SYMBOL = "SPY"

ALL_SECTOR_SYMBOLS: list[str] = list(SECTOR_ETFS.keys()) + [BENCHMARK_SYMBOL]
