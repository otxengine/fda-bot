"""Read-only query functions exposed to the chat/advisor page as Anthropic
tool-use tools. The whole point of tool-calling here (rather than just
pasting a data dump into the prompt) is that the model can only ground its
answer in what these functions actually return from the local DB — never its
own general market knowledge, and never a prediction. See plan's Goals
("Chat/advisor") and the Non-Goals rule against forecasting/false precision.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import sqlite3

from analysis import macro_regime, sector_rotation
from analysis.sector_quant_screener import build_quant_table, sector_growth_overview, stocks_in_sector
from analysis.technicals import get_price_history, latest_snapshot, with_technicals

SYSTEM_PROMPT = """You are a research assistant embedded in a personal financial-markets \
dashboard. You answer ONLY using the tools provided — they query the user's own local \
database of macro indicators, sector performance, watchlist data, and screener results. \

Rules:
- Never answer from your own general knowledge of markets, companies, or macro conditions. \
If a tool doesn't cover something, say so plainly rather than filling the gap yourself.
- Never predict future prices, give a probability of a recession, or otherwise present a \
forecast — the underlying data is descriptive/heuristic (rule-based lights and relative \
performance), not a model, and must be presented that way.
- Never give direct investment advice ("you should buy X"). Instead, lay out what the \
retrieved macro and micro data show and let the user draw their own conclusion.
- Always be explicit about which data you used (e.g. "based on the current macro regime \
and XLK's relative strength over the last 21 days...").
- When asked how current/up-to-date the data is (or whenever it's materially relevant), cite \
each indicator's own 'as_of_date' from get_macro_regime — that's the date the underlying \
economic figure was published FOR, not when it was fetched. It is normal and expected for \
this to lag today's date by weeks or months (e.g. CPI, payrolls, GDP are never same-day data) \
— say so plainly rather than implying the figures are real-time.
- get_macro_regime only covers 9 indicators across 4 categories (rates/inflation/employment/ \
growth). For anything else — consumption, production, housing, sentiment, trade, or a survey — \
call list_available_indicators to find the right series_id, then get_indicator_history on it, \
rather than saying the data doesn't exist.
- This is not a substitute for professional financial advice, and you should say so if the \
user seems to be asking you to make a decision for them.
"""


def get_macro_regime(conn: sqlite3.Connection) -> dict[str, Any]:
    regime = macro_regime.get_latest_regime(conn)
    if regime is None:
        return {"error": "No macro regime computed yet"}
    return {
        "computed_at": str(regime["computed_at"]),
        "regime_label": regime["regime_label"],
        "category_scores": regime["category_scores"],
        "coverage": regime["coverage"],
        "indicators_with_data": regime["indicators_with_data"],
        "total_indicators": regime["total_indicators"],
        # Each entry includes 'as_of_date' — the date the underlying economic
        # figure itself refers to (its publication vintage), which is USUALLY
        # older than 'computed_at' (when we fetched/scored it) since data like
        # CPI/GDP is released with a lag. Cite as_of_date when asked "how
        # current is this", not computed_at.
        "indicator_details": regime["details"],
    }


def get_sector_rotation(conn: sqlite3.Connection, lookback_days: int = 21) -> dict[str, Any]:
    """PRICE-based sector view — recent return/relative-strength/volume. For
    FUNDAMENTALS-based sector growth (analyst-estimated revenue/earnings
    growth), use get_sector_growth_ranking instead — they answer different
    questions ("which sector's price is moving" vs "which sector's
    fundamentals are growing fastest") and shouldn't be conflated."""
    df = sector_rotation.compute_sector_rotation(conn, lookback_days=lookback_days)
    if df.empty:
        return {"error": "No sector data loaded yet"}
    return {"lookback_days": lookback_days, "sectors": df.to_dict(orient="records")}


def get_sector_growth_ranking(conn: sqlite3.Connection) -> dict[str, Any]:
    """FUNDAMENTALS-based sector ranking by analyst-consensus growth (the
    S&P 500-wide sector+quant screen — same methodology as the user's own
    Excel/Zacks guide, see analysis/sector_quant_screener.py). Sector
    averages exclude extreme (likely 'base effect') outliers."""
    quant_df = build_quant_table(conn)
    if quant_df.empty or quant_df["earnings_growth_f1"].isna().all():
        return {"error": "No analyst estimate data loaded for the universe yet — the full-universe fetch takes up to 45 minutes and runs every ~3 hours."}
    overview = sector_growth_overview(quant_df)
    return {
        "note": "Averages exclude companies with >300% growth (likely a low-base distortion, not filtered out of the underlying data, just the average).",
        "sectors_ranked_by_earnings_growth": overview.to_dict(orient="records"),
    }


def get_top_stocks_in_sector(conn: sqlite3.Connection, sector: str, n: int = 10) -> dict[str, Any]:
    """Individual stocks within one sector, ranked by expected earnings
    growth (EG1), each flagged for whether it beats the sector's own average
    and whether it's a possible base-effect outlier. Call
    get_sector_growth_ranking first to see valid sector names."""
    quant_df = build_quant_table(conn)
    stocks = stocks_in_sector(quant_df, sector)
    if stocks.empty:
        return {"error": f"No companies found for sector='{sector}' — call get_sector_growth_ranking to see valid sector names."}
    cols = [
        "symbol", "name", "industry", "pe_trailing", "pe_f1", "ps_trailing",
        "sales_growth_next_year", "earnings_growth_f1", "earnings_growth_f2",
        "peg_ratio", "above_sector_avg_earnings_growth", "earnings_growth_f1_outlier",
    ]
    return {
        "sector": sector,
        "sector_avg_earnings_growth_f1": stocks.attrs.get("sector_avg_earnings_growth_f1"),
        "top_stocks": stocks[cols].head(n).to_dict(orient="records"),
    }


def get_economic_calendar(conn: sqlite3.Connection, days_ahead: int = 30) -> dict[str, Any]:
    """Upcoming scheduled economic data releases (CPI, jobs report, GDP, PPI,
    retail sales, etc.) — a free/official FRED-sourced calendar (see
    connectors/fred.py's WATCHED_RELEASE_IDS), not investment advice, just
    "what's coming up and when"."""
    import re
    from datetime import date, timedelta

    rows = conn.execute(
        "SELECT message FROM flags WHERE flag_type IN ('economic_release', 'fomc_meeting')"
    ).fetchall()
    if not rows:
        return {"error": "No economic calendar data fetched yet."}

    # The event date is embedded in the message text (e.g. "CPI scheduled for
    # 2026-10-14") — flags.created_at is fetch time, NOT the event date, so
    # sorting by created_at would give insertion order, not a real calendar
    # order. Extract the actual date and sort/window by that instead.
    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
    today = date.today()
    horizon = today + timedelta(days=days_ahead)
    dated_events: list[tuple[date, str]] = []
    for (message,) in rows:
        match = date_pattern.search(message)
        if not match:
            continue
        try:
            event_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if today - timedelta(days=7) <= event_date <= horizon:
            dated_events.append((event_date, message))
    dated_events.sort(key=lambda pair: pair[0])

    return {
        "note": "Dates are scheduled release dates, not predictions — this is purely a calendar.",
        "upcoming_and_recent_releases": [{"date": str(d), "event": msg} for d, msg in dated_events],
    }


def get_watchlist_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM watchlist").fetchall()]
    out = []
    for symbol in symbols:
        q = conn.execute(
            "SELECT price, change_pct, pe_ratio, market_cap FROM quotes_latest WHERE symbol = ?",
            [symbol],
        ).fetchone()
        df = get_price_history(conn, symbol, interval="1d")
        tech = latest_snapshot(with_technicals(df)) if not df.empty else {}
        out.append(
            {
                "symbol": symbol,
                "price": q[0] if q else None,
                "change_pct": q[1] if q else None,
                "pe_ratio": q[2] if q else None,
                "market_cap": q[3] if q else None,
                "rsi_14": tech.get("rsi_14"),
                "pct_above_sma50": tech.get("pct_above_sma50"),
            }
        )
    return {"watchlist": out}


def get_earnings_estimates(conn: sqlite3.Connection, symbol: str) -> dict[str, Any]:
    """Analyst consensus revenue/earnings for the current and next fiscal
    year, and their YoY growth rates — answers "what's X's growth rate next
    year" style questions. Watchlist symbols only (see connectors/yahoo_finance.py
    — too expensive per-symbol to fetch for the whole screener universe)."""
    rows = conn.execute(
        """
        SELECT period, metric, avg_estimate, growth_yoy, number_of_analysts, as_of
        FROM earnings_estimates
        WHERE symbol = ? AND as_of = (SELECT max(as_of) FROM earnings_estimates WHERE symbol = ?)
        ORDER BY metric, period
        """,
        [symbol, symbol],
    ).fetchall()
    if not rows:
        return {"error": f"No analyst estimates for {symbol} — only fetched for watchlist symbols, and only after at least one scheduler tick."}
    period_labels = {"0q": "this quarter", "+1q": "next quarter", "0y": "this fiscal year", "+1y": "next fiscal year"}
    return {
        "symbol": symbol,
        "as_of": str(rows[0][5]),
        "estimates": [
            {
                "period": period_labels.get(r[0], r[0]),
                "metric": r[1],
                "consensus_avg": r[2],
                "growth_yoy_pct": round(r[3] * 100, 2) if r[3] is not None else None,
                "number_of_analysts": r[4],
            }
            for r in rows
        ],
    }


def get_screener_top(conn: sqlite3.Connection, n: int = 10) -> dict[str, Any]:
    latest_run = conn.execute(
        "SELECT run_id, run_ts FROM screener_snapshots ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()
    if latest_run is None:
        return {"error": "No screener run yet"}
    run_id, run_ts = latest_run
    rows = conn.execute(
        "SELECT symbol, rank, score FROM screener_snapshots WHERE run_id = ? ORDER BY rank LIMIT ?",
        [run_id, n],
    ).fetchall()
    return {
        "run_ts": str(run_ts),
        "top": [{"symbol": r[0], "rank": r[1], "score": round(r[2], 1)} for r in rows],
    }


def get_fda_bot_performance(conn: sqlite3.Connection) -> dict[str, Any]:
    """Latest read of the user's own separate fda-bot service (an FDA/biopharma
    catalyst options-flow scanner) — its alert-outcome win rate, broken down
    by call/put ratio bucket and by composite-score bucket, plus current
    0-7 day actionable tickers. Descriptive only: report what the numbers
    say, including when a bucket breakdown is NOT monotonic (e.g. a lower
    bucket outperforming a higher one) — never smooth over that or imply the
    scoring is more reliable than the data shows.

    win_rate_to_target/avg_return_to_target (entry -> the bot's own planned
    pre-event exit) is the metric to treat as "did the signal work" — every
    alert says to exit before the FDA decision, never hold through it.
    win_rate_1d/3d and avg_return_1d/3d measure the POST-event reaction
    instead (holding through the decision), which the bot's own alerts
    explicitly recommend against — mention this distinction if asked about
    the bot's accuracy, and note n_to_target is still small/growing (added
    2026-09-28) rather than presenting it as a mature, settled number."""
    perf = conn.execute(
        "SELECT fetched_at, total_alerts_tracked, n_with_1d, win_rate_1d, win_rate_3d, "
        "avg_return_1d, avg_return_3d, n_to_target, win_rate_to_target, avg_return_to_target, "
        "upcoming_events, events_next_7d, total_signals, historical_records, last_scan_at "
        "FROM fda_bot_performance_snapshots ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if perf is None:
        return {"error": "No fda-bot data fetched yet — check the fda_bot connector is enabled and has run."}

    cp_rows = conn.execute(
        "SELECT bucket, n, win_rate, avg_change FROM fda_bot_cp_buckets "
        "WHERE fetched_at = (SELECT MAX(fetched_at) FROM fda_bot_cp_buckets)"
    ).fetchall()
    score_rows = conn.execute(
        "SELECT range, n, avg_change, median_change FROM fda_bot_score_buckets "
        "WHERE fetched_at = (SELECT MAX(fetched_at) FROM fda_bot_score_buckets)"
    ).fetchall()
    signal_rows = conn.execute(
        "SELECT ticker, company, event_type, days_until, stock_signal, composite_score, "
        "call_put_ratio, entry_price, stop_loss_price "
        "FROM fda_bot_signals WHERE stock_signal IN ('BUY','EARLY_BUY') "
        "ORDER BY composite_score DESC LIMIT 15"
    ).fetchall()

    return {
        "data_as_of": str(perf[0]),
        "overall": {
            "total_alerts_tracked": perf[1],
            "n_with_1d_outcome": perf[2],
            "win_rate_1d_pct": perf[3],
            "win_rate_3d_pct": perf[4],
            "avg_return_1d_pct": perf[5],
            "avg_return_3d_pct": perf[6],
            "n_to_target": perf[7],
            "win_rate_to_target_pct": perf[8],
            "avg_return_to_target_pct": perf[9],
        },
        "upcoming_fda_events": perf[10],
        "events_next_7d": perf[11],
        "cp_ratio_buckets": [
            {"bucket": r[0], "n": r[1], "win_rate_pct": r[2], "avg_change_pct": r[3]} for r in cp_rows
        ],
        "composite_score_buckets": [
            {"range": r[0], "n": r[1], "avg_change_pct": r[2], "median_change_pct": r[3]} for r in score_rows
        ],
        "current_buy_or_early_buy_signals": [
            {
                "ticker": r[0], "company": r[1], "event_type": r[2], "days_until": r[3],
                "stock_signal": r[4], "composite_score": r[5], "call_put_ratio": r[6],
                "entry_price": r[7], "stop_loss_price": r[8],
            }
            for r in signal_rows
        ],
    }


def get_indicator_history(conn: sqlite3.Connection, series_id: str, lookback_days: int = 365) -> dict[str, Any]:
    series = macro_regime.get_series(conn, series_id)
    if series.empty:
        return {"error": f"No data for series_id={series_id}. Call list_available_indicators first if unsure of the exact series_id."}
    cutoff = series.index[-1] - __import__("pandas").Timedelta(days=lookback_days)
    windowed = series[series.index >= cutoff]
    return {
        "series_id": series_id,
        "points": [{"date": str(d.date()), "value": v} for d, v in windowed.items()],
    }


def list_available_indicators(conn: sqlite3.Connection) -> dict[str, Any]:
    """Catalog of every macro indicator this app tracks — both the 9 in the
    regime score and the broader supplementary set (retail sales, industrial
    production, housing, sentiment, PPI, PCE inflation, trade balance,
    regional manufacturing surveys) — so a question outside the regime's 4
    categories doesn't dead-end. Call this first when unsure which series_id
    covers a topic, then pass it to get_indicator_history."""
    from analysis.additional_indicators import load_additional_indicators_config
    from analysis.macro_regime import load_thresholds

    regime_indicators = [
        {"series_id": ind["series_id"], "display_name": ind["display_name"], "part_of_regime_score": True}
        for cat in load_thresholds()["categories"].values()
        for ind in cat["indicators"].values()
    ]
    additional = [
        {"series_id": ind["series_id"], "display_name": ind["display_name"], "part_of_regime_score": False,
         "category": ind["category"]}
        for ind in load_additional_indicators_config()
    ]
    return {"indicators": regime_indicators + additional}


# --- Anthropic tool-use schema + dispatcher -------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_macro_regime",
        "description": "Latest computed macro regime label (Expansion/Late-cycle/Slowdown/"
        "Contraction risk), per-category scores, and every indicator's raw value + light "
        "that fed into it. This is a rule-based heuristic, not a forecast.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sector_rotation",
        "description": "Per-sector-ETF return, relative strength vs SPY, and a volume-based "
        "'flow' proxy over a lookback window. Descriptive only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lookback_days": {"type": "integer", "description": "Lookback window in days (default 21)"}
            },
        },
    },
    {
        "name": "get_economic_calendar",
        "description": "Upcoming (and recent) scheduled economic data releases — CPI, jobs "
        "report, GDP, PPI, retail sales, industrial production, housing starts, consumer "
        "sentiment, trade balance, regional manufacturing surveys, and FOMC meetings. A "
        "calendar of WHEN data is due, not the data itself or a prediction.",
        "input_schema": {
            "type": "object",
            "properties": {"days_ahead": {"type": "integer", "description": "default 30"}},
        },
    },
    {
        "name": "get_sector_growth_ranking",
        "description": "FUNDAMENTALS-based sector ranking (not price) — average analyst-consensus "
        "revenue/earnings growth per sector across the full S&P 500, per the same sector+quant "
        "methodology as the user's own Excel/Zacks guide. Use for 'which sector is expected to "
        "grow fastest' style questions; use get_sector_rotation instead for price-momentum questions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_top_stocks_in_sector",
        "description": "Individual S&P 500 stocks within one sector, ranked by expected earnings "
        "growth, each flagged for whether it beats the sector's own average and whether it looks "
        "like a base-effect outlier. Call get_sector_growth_ranking first for valid sector names.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {"type": "string"},
                "n": {"type": "integer", "description": "How many top rows (default 10)"},
            },
            "required": ["sector"],
        },
    },
    {
        "name": "get_watchlist_summary",
        "description": "Current price, change %, P/E, market cap, RSI-14 and %-above-50-day-SMA "
        "for every symbol on the user's watchlist.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_screener_top",
        "description": "Top-N ranked symbols from the most recent screener run, with their "
        "composite percentile-rank score.",
        "input_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "description": "How many top rows (default 10)"}},
        },
    },
    {
        "name": "get_earnings_estimates",
        "description": "Analyst consensus revenue and earnings (EPS) for a watchlist symbol, for "
        "this quarter/next quarter/this fiscal year/next fiscal year, with each period's YoY "
        "growth rate. Use for 'what's X's revenue/earnings growth next year' style questions. "
        "Only available for watchlist symbols.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "e.g. 'AAPL'"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_indicator_history",
        "description": "Raw time series for one macro indicator (e.g. 'FRED:UNRATE', "
        "'FRED:T10Y2Y') over a lookback window, for trend context. Call "
        "list_available_indicators first if you're not sure of the exact series_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "series_id": {"type": "string"},
                "lookback_days": {"type": "integer", "description": "default 365"},
            },
            "required": ["series_id"],
        },
    },
    {
        "name": "get_fda_bot_performance",
        "description": "Latest read of the user's own separate fda-bot service (an FDA/biopharma "
        "catalyst options-flow scanner deployed on Render, github.com/otxengine/fda-bot) — its "
        "real alert-outcome win rate overall (prefer win_rate_to_target/avg_return_to_target: entry "
        "to the bot's own planned pre-event exit, the trade its alerts actually recommend, over "
        "win_rate_1d/3d which measure the post-event reaction the bot tells users NOT to hold "
        "through), broken down by call/put-ratio bucket and by composite-score bucket, plus current "
        "0-7 day BUY/EARLY_BUY tickers. Use for questions about that bot's signals, its track "
        "record, or whether its scoring is well-calibrated.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_available_indicators",
        "description": "Catalog of every macro indicator this app tracks, both the ones in the "
        "regime score and broader supplementary ones (retail sales, industrial production, "
        "housing starts, consumer sentiment, PPI, PCE inflation, trade balance, regional "
        "manufacturing surveys). Call this when a question is about a topic outside the regime's "
        "4 categories (rates/inflation/employment/growth) to find the right series_id, then use "
        "get_indicator_history on it.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "get_macro_regime": get_macro_regime,
    "get_economic_calendar": get_economic_calendar,
    "get_sector_rotation": get_sector_rotation,
    "get_sector_growth_ranking": get_sector_growth_ranking,
    "get_top_stocks_in_sector": get_top_stocks_in_sector,
    "list_available_indicators": list_available_indicators,
    "get_watchlist_summary": get_watchlist_summary,
    "get_screener_top": get_screener_top,
    "get_earnings_estimates": get_earnings_estimates,
    "get_indicator_history": get_indicator_history,
    "get_fda_bot_performance": get_fda_bot_performance,
}


def call_tool(conn: sqlite3.Connection, name: str, tool_input: dict[str, Any]) -> str:
    fn = _DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        result = fn(conn, **tool_input)
    except Exception as exc:  # noqa: BLE001 — a tool error should be a message back to the model, not a crash
        result = {"error": str(exc)}
    return json.dumps(result, default=str)


# --- Chat loop --------------------------------------------------------------

CHAT_MODEL = "claude-sonnet-5"
MAX_TOOL_ROUNDS = 6  # hard cap so a confused tool-use loop can't run away


def run_chat_turn(
    conn: sqlite3.Connection, api_key: str, message_history: list[dict[str, Any]]
) -> str:
    """message_history is the running Anthropic-format conversation (list of
    {"role": "user"|"assistant", "content": ...}), already including the
    user's newest message. Returns the assistant's final text reply; the
    caller (dashboard page) is responsible for appending it to history."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    messages = list(message_history)

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result_text = call_tool(conn, block.name, block.input)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
            )
        messages.append({"role": "user", "content": tool_results})

    return "I wasn't able to settle on an answer within the tool-call budget for this turn — try narrowing the question."
