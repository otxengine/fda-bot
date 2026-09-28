# Fin Research

Personal, local-first financial markets research & analysis system. Pulls from
official macro/market data sources, analyzes them, and shows a synthesized
macro + micro picture on a local web dashboard. **Never places trades.** Pure
research/decision-support.

See `~/.claude/plans/structured-honking-peacock.md` for the full design plan
(architecture, data sources, ADRs, phased build plan).

## Setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
```

Then fill in `.env` with your own API keys:

- `FRED_API_KEY` — free, from https://fred.stlouisfed.org/docs/api/api_key.html
- `BLS_API_KEY` — free, from https://www.bls.gov/developers/
- `BEA_API_KEY` — free, from https://apps.bea.gov/API/signup/
- `ANTHROPIC_API_KEY` — optional, only needed for the Chat page. From
  console.anthropic.com — **not** the same as a claude.ai Pro/Max chat
  subscription; this is separate, usage-based API billing.

World Bank and the Federal Reserve FOMC calendar need no key. Yahoo Finance
(`yfinance`) needs no key either but is an unofficial library — see the
plan's Legal/ToS Risk Summary.

## Running

```
run.bat
```

This starts two processes (see the plan's ADR-3 for why they're separate):

1. **Scheduler** (`python -m scheduler.main`) — fetches data on an interval
   and writes to the local SQLite database. Opens in its own console window
   so you can watch its logs.
2. **Dashboard** (`streamlit run dashboard/app.py`) — reads the database
   read-only and renders the UI. Opens in your browser automatically.

Close either window (or Ctrl+C) to stop that process. Data only updates
while the scheduler is running.

First run does an immediate catch-up fetch of every enabled connector before
settling into its normal interval schedule, so the dashboard isn't staring
at empty tables.

## Running it always-on (catch a report the same day it's released)

By default (above), data only updates while `run.bat` happens to be running —
fine for occasional checks, but it means a report published while your
computer was off/idle won't show up until you next launch it. To have the
scheduler poll continuously in the background instead (hourly for FRED/BLS/
BEA/Federal Reserve, so any new release is picked up within the hour,
whatever time of day it lands):

```
powershell -ExecutionPolicy Bypass -File install_background_task.ps1
```

**This needs to be run from an elevated ("Run as Administrator") PowerShell**
— Task Scheduler registration requires it, and this session couldn't do it
non-interactively. Right-click PowerShell → "Run as administrator", `cd` into
this folder, then run the command above. One-time setup; it then starts
automatically at every login, invisibly (no console window), and logs to
`data/scheduler.log`.

- Check it's running: `Get-ScheduledTask -TaskName "FinResearch Scheduler" | Get-ScheduledTaskInfo`
- Watch it live: `Get-Content data\scheduler.log -Tail 30 -Wait`
- Start it now without logging out/in: `Start-ScheduledTask -TaskName "FinResearch Scheduler"`
- Remove it: `powershell -ExecutionPolicy Bypass -File uninstall_background_task.ps1`

`run.bat` and the background service will never both run the scheduler at
once — `run.bat` checks for an already-running instance first (via a PID
file) and just opens the dashboard if it finds one, since this app has
exactly one writer by design (see `storage/db.py`).

## Deploying to Render (true 24/7 — no local machine needed)

**Superseded — see the top-level `README.md` in this repo.** finresearch was
originally deployed as its own standalone Render service (`otxengine/
finresearch`, its own `render.yaml`/`start_render.sh`); it was then merged
into this repo (`otxengine/fda-bot`) to run behind fda-bot's existing Render
service instead of paying for a second one — see `backend/finresearch_mount.py`
and the top-level README's "What's in this repo" section for how that works
(a reverse-proxy mount at `/finresearch`, HTTP + WebSocket, plus finresearch's
scheduler as a subprocess of fda-bot's own FastAPI app). The steps and
warnings below described the old standalone setup and no longer apply as
written; kept for history.

The Windows Task Scheduler setup above still needs your PC on. ~~To run this
with zero dependency on any specific machine being on, `render.yaml` deploys
it as one Render Web Service — the scheduler backgrounded, Streamlit in the
foreground, sharing the service's own persistent disk.~~

**⚠️ This app has no login of any kind — it was built for `localhost` only.**
This is still true and still applies at `/finresearch` on the merged
deployment — a deliberate, informed choice, not an oversight.

## Storage

SQLite in WAL mode at `data/finresearch.db` (configurable via
`FINRESEARCH_DB_PATH` in `.env`). The original design planned DuckDB; a
Phase-1 concurrency smoke test found DuckDB's file lock blocks a second
process's read connection while the writer holds the file open on Windows,
so this fell back to SQLite per the plan's own documented contingency — see
`storage/db.py`'s module docstring and `tests/smoke/test_db_concurrency.py`.

## Project layout

```
config/        settings.py + connectors.yaml, macro_thresholds.yaml, additional_indicators.yaml, screener_criteria.yaml
connectors/    one module per data source, pluggable interface (base.py)
ingestion/     canonical pydantic models + per-connector normalizers
storage/       schema.sql, db.py (connection helpers + PID-lock-aware init), writers.py (idempotent upserts)
analysis/      macro_regime.py, additional_indicators.py, technicals.py, screener.py, sector_rotation.py, sentiment.py, chat_tools.py
scheduler/     main.py (APScheduler loop + PID lock), jobs.py (one function per unit of work)
dashboard/     app.py + pages/ (Macro, Watchlist, Screener, News & Flags, Chat)
universe/      S&P 500 + sector ETF symbol lists
tests/         unit tests (test_analysis/, test_connectors/, test_scheduler/) + smoke tests (tests/smoke/)

