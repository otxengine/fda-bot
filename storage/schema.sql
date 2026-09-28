-- finresearch DuckDB schema
-- Written once against a fresh DB file by storage/db.py::init_schema().
-- Keep this file idempotent (CREATE TABLE IF NOT EXISTS) so it can double as a migration base.

-- Canonical instrument reference (equities, ETFs, indices)
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    asset_type TEXT,       -- 'equity','etf','index'
    exchange TEXT,
    sector TEXT,
    industry TEXT,
    currency TEXT,
    is_watchlist BOOLEAN DEFAULT FALSE,
    added_at TIMESTAMP
);

-- Raw price/quote time series (OHLCV, intraday + daily)
CREATE TABLE IF NOT EXISTS price_bars (
    symbol TEXT,
    ts TIMESTAMP,           -- bar timestamp (UTC)
    interval TEXT,          -- '1d','1h','15m'
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume BIGINT,
    source TEXT,             -- 'yfinance'
    fetched_at TIMESTAMP,
    PRIMARY KEY (symbol, ts, interval)
);

-- Latest quote snapshot (cheap "current state" table, upserted)
CREATE TABLE IF NOT EXISTS quotes_latest (
    symbol TEXT PRIMARY KEY,
    price DOUBLE,
    change_pct DOUBLE,
    volume BIGINT,
    market_cap DOUBLE,
    pe_ratio DOUBLE,
    pb_ratio DOUBLE,
    dividend_yield DOUBLE,
    fifty_two_wk_high DOUBLE,
    fifty_two_wk_low DOUBLE,
    as_of TIMESTAMP,
    source TEXT
);

-- Fundamentals snapshot history (append, not just latest — for trend)
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol TEXT,
    as_of TIMESTAMP,
    pe_ratio DOUBLE,
    forward_pe DOUBLE,
    peg_ratio DOUBLE,
    eps DOUBLE,
    revenue_ttm DOUBLE,
    revenue_growth_yoy DOUBLE,
    profit_margin DOUBLE,
    debt_to_equity DOUBLE,
    free_cash_flow DOUBLE,
    roe DOUBLE,
    source TEXT,
    PRIMARY KEY (symbol, as_of)
);

-- Analyst consensus estimates (current/next quarter+year, growth rate) — one
-- row per symbol/period/metric/fetch, appended (not upserted-to-latest) so
-- estimate revisions over time stay visible. Watchlist symbols only (see
-- connectors/yahoo_finance.py — too expensive per-symbol to fetch for the
-- full ~500-symbol screener universe too).
CREATE TABLE IF NOT EXISTS earnings_estimates (
    symbol TEXT,
    as_of TIMESTAMP,
    period TEXT,             -- '0q','+1q','0y','+1y'
    metric TEXT,             -- 'earnings' or 'revenue'
    avg_estimate DOUBLE,
    growth_yoy DOUBLE,
    number_of_analysts INTEGER,
    source TEXT,
    PRIMARY KEY (symbol, as_of, period, metric)
);

-- Macro indicator series (generic, EAV-style — see plan's rationale: small series count, read-mostly)
CREATE TABLE IF NOT EXISTS macro_series (
    series_id TEXT,          -- e.g. 'FRED:FEDFUNDS', 'BLS:CUUR0000SA0', 'WB:NY.GDP.MKTP.KD.ZG'
    date DATE,
    value DOUBLE,
    source TEXT,              -- 'fred','bls','bea','worldbank','federalreserve'
    fetched_at TIMESTAMP,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS macro_series_meta (
    series_id TEXT PRIMARY KEY,
    display_name TEXT,
    unit TEXT,
    frequency TEXT,
    source TEXT,
    category TEXT             -- 'rates','inflation','employment','growth','global'
);

-- Screener snapshots (point-in-time ranked results, for "what changed since last scan")
CREATE TABLE IF NOT EXISTS screener_snapshots (
    run_id TEXT,             -- uuid per scan run
    run_ts TIMESTAMP,
    symbol TEXT,
    rank INTEGER,
    score DOUBLE,
    criteria_json TEXT,       -- JSON blob of filter values that produced this row
    PRIMARY KEY (run_id, symbol)
);

-- Watchlist metadata (user-curated)
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    notes TEXT,
    target_price DOUBLE,
    tags TEXT,                -- comma-separated or JSON array
    added_at TIMESTAMP
);

-- News / articles
CREATE TABLE IF NOT EXISTS news_articles (
    article_id TEXT PRIMARY KEY,   -- hash of URL
    symbol TEXT,                    -- nullable, may be macro-only news
    headline TEXT,
    url TEXT,
    published_at TIMESTAMP,
    source TEXT,                    -- 'yfinance','benzinga','investing'
    sentiment_score DOUBLE,         -- nullable until scored
    fetched_at TIMESTAMP
);

-- Flags/badges surfaced in dashboard (replaces push alerts)
CREATE TABLE IF NOT EXISTS flags (
    flag_id TEXT PRIMARY KEY,
    flag_type TEXT,            -- 'macro_regime_change','screener_new_hit','earnings_soon','52wk_high'
    symbol TEXT,                -- nullable for macro-level flags
    severity TEXT,               -- 'info','watch','warn'
    message TEXT,
    created_at TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE
);

-- Rule-based macro regime label history
CREATE TABLE IF NOT EXISTS macro_regime_history (
    computed_at TIMESTAMP,
    regime_label TEXT,        -- 'Expansion','Late-cycle','Slowdown','Contraction risk'
    rates_score DOUBLE,
    inflation_score DOUBLE,
    employment_score DOUBLE,
    growth_score DOUBLE,
    details_json TEXT,        -- per-indicator light + value, for the "why" expander in the UI
    PRIMARY KEY (computed_at)
);

-- Connector run log (observability / debugging fetch health)
CREATE TABLE IF NOT EXISTS connector_runs (
    connector_name TEXT,
    run_ts TIMESTAMP,
    status TEXT,               -- 'ok','error','partial'
    rows_ingested INTEGER,
    error_message TEXT,
    duration_ms INTEGER,
    PRIMARY KEY (connector_name, run_ts)
);

-- Chat/advisor conversations — multiple, named, resumable, like a normal AI
-- chat app's history sidebar (dashboard/pages/5_Chat.py).
CREATE TABLE IF NOT EXISTS chat_conversations (
    id TEXT PRIMARY KEY,       -- uuid
    title TEXT,                -- auto-derived from the first message
    created_at TIMESTAMP,
    updated_at TIMESTAMP       -- bumped on every message, for sidebar ordering
);

-- Only ever stores the clean final user/assistant text turns — never the
-- intermediate tool_use/tool_result blocks a turn generates internally (see
-- analysis/chat_tools.py) — so resuming a conversation replays as plain text
-- context, never stale tool output presented as if it were still current.
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    role TEXT,                 -- 'user' or 'assistant'
    content TEXT,
    created_at TIMESTAMP
);

