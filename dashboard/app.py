"""Dashboard entrypoint. Run via `streamlit run dashboard/app.py` (or run.bat,
which starts this alongside the scheduler process). This process is
read-only against the DB — see dashboard/data.py and plan's ADR-3."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Fin Research", page_icon="📈", layout="wide")

st.title("📈 Personal Financial Markets Research")
st.caption(
    "Local, personal research & decision-support tool. Never places trades. "
    "Data updates only while the scheduler process is running — see the "
    "Connector Health panel on the News & Flags page for freshness."
)

st.markdown(
    """
Use the pages in the sidebar:

- **Macro** — the synthesized regime badge and core macro indicator trends
- **Watchlist** — deep per-ticker view (price, technicals, fundamentals, news)
- **Screener** — broad-universe ranked scan
- **News & Flags** — recent headlines, flagged events, and connector health
- **Chat** — ask questions grounded strictly in this app's own stored data

This is a heuristic research tool, not investment advice — every macro/screener
score is a rule-based descriptive label, never a prediction.
"""
)
