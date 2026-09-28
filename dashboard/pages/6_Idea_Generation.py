"""Idea Generation — sector + quantitative screener, automating the exact
methodology from the user's own Excel/Zacks guide (מדריך ניתוח קוונטיטיבי
באקסל). The guide's 3 parts map onto this app as:

  Part A (pull data)  -> already automated by the scheduler (yahoo_finance
                          connectors + universe/sp500_fetch.py) — no manual
                          Zacks screener/CSV download needed.
  Part B (organize)    -> analysis/sector_quant_screener.py::build_quant_table()
                          computes the same derived columns as the guide's
                          Excel formulas (P/E trailing/F1/F2, P/S, SG1, EG1,
                          EG2, PEG) — always up to date, no manual filtering.
  Part C (analyze)      -> the two sections on this page, in the guide's own
                          order: sector-level growth overview, then
                          stock-level screening within a chosen sector.

This is descriptive screening output for further research, same as the
guide itself says — not investment advice, and never a substitute for the
"deep analysis" step the guide explicitly calls out before anything reaches
a real watchlist.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from analysis.sector_quant_screener import OUTLIER_GROWTH_THRESHOLD, sector_growth_overview, stocks_in_sector
from dashboard.data import add_to_watchlist, load_connector_health, load_quant_table

st.set_page_config(page_title="Idea Generation — Fin Research", page_icon="📈", layout="wide")
st.title("Idea Generation (Sector + Quant)")
st.caption(
    "Automates the sector + quantitative screening process from your own Excel/Zacks guide: "
    "pull → organize → analyze. Output is a starting point for further research, not a "
    "recommendation — the guide itself is explicit that ideas need deep analysis before they "
    "become watchlist entries or trades."
)

# --- Coverage / freshness --------------------------------------------------
health = load_connector_health()
universe_row = health[health["connector_name"] == "yahoo_finance_universe"] if not health.empty else health
if not universe_row.empty:
    row = universe_row.iloc[0]
    icon = "🟢" if row["status"] == "ok" else "🔴"
    st.caption(f"{icon} Full-universe fetch (with analyst estimates) last ran: {row['run_ts']} — runs every ~3h, takes up to 45 min.")

quant_df = load_quant_table()
coverage = quant_df["earnings_growth_f1"].notna().sum()
total = len(quant_df)
if total == 0:
    st.info("No S&P 500 universe loaded yet — the scheduler populates this weekly (or on first catch-up).")
    st.stop()
st.caption(f"Analyst estimate coverage: {coverage}/{total} S&P 500 symbols have forward EPS/revenue data so far.")
if coverage < total * 0.5:
    st.warning(
        "Coverage is still building — the full-universe fetch (with estimates) takes up to 45 "
        "minutes and runs every ~3 hours. Sector averages below will fill in and stabilize as "
        "more symbols get fetched."
    )

st.divider()

# --- Step 1: sector growth overview (guide's Part C, step 1-3) -------------
st.subheader("Step 1 — Sector Growth Overview")
st.caption(
    f"Average next-year sales growth (SG1) and this/next-year earnings growth (EG1/EG2) per "
    f"sector. Values beyond ±{OUTLIER_GROWTH_THRESHOLD:.0%} are excluded from these averages "
    "(likely a 'base effect' — a tiny prior-year base inflating the % — per the guide), but "
    "never hidden from the stock-level table in Step 2."
)
overview = sector_growth_overview(quant_df)
if overview.empty or overview["avg_earnings_growth_f1"].isna().all():
    st.info("Not enough estimate data yet to compute sector averages — check back once the universe fetch completes.")
else:
    fig = go.Figure()
    colors = ["#2f6fed" if v else "#9aa3af" for v in overview["above_overall_average"].fillna(False)]
    fig.add_trace(
        go.Bar(x=overview["sector"], y=overview["avg_earnings_growth_f1"], marker_color=colors, name="Avg EG1")
    )
    overall_avg = overview.attrs.get("overall_avg_earnings_growth_f1")
    if overall_avg is not None:
        fig.add_hline(y=overall_avg, line_dash="dot", line_color="#d64545",
                       annotation_text=f"Cross-sector avg: {overall_avg:.1%}")
    fig.update_layout(
        height=350, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
        yaxis_tickformat=".0%",
    )
    st.plotly_chart(fig, use_container_width=True)

    display = overview.rename(columns={
        "sector": "Sector", "avg_sales_growth": "Avg Sales Growth (next yr)",
        "avg_earnings_growth_f1": "Avg Earnings Growth (this yr)",
        "avg_earnings_growth_f2": "Avg Earnings Growth (next yr)",
        "n_companies": "# Companies", "above_overall_average": "Above Cross-Sector Avg",
    })
    st.dataframe(
        display, hide_index=True, use_container_width=True,
        column_config={
            "Avg Sales Growth (next yr)": st.column_config.NumberColumn(format="percent"),
            "Avg Earnings Growth (this yr)": st.column_config.NumberColumn(format="percent"),
            "Avg Earnings Growth (next yr)": st.column_config.NumberColumn(format="percent"),
        },
    )

st.divider()

# --- Step 2: stocks within a chosen sector (guide's Part C, step 4-6) ------
st.subheader("Step 2 — Stocks Within a Sector")
sectors = sorted(quant_df["sector"].dropna().unique())
if not sectors:
    st.info("No sector data yet.")
    st.stop()

default_sector = overview.iloc[0]["sector"] if not overview.empty else sectors[0]
selected_sector = st.selectbox("Sector", sectors, index=sectors.index(default_sector) if default_sector in sectors else 0)

stocks = stocks_in_sector(quant_df, selected_sector)
if stocks.empty:
    st.caption("No companies in this sector yet.")
else:
    sector_avg_eg1 = stocks.attrs.get("sector_avg_earnings_growth_f1")
    if sector_avg_eg1 is not None:
        st.caption(f"Sector average earnings growth (this year, outlier-trimmed): {sector_avg_eg1:.1%}")

    def _flag(row) -> str:
        flags = []
        if row.get("above_sector_avg_earnings_growth"):
            flags.append("🟢 above avg")
        if row.get("earnings_growth_f1_outlier") or row.get("sales_growth_next_year_outlier"):
            flags.append("⚠️ possible base-effect outlier")
        return " ".join(flags)

    display_stocks = stocks.copy()
    display_stocks["Flags"] = display_stocks.apply(_flag, axis=1)
    display_stocks = display_stocks.rename(columns={
        "symbol": "Symbol", "name": "Name", "industry": "Industry",
        "pe_trailing": "P/E (Trailing)", "pe_f1": "P/E (F1)", "pe_f2": "P/E (F2)",
        "ps_trailing": "P/S (Trailing)", "ps_next_year": "P/S (Next Yr)",
        "sales_growth_next_year": "Sales Growth (SG1)",
        "earnings_growth_f1": "Earnings Growth (EG1)", "earnings_growth_f2": "Earnings Growth (EG2)",
        "peg_ratio": "PEG", "peg_ratio_next": "PEG (Next)",
    })
    cols_to_show = [
        "Symbol", "Name", "Industry", "P/E (Trailing)", "P/E (F1)", "P/E (F2)",
        "P/S (Trailing)", "P/S (Next Yr)", "Sales Growth (SG1)", "Earnings Growth (EG1)",
        "Earnings Growth (EG2)", "PEG", "PEG (Next)", "Flags",
    ]
    st.dataframe(
        display_stocks[cols_to_show], hide_index=True, use_container_width=True,
        column_config={
            "Sales Growth (SG1)": st.column_config.NumberColumn(format="percent"),
            "Earnings Growth (EG1)": st.column_config.NumberColumn(format="percent"),
            "Earnings Growth (EG2)": st.column_config.NumberColumn(format="percent"),
        },
    )

    promote_col, button_col = st.columns([3, 1])
    symbol_to_add = promote_col.selectbox("Add a symbol to your watchlist", stocks["symbol"])
    if button_col.button("Add to watchlist", key="idea_gen_add"):
        ok, msg = add_to_watchlist(symbol_to_add)
        (st.success if ok else st.error)(msg)
