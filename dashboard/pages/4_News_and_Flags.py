"""News & Flags page: filterable, severity-sorted list with status badges
(never color alone — every badge pairs an icon/text with the color), plus the
Connector Health panel so silent connector breakage (especially any Phase 4
scrape fallback) stays visible rather than hidden (plan's Verification
section)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data import load_connector_health, load_flags, load_news
from dashboard.rtl import apply_rtl

apply_rtl()
st.title("חדשות והתראות")

tab_flags, tab_news, tab_health = st.tabs(["התראות", "חדשות", "בריאות מקורות נתונים"])

_SEVERITY_BADGE = {"info": "🔵 מידע", "watch": "🟡 מעקב", "warn": "🔴 אזהרה"}

with tab_flags:
    flags = load_flags(limit=100)
    if flags.empty:
        st.caption("אין עדיין התראות.")
    else:
        severities = st.multiselect("חומרה", options=sorted(flags["severity"].unique()), default=list(flags["severity"].unique()))
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
        st.caption("טרם נשאבו חדשות.")
    else:
        for _, article in news.iterrows():
            sentiment = article["sentiment_score"]
            badge = "🟢" if sentiment and sentiment > 0.2 else ("🔴" if sentiment and sentiment < -0.2 else "⚪")
            symbol_tag = f" `{article['symbol']}`" if pd.notna(article["symbol"]) else ""
            st.markdown(f"{badge}{symbol_tag} [{article['headline']}]({article['url']}) — *{article['published_at']}*")

with tab_health:
    health = load_connector_health()
    if health.empty:
        st.caption("טרם נרשמו ריצות מקורות נתונים — הפעילו את התזמון (run.bat).")
    else:
        def _status_icon(row) -> str:
            if row["status"] == "ok":
                return "🟢 תקין"
            if row["status"] == "disabled":
                return "⚪ מושבת"
            return f"🔴 שגיאה (×{row['failure_streak']} ברצף)"

        health = health.copy()
        health["סטטוס"] = health.apply(_status_icon, axis=1)
        st.dataframe(
            health[["connector_name", "סטטוס", "run_ts", "duration_ms", "error_message"]].rename(
                columns={"connector_name": "מקור נתונים", "run_ts": "ריצה אחרונה", "duration_ms": "משך (מ״ש)", "error_message": "שגיאה אחרונה"}
            ),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "מקור נתונים שביר (במיוחד כל fallback גירוד מ-Phase 4) שנכשל שוב ושוב "
            "יופיע כאן במקום להתיישן בשקט."
        )