-- FDA-Bot integration (the user's own separate tool — github.com/otxengine/
-- fda-bot, an FDA/biopharma catalyst options-flow scanner deployed on
-- Render). Pulled read-only from its public /api/* HTTP surface (see
-- connectors/fda_bot.py) — finresearch never writes back to it.

-- Point-in-time read of the bot's own alert-outcome performance
-- (/api/performance) — appended over time so win-rate trend is visible here,
-- not just the latest snapshot.
CREATE TABLE IF NOT EXISTS fda_bot_performance_snapshots (
    fetched_at TIMESTAMP PRIMARY KEY,
    total_alerts_tracked INTEGER,
    n_with_1d INTEGER,
    win_rate_1d DOUBLE,
    win_rate_3d DOUBLE,
    avg_return_1d DOUBLE,
    avg_return_3d DOUBLE,
    -- entry -> planned pre-event exit (fda-bot's own target_date) — the
    -- trade its alerts actually recommend, added to fda-bot 2026-09-28.
    -- See dashboard/pages/7_FDA_Bot.py's Diagnostics tab for why this is
    -- preferred over win_rate_1d/3d and avg_return_1d/3d above.
    n_to_target INTEGER,
    win_rate_to_target DOUBLE,
    avg_return_to_target DOUBLE,
    upcoming_events INTEGER,
    events_next_7d INTEGER,
    total_signals INTEGER,
    historical_records INTEGER,
    last_scan_at TIMESTAMP
);

-- Win-rate broken down by call/put ratio bucket (/api/performance's
-- cp_buckets — the bot's own documented "strongest predictor"). Appended per
-- fetch so a bucket's win rate can be tracked over time, not just latest.
CREATE TABLE IF NOT EXISTS fda_bot_cp_buckets (
    fetched_at TIMESTAMP,
    bucket TEXT,               -- '<1.0','1.0-2.0','2.0-3.5','3.5-5.0','5.0-8.0','>8.0'
    n INTEGER,
    win_rate DOUBLE,
    avg_change DOUBLE,
    PRIMARY KEY (fetched_at, bucket)
);

-- Win-rate broken down by composite-score bucket (/api/calibration).
CREATE TABLE IF NOT EXISTS fda_bot_score_buckets (
    fetched_at TIMESTAMP,
    range TEXT,                -- '80-100','65-80','50-65','35-50','0-35'
    n INTEGER,
    p_up5 DOUBLE,
    p_up10 DOUBLE,
    p_down5 DOUBLE,
    p_down10 DOUBLE,
    avg_change DOUBLE,
    median_change DOUBLE,
    PRIMARY KEY (fetched_at, range)
);

-- Latest 0-7 day actionable signal per ticker (/api/stock-signals) — fully
-- replaced on every fetch (see writers.replace_fda_bot_signals) so a ticker
-- whose event has passed or dropped out of the window doesn't linger stale.
CREATE TABLE IF NOT EXISTS fda_bot_signals (
    ticker TEXT PRIMARY KEY,
    company TEXT,
    event_type TEXT,
    event_date DATE,
    days_until INTEGER,
    stock_signal TEXT,          -- BUY / WATCH / AVOID / EARLY_BUY
    stock_signal_reason TEXT,
    entry_price DOUBLE,
    stop_loss_price DOUBLE,
    target_date TEXT,
    composite_score DOUBLE,
    expected_move_pct DOUBLE,
    entry_window TEXT,
    call_put_ratio DOUBLE,
    iv_rank DOUBLE,
    premium_flow DOUBLE,
    liquidity_warning BOOLEAN,
    iv_crush_warning BOOLEAN,
    -- Biotech sector strength / macro risk read, added to fda-bot
    -- 2026-09-28 — see storage/db.py's _migrate_fda_bot_target_columns.
    sector_momentum TEXT,
    macro_risk_flag TEXT,
    fetched_at TIMESTAMP
);
