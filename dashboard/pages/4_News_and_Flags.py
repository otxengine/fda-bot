"""News & Flags page: filterable, severity-sorted list with status badges
(never color alone — every badge pairs an icon/text with the color), plus the
Connector Health panel so silent connector breakage (especially any Phase 4
scrape fallback) stays visible rather than hidden (plan's Verification
section)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data import load_connector_health, load_flags, load_news

st.set_page_config(page_title="News & Flags — Fin Research", page_icon="📈", layout="wide")
st.title("News & Flags")

tab_flags, tab_news, tab_health = st.tabs(["Flags", "News", "Connector Health"])

_SEVERITY_BADGE = {"info": "🔵 Info", "watch": "🟡 Watch", "warn": "🔴 Warn"}

with tab_flags:
    flags = load_flags(limit=100)
    if flags.empty:
        st.caption("No flags yet.")
    else:
        severities = st.multiselect("Severity", options=sorted(flags["severity"].unique()), default=list(flags["severity"].unique()))
        filtered = flags[flags["severity"].isin(severities)]
        for _, f in filtered.iterrows():
            badge = _SEVERITY_BADGE.get(f["severity"], f["severity"])
            # NULL symbols (macro-level flags, e.g. FOMC meetings) come back from
            # SQL as NaN in the DataFrame — NaN is truthy in Python, so `if
            # f["symbol"]` alone would render the literal string "nan". Must
            # check pd.notna() explicitly.
            symbol_tag = f" — `{f['symbol']}`" if pd.notna(f["symbol"]) else ""
            st.markdown(f"{badge}{symbol_tag}: {f['message']}  \n*{f['created_at']}*")

with tab_news:
    news = load_news(limit=50)
    if news.empty:
        st.caption("No news fetched yet.")
    else:
        for _, article in news.iterrows():
            sentiment = article["sentiment_score"]
            badge = "🟢" if sentiment and sentiment > 0.2 else ("🔴" if sentiment and sentiment < -0.2 else "⚪")
            symbol_tag = f" `{article['symbol']}`" if pd.notna(article["symbol"]) else ""
            st.markdown(f"{badge}{symbol_tag} [{article['headline']}]({article['url']}) — *{article['published_at']}*")

with tab_health:
    health = load_connector_health()
    if health.empty:
        st.caption("No connector runs logged yet — start the scheduler (run.bat).")
    else:
        def _status_icon(row) -> str:
            if row["status"] == "ok":
                return "🟢 OK"
            if row["status"] == "disabled":
                return "⚪ Disabled"
            return f"🔴 Error (×{row['failure_streak']} in a row)"

        health = health.copy()
        health["Status"] = health.apply(_status_icon, axis=1)
        st.dataframe(
            health[["connector_name", "Status", "run_ts", "duration_ms", "error_message"]].rename(
                columns={"connector_name": "Connector", "run_ts": "Last run", "duration_ms": "Duration (ms)", "error_message": "Last error"}
            ),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "A fragile connector (especially any Phase 4 scrape fallback) failing repeatedly "
            "shows up here rather than silently going stale."
        )
