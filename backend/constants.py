"""Shared constants used across the scanning/alerting/API layers.

REAL_FDA_SOURCES / IV_PLACEHOLDER_SOURCE were previously copy-pasted
identically into backend/main.py (twice), backend/scheduler.py, and
backend/signals/unified_scanner.py. That duplication is why one of those
four copies (backend/scheduler.py's run_options_scan) never got the filter
applied when the other three did — a single shared constant makes that class
of drift impossible going forward.
"""

# Sources that represent a real, confirmed FDA/catalyst event with a known
# date — as opposed to IV_PLACEHOLDER_SOURCE, which is an algorithmic guess
# (see backend/scrapers/broad_biotech.py) with no actual confirmed catalyst.
REAL_FDA_SOURCES = frozenset({
    "biopharmcatalyst", "edgar/8-K", "fda.gov", "biopharmawatch",
    "fda_multi_source", "manual", "nasdaq_earnings", "auto_discovery",
})

# scan_broad_biotech() tags tickers it flags purely from an IV-rank spike
# (no known event date) with this source and event_type "Catalyst (IV
# signal)". These are deliberately excluded from BUY-signal alerting in
# run_realtime_scan/scan_and_alert/unified_scanner — but were NOT excluded
# from run_options_scan's alerting (see backend/scheduler.py) nor from the
# win-rate/calibration stats in probability.py and learning_engine.py, which
# silently diluted those aggregates: in a full-history pull (890 archived
# events, 2026-09-28), IV-placeholder-sourced rows were 48% of the scored
# subset and averaged -2.58%/day with a 4.4% win rate, versus +1.39%/day and
# 16.7% for genuine FDA-catalyst rows — a real population, not noise, and
# one the bot itself never intended to trade on.
IV_PLACEHOLDER_SOURCE = "broad_scan/iv"