run.bat                        interactive launcher (dashboard + scheduler if not already running)
run_background.bat             retry-loop wrapper the background service uses
run_background_hidden.vbs      launches run_background.bat with no console window
install_background_task.ps1    registers the always-on Task Scheduler entry (needs admin)
uninstall_background_task.ps1  removes it
```

## Testing

```
.venv\Scripts\python.exe -m pytest tests/ -v
```

Unit tests use recorded fixtures — no live network calls. The two smoke
tests under `tests/smoke/` do real (fast, local) I/O: `test_schema.py`
builds a fresh DB from `schema.sql`, and `test_db_concurrency.py` spawns two
real OS subprocesses to validate the writer+reader access pattern.

Per-connector manual smoke check (needs the relevant API key in `.env`):

```python
from connectors.fred import FredConnector
result = FredConnector().fetch()
print(result.status, result.error)
```

## On data currency ("is this from this month?")

Most indicators (CPI, unemployment, payrolls, PPI, retail sales, industrial
production, housing starts, trade balance) genuinely are last month's figure
once the scheduler's been running — that's simply how often those reports
publish, and hourly polling means a fresh release is picked up promptly.
Two things can't be changed no matter how often we poll:
- **GDP** is quarterly by nature — the newest possible figure is always the
  most recently *completed* quarter, dated by that quarter's start.
- **Consumer Sentiment (UMich)** carries an extra month of delay in FRED's
  own copy of the series versus its original release.
Every indicator on the Macro page shows its own "data as of" date so this is
never hidden — see `analysis/macro_regime.py` and
`analysis/additional_indicators.py`.

## Known limitations / what's not built yet

- **Phase 2** (technicals are wired in, but the S&P 500 universe fetch and
  full Screener page work only once `instruments` is populated — run
  `scheduler.jobs.job_refresh_sp500_universe` once, or wait for its weekly
  schedule).
- **Phase 3** (Nasdaq Data Link, Benzinga) and **Phase 4** (scrape-fallback
  connectors for Investing.com/Finviz/Seeking Alpha/Koyfin/Zacks/
  TradingView/ETFdb) are not implemented — they're off by default in
  `config/connectors.yaml` and were explicitly deferred as optional,
  last-resort per the plan's Legal/ToS Risk Summary.
- Yahoo Finance (`yfinance`) can be rate-limited by Yahoo under heavy use;
  the connector's hard timeout (`fetch_timeout_seconds` in
  `connectors.yaml`) is the safety net if that happens — it degrades to a
  logged error for that tick rather than blocking the schedule.
