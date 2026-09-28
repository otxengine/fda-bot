"""Mounts finresearch (github.com/otxengine/finresearch — a separate
personal project, now living in this same repo/Render service to avoid
paying for a second always-on service) at /finresearch on this FastAPI app,
and manages its two subprocesses (scheduler + Streamlit dashboard).

Why subprocesses, not in-process: finresearch's own scheduler is a
BlockingScheduler that never returns once started (scheduler/main.py) — a
real subprocess is the right isolation here, exactly matching how
finresearch already runs itself locally (its own run.bat launches these
same two things as two OS processes) — a crash in one can't take the other
down, same principle finresearch's own local architecture already uses.

Why a proxy mount, not something fancier: finresearch is a Streamlit app,
which has to be the actual server handling its own requests, including a
persistent WebSocket for live reactivity — it can't be mounted as an ASGI
sub-application the way another FastAPI app could. asgiproxy (HTTP +
WebSocket, both needed — Streamlit is unusable without the WebSocket)
forwards /finresearch/* to the real Streamlit process on FINRESEARCH_PORT.
Verified end-to-end before this was wired in for real — including actual
browser navigation between pages through the live proxied WebSocket, not
just an HTTP 200 on the root path.
"""
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
FINRESEARCH_PORT = 8502

_scheduler_proc: Optional[subprocess.Popen] = None
_dashboard_proc: Optional[subprocess.Popen] = None


def _finresearch_present() -> bool:
    return (REPO_ROOT / "dashboard" / "app.py").exists()


def mount_finresearch(app: FastAPI) -> None:
    """Call once at import time (module-level app setup), before the server
    starts. No-op if finresearch's code isn't present in this checkout
    (e.g. an older commit, or a partial/local checkout of just backend/) —
    degrades gracefully rather than crashing fda-bot's own startup."""
    if not _finresearch_present():
        logger.info("finresearch not found in this checkout — skipping /finresearch mount")
        return

    try:
        from asgiproxy.config import BaseURLProxyConfigMixin, ProxyConfig
        from asgiproxy.context import ProxyContext
        from asgiproxy.simple_proxy import make_simple_proxy_app
    except ImportError:
        logger.warning("asgiproxy not installed — skipping /finresearch mount")
        return

    class _FinresearchProxyConfig(BaseURLProxyConfigMixin, ProxyConfig):
        upstream_base_url = f"http://localhost:{FINRESEARCH_PORT}"
        rewrite_host_header = f"localhost:{FINRESEARCH_PORT}"

    proxy_context = ProxyContext(config=_FinresearchProxyConfig())
    app.mount("/finresearch", make_simple_proxy_app(proxy_context))

    @app.on_event("shutdown")
    async def _close_finresearch_proxy():
        await proxy_context.close()

    logger.info(f"Mounted finresearch at /finresearch (upstream :{FINRESEARCH_PORT})")


def start_finresearch_subprocesses() -> None:
    """Launch finresearch's scheduler + Streamlit dashboard as real OS
    subprocesses. Call from fda-bot's own lifespan startup. Never raises —
    a failure here must not take fda-bot's own API down."""
    global _scheduler_proc, _dashboard_proc

    if not _finresearch_present():
        return

    try:
        _scheduler_proc = subprocess.Popen(
            [sys.executable, "-m", "scheduler.main"],
            cwd=str(REPO_ROOT),
        )
        logger.info(f"finresearch scheduler started (pid {_scheduler_proc.pid})")
    except Exception as e:
        logger.error(f"Failed to start finresearch scheduler: {e}")

    try:
        _dashboard_proc = subprocess.Popen(
            [
                sys.executable, "-m", "streamlit", "run", "dashboard/app.py",
                "--server.port", str(FINRESEARCH_PORT),
                "--server.address", "0.0.0.0",
                "--server.headless", "true",
                "--server.baseUrlPath", "finresearch",
                "--browser.gatherUsageStats", "false",
                # Streamlit's default CORS/XSRF origin-checking compares the
                # browser's Origin (https://<render-domain>) against what it
                # thinks it's serving as (localhost:8502) and rejects the
                # WebSocket handshake on mismatch — confirmed live: worked in
                # local same-machine testing (origins close enough to pass)
                # but failed on the real deployed domain with "WebSocket
                # onerror" in the browser console. Disabling both is
                # Streamlit's own documented fix for running behind any
                # reverse proxy (https://docs.streamlit.io — deployment
                # behind a proxy). Safe here specifically because Streamlit
                # itself is never reached directly — only via the asgiproxy
                # mount, which is the only thing actually exposed publicly.
                "--server.enableCORS", "false",
                "--server.enableXsrfProtection", "false",
            ],
            cwd=str(REPO_ROOT),
        )
        logger.info(f"finresearch dashboard started (pid {_dashboard_proc.pid})")
    except Exception as e:
        logger.error(f"Failed to start finresearch dashboard: {e}")


def stop_finresearch_subprocesses() -> None:
    for proc, name in ((_scheduler_proc, "scheduler"), (_dashboard_proc, "dashboard")):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                logger.info(f"finresearch {name} stopped")
            except Exception as e:
                logger.debug(f"finresearch {name} stop failed: {e}")
