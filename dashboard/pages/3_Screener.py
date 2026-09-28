"""Screener page: filters are just a status caption here (the actual criteria
live in config/screener_criteria.yaml, edited outside the UI per the plan —
no in-app criteria editor for v1); the ranked, sortable table is the primary
content. A sparing score-column tint (sequential, not a "rainbow" of colors)
per the plan's dashboard design principles."""

from __future__ import annotations

import streamlit as st

from analysis.screener import load_criteria
from dashboard.data import add_to_watchlist, load_screener_latest

st.set_page_config(page_title="Screener — Fin Research", page_icon="📈", layout="wide")
st.title("Screener")

cfg = load_criteria()
with st.expander("Current criteria (edit config/screener_criteria.yaml to change)"):
    for c in cfg["criteria"]:
        st.markdown(f"- **{c['display_name']}** — {c['direction'].replace('_', ' ')}, weight {c['weight']}")

results = load_screener_latest(n=100)
if results.empty:
    st.info("No screener run yet — it runs hourly once the scheduler is active (see run.bat).")
    st.stop()

st.caption(f"Latest run: {results.attrs.get('run_ts', 'unknown')} — {len(results)} matches shown")

st.dataframe(
    results.rename(columns={"symbol": "Symbol", "rank": "Rank", "score": "Score"}),
    hide_index=True,
    use_container_width=True,
    column_config={
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
    },
)

promote_col, button_col = st.columns([3, 1])
symbol_to_promote = promote_col.selectbox("Promote a symbol to your watchlist", results["symbol"])
if button_col.button("Add to watchlist"):
    ok, msg = add_to_watchlist(symbol_to_promote)
    (st.success if ok else st.error)(msg)
