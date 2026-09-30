"""Watchlist page: add/remove symbols, then per-symbol deep dive — candlestick
(not line — OHLC all matter), moving averages in a fixed color order, RSI/MACD
in their own sub-panel (never sharing the price axis — plan's "no dual axis"
rule), fundamentals with trend, and recent news with sentiment badges."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import pandas as pd

from dashboard.data import (
    add_to_watchlist,
    load_earnings_estimates,
    load_fundamentals_history,
    load_news,
    load_price_history_with_technicals,
    load_watchlist_details,
    load_watchlist_symbols,
    remove_from_watchlist,
)
from dashboard.rtl import apply_rtl

_PERIOD_LABELS = {"0q": "הרבעון הנוכחי", "+1q": "הרבעון הבא", "0y": "השנה הנוכחית", "+1y": "השנה הבאה"}
_PERIOD_ORDER = ["0q", "+1q", "0y", "+1y"]

apply_rtl()
st.title("רשימת מעקב")

with st.form("add_symbol", clear_on_submit=True):
    cols = st.columns([2, 4, 1])
    new_symbol = cols[0].text_input("הוספת טיקר", placeholder="למשל AAPL").strip().upper()
    new_notes = cols[1].text_input("הערות (אופציונלי)")
    submitted = cols[2].form_submit_button("הוספה")
    if submitted and new_symbol:
        ok, msg = add_to_watchlist(new_symbol, new_notes)
        (st.success if ok else st.error)(msg)

symbols = load_watchlist_symbols()
if not symbols:
    st.info("רשימת המעקב שלכם ריקה — הוסיפו טיקר למעלה.")
    st.stop()

details = load_watchlist_details()
st.caption("נתוני רשימת המעקב נכונים לשאיבת ה-yahoo_finance האחרונה של התזמון.")
st.dataframe(
    details.rename(columns={
        "symbol": "טיקר", "price": "מחיר", "change_pct": "שינוי %",
        "pe_ratio": "מכפיל רווח", "market_cap": "שווי שוק",
        "fifty_two_wk_high": "גבוה 52 שבועות", "fifty_two_wk_low": "נמוך 52 שבועות",
        "notes": "הערות", "target_price": "יעד", "tags": "תגיות",
    }),
    hide_index=True, use_container_width=True,
)

selected = st.selectbox("מבט מעמיק", symbols)
remove_col, _ = st.columns([1, 5])
if remove_col.button(f"הסרת {selected} מרשימת המעקב"):
    ok, msg = remove_from_watchlist(selected)
    (st.success if ok else st.error)(msg)
    st.rerun()

row = details[details["symbol"] == selected].iloc[0] if not details.empty else None
if row is not None:
    m = st.columns(4)
    m[0].metric("מחיר", f"${row['price']:.2f}" if row["price"] else "—")
    m[1].metric("שינוי %", f"{row['change_pct']:+.2f}%" if row["change_pct"] is not None else "—")
    m[2].metric("מכפיל רווח", f"{row['pe_ratio']:.1f}" if row["pe_ratio"] else "—")
    m[3].metric("שווי שוק", f"${row['market_cap']/1e9:.1f}B" if row["market_cap"] else "—")

df = load_price_history_with_technicals(selected, interval="1d")
if df.empty:
    st.info("אין עדיין היסטוריית מחירים לטיקר זה — התזמון ישאב אותה בטיק הבא.")
else:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05,
        subplot_titles=(f"{selected} — יומי", "RSI (14)"),
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="מחיר",
        ),
        row=1, col=1,
    )
    # Fixed categorical color order for the moving-average overlays — never auto-cycled.
    ma_colors = {"sma20": "#2f6fed", "sma50": "#f2a340", "sma200": "#7b4fd6"}
    for col_name, color in ma_colors.items():
        if col_name in df.columns:
            fig.add_trace(
                go.Scatter(x=df.index, y=df[col_name], mode="lines", name=col_name.upper(), line=dict(color=color, width=1.5)),
                row=1, col=1,
            )
    if "rsi14" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["rsi14"], mode="lines", name="RSI(14)", line=dict(color="#2f6fed")),
            row=2, col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color="#d64545", row=2, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="#2f9e5e", row=2, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

st.subheader("פונדמנטלס (מגמה)")
fund_hist = load_fundamentals_history(selected)
if fund_hist.empty:
    st.caption("אין עדיין היסטוריית פונדמנטלס.")
else:
    st.dataframe(
        fund_hist.rename(columns={
            "as_of": "נכון ל", "pe_ratio": "מכפיל רווח", "forward_pe": "מכפיל רווח עתידי", "eps": "רווח למניה",
            "revenue_growth_yoy": "צמיחת הכנסות שנתית", "profit_margin": "שולי רווח", "roe": "תשואה להון",
        }),
        hide_index=True, use_container_width=True,
    )

st.subheader("תחזיות אנליסטים")
st.caption(
    "תחזיות קונצנזוס לפי תקופה — 'השנה הנוכחית'/'השנה הבאה' עונות על הכנסות/רווחים "
    "נוכחיים מול השנה הבאה וקצב הצמיחה השנתי שלהם. קונצנזוס אנליסטים, לא ניתוח של האפליקציה עצמה."
)
estimates = load_earnings_estimates(selected)
if estimates.empty:
    st.caption("טרם נשאבו תחזיות אנליסטים לטיקר זה.")
else:
    pivoted = estimates.pivot_table(index="period", columns="metric", values=["avg_estimate", "growth_yoy"])
    rows = []
    for period in _PERIOD_ORDER:
        if period not in pivoted.index:
            continue
        rows.append(
            {
                "תקופה": _PERIOD_LABELS[period],
                "תחזית רווח (EPS)": pivoted.loc[period, ("avg_estimate", "earnings")]
                if ("avg_estimate", "earnings") in pivoted.columns else None,
                # growth_yoy is stored as a fraction (0.0864) — *100 here so the
                # "%.1f%%" column format below renders "8.6%", not "0.1%".
                "צמיחת רווח שנתית": pivoted.loc[period, ("growth_yoy", "earnings")] * 100
                if ("growth_yoy", "earnings") in pivoted.columns and pd.notna(pivoted.loc[period, ("growth_yoy", "earnings")]) else None,
                "תחזית הכנסות": pivoted.loc[period, ("avg_estimate", "revenue")]
                if ("avg_estimate", "revenue") in pivoted.columns else None,
                "צמיחת הכנסות שנתית": pivoted.loc[period, ("growth_yoy", "revenue")] * 100
                if ("growth_yoy", "revenue") in pivoted.columns and pd.notna(pivoted.loc[period, ("growth_yoy", "revenue")]) else None,
            }
        )
    display_df = pd.DataFrame(rows)
    st.dataframe(
        display_df,
        hide_index=True, use_container_width=True,
        column_config={
            "צמיחת רווח שנתית": st.column_config.NumberColumn(format="%.1f%%"),
            "צמיחת הכנסות שנתית": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    as_of = estimates["as_of"].iloc[0]
    st.caption(f"נכון ל-{as_of}")

st.subheader("חדשות אחרונות")
news = load_news(symbol=selected, limit=15)
if news.empty:
    st.caption("טרם נשאבו חדשות לטיקר זה.")
else:
    for _, article in news.iterrows():
        sentiment = article["sentiment_score"]
        badge = "🟢" if sentiment and sentiment > 0.2 else ("🔴" if sentiment and sentiment < -0.2 else "⚪")
        st.markdown(f"{badge} [{article['headline']}]({article['url']}) — *{article['published_at']}*")
