"""Screener page: filters are just a status caption here (the actual criteria
live in config/screener_criteria.yaml, edited outside the UI per the plan —
no in-app criteria editor for v1); the ranked, sortable table is the primary
content. A sparing score-column tint (sequential, not a "rainbow" of colors)
per the plan's dashboard design principles."""

from __future__ import annotations

import streamlit as st

from analysis.screener import load_criteria
from dashboard.data import add_to_watchlist, load_screener_latest
from dashboard.rtl import apply_rtl

apply_rtl()
st.title("סקרינר")

cfg = load_criteria()
with st.expander("קריטריונים נוכחיים (לשינוי, ערכו את config/screener_criteria.yaml)"):
    for c in cfg["criteria"]:
        st.markdown(f"- **{c['display_name']}** — {c['direction'].replace('_', ' ')}, משקל {c['weight']}")

results = load_screener_latest(n=100)
if results.empty:
    st.info("הסקרינר טרם רץ — הוא רץ כל שעה כשהתזמון פעיל (ראו run.bat).")
    st.stop()

st.caption(f"ריצה אחרונה: {results.attrs.get('run_ts', 'unknown')} — מוצגות {len(results)} התאמות")

st.dataframe(
    results.rename(columns={"symbol": "טיקר", "rank": "דירוג", "score": "ציון"}),
    hide_index=True,
    use_container_width=True,
    column_config={
        "ציון": st.column_config.ProgressColumn("ציון", min_value=0, max_value=100, format="%.1f"),
    },
)

promote_col, button_col = st.columns([3, 1])
symbol_to_promote = promote_col.selectbox("קידום טיקר לרשימת המעקב", results["symbol"])
if button_col.button("הוספה לרשימת המעקב"):
    ok, msg = add_to_watchlist(symbol_to_promote)
    (st.success if ok else st.error)(msg)
