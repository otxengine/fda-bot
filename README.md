# fda-bot + finresearch

One Render service running two personal projects:

- **fda-bot** — FDA/biopharma catalyst options-flow scanner. Scrapes/pulls
  FDA event calendars (BiopharmCatalyst, EDGAR, FDA.gov, etc.), scores
  options-flow signals, tracks real outcomes, sends Telegram alerts. Lives
  in `backend/`.
- **finresearch** — personal macro/micro financial markets research
  dashboard (FRED/BLS/BEA/World Bank macro data, a stock screener, a
  chat/advisor grounded in its own stored data, and a monitoring page for
  *this* project). Originally its own repo/Render service
  (`otxengine/finresearch`); merged in here to avoid paying for a second
  always-on service. Lives at repo root: `analysis/`, `config/`,
  `connectors/`, `dashboard/`, `ingestion/`, `scheduler/`, `storage/`,
  `universe/`. Its own detailed docs: [`README_finresearch.md`](README_finresearch.md).

## How the merge works

Both apps keep their own code, own database file, own background scheduler
— nothing about either app's internals changed to make this work. What's
new is `backend/finresearch_mount.py`, called from fda-bot's own FastAPI
startup (`backend/main.py`):

1. **Mounts a reverse proxy at `/finresearch`** on fda-bot's existing
   FastAPI app, forwarding both HTTP and WebSocket traffic to finresearch's
   Streamlit process. (Streamlit can't be mounted as an ASGI sub-app the
   way another FastAPI app could — it needs to be a real server, including
   a persistent WebSocket for its live reactivity — so this proxies to it
   instead, via [`asgiproxy`](https://pypi.org/project/asgiproxy/).
   Verified end-to-end — real browser, live page navigation through the
   proxied WebSocket — before being wired in for real.)
2. **Launches finresearch's own scheduler and Streamlit dashboard as
   subprocesses** of fda-bot's FastAPI process, on an internal port
   (8502) — the exact same two-process shape finresearch already runs
   locally via its own `run.bat`, just supervised from here instead of a
   batch file.

Only ONE thing binds to Render's public `$PORT`: fda-bot's own FastAPI app
(`startCommand` in `render.yaml` is unchanged from before the merge). Both
apps' databases live on the same Render persistent disk (`/data/fda_scanner.db`
and `/data/finresearch.db`) — separate files, no sharing, each app owns its
own schema and writes.

**⚠️ finresearch has no authentication of any kind — it was built for
`localhost`-only use.** Mounted at `/finresearch` on this public Render
service, anyone who finds the URL can browse its data and use its Chat page,
which spends *your* Anthropic API credits on *their* conversation. This was
a deliberate, informed choice (see `README_finresearch.md`), not an
oversight — if that's no longer acceptable, say so and a password gate can
be added.

## Setup / running locally

Each app still runs exactly as it always did on its own:
- fda-bot: see below.
- finresearch: see [`README_finresearch.md`](README_finresearch.md) (its
  `run.bat`, background Task Scheduler setup, etc. are all unaffected by
  the merge — they're for local Windows use, separate from this repo's
  Render deployment).

To run the FULL merged setup locally (fda-bot + finresearch behind one
proxy, exactly as it runs on Render):
```
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Then fda-bot is at `http://localhost:8000/` and finresearch at
`http://localhost:8000/finresearch`.

### fda-bot env vars
`POLYGON_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BPC_API_KEY`,
`DB_PATH` — see `.env.example`.

### finresearch env vars
`FRED_API_KEY`, `BLS_API_KEY`, `BEA_API_KEY`, `FINRESEARCH_DB_PATH` — see
`.env.example`.

### Shared
`ANTHROPIC_API_KEY` — used by fda-bot's learning engine
(`backend/signals/learning_engine.py`) and by finresearch's Chat page.
Leave blank to disable finresearch's Chat page (degrades gracefully rather
than erroring); fda-bot's learning features degrade gracefully too.

## Deploying

`render.yaml` is the single source of truth — push to `master` and Render
auto-deploys. Env vars marked `sync: false` there are set once in the
Render dashboard, not synced from this file.
