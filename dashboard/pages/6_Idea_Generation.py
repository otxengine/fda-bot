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
from dashboard.rtl import apply_rtl

apply_rtl()
st.title("יצירת רעיונות (סקטור + כימות)")
st.caption(
    "מאוטמת תהליך סינון סקטוריאלי וכמותי מתוך מדריך ה-Excel/Zacks שלכם: "
    "שאיבה ← ארגון ← ניתוח. הפלט הוא נקודת פתיחה למחקר נוסף, לא המלצה — "
    "המדריך עצמו מדגיש שרעיונות דורשים ניתוח מעמיק לפני שהם הופכים לערכי "
    "רשימת מעקב או עסקאות."
)

# --- Coverage / freshness --------------------------------------------------
health = load_connector_health()
universe_row = health[health["connector_name"] == "yahoo_finance_universe"] if not health.empty else health
if not universe_row.empty:
    row = universe_row.iloc[0]
    icon = "🟢" if row["status"] == "ok" else "🔴"
    st.caption(f"{icon} שאיבת היקום המלא (עם תחזיות אנליסטים) רצה לאחרונה: {row['run_ts']} — רצה כל כ-3 שעות, לוקחת עד 45 דקות.")

quant_df = load_quant_table()
coverage = quant_df["earnings_growth_f1"].notna().sum()
total = len(quant_df)
if total == 0:
    st.info("טרם נטען היקום המלא של S&P 500 — התזמון ממלא אותו שבועית (או בשאיבת ה-catch-up הראשונה).")
    st.stop()
st.caption(f"כיסוי תחזיות אנליסטים: ל-{coverage}/{total} מטיקרי S&P 500 יש עד כה נתוני EPS/הכנסות עתידיים.")
if coverage < total * 0.5:
    st.warning(
        "הכיסוי עדיין נבנה — שאיבת היקום המלא (עם תחזיות) לוקחת עד 45 דקות ורצה כל "
        "כ-3 שעות. ממוצעי הסקטורים למטה יתמלאו ויתייצבו ככל שיישאבו עוד טיקרים."
    )

st.divider()

# --- Step 1: sector growth overview (guide's Part C, step 1-3) -------------
st.subheader("שלב 1 — סקירת צמיחה סקטוריאלית")
st.caption(
    f"ממוצע צמיחת מכירות לשנה הבאה (SG1) וצמיחת רווחים לשנה הנוכחית/הבאה (EG1/EG2) "
    f"לפי סקטור. ערכים מעבר ל-±{OUTLIER_GROWTH_THRESHOLD:.0%} מוחרגים מהממוצעים הללו "
    "(ככל הנראה 'אפקט בסיס' — בסיס שנה קודמת זעיר שמנפח את האחוז, לפי המדריך), אך "
    "לעולם לא מוסתרים מטבלת רמת המניה בשלב 2."
)
overview = sector_growth_overview(quant_df)
if overview.empty or overview["avg_earnings_growth_f1"].isna().all():
    st.info("אין עדיין מספיק נתוני תחזיות לחישוב ממוצעים סקטוריאליים — בדקו שוב לאחר השלמת שאיבת היקום.")
else:
    fig = go.Figure()
    colors = ["#2f6fed" if v else "#9aa3af" for v in overview["above_overall_average"].fillna(False)]
    fig.add_trace(
        go.Bar(x=overview["sector"], y=overview["avg_earnings_growth_f1"], marker_color=colors, name="ממוצע EG1")
    )
    overall_avg = overview.attrs.get("overall_avg_earnings_growth_f1")
    if overall_avg is not None:
        fig.add_hline(y=overall_avg, line_dash="dot", line_color="#d64545",
                       annotation_text=f"ממוצע כלל-סקטוריאלי: {overall_avg:.1%}")
    fig.update_layout(
        height=350, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
        yaxis_tickformat=".0%",
    )
    st.plotly_chart(fig, use_container_width=True)

    display = overview.rename(columns={
        "sector": "סקטור", "avg_sales_growth": "ממוצע צמיחת מכירות (שנה הבאה)",
        "avg_earnings_growth_f1": "ממוצע צמיחת רווחים (השנה)",
        "avg_earnings_growth_f2": "ממוצע צמיחת רווחים (שנה הבאה)",
        "n_companies": "מס' חברות", "above_overall_average": "מעל הממוצע הכלל-סקטוריאלי",
    })
    st.dataframe(
        display, hide_index=True, use_container_width=True,
        column_config={
            "ממוצע צמיחת מכירות (שנה הבאה)": st.column_config.NumberColumn(format="percent"),
            "ממוצע צמיחת רווחים (השנה)": st.column_config.NumberColumn(format="percent"),
            "ממוצע צמיחת רווחים (שנה הבאה)": st.column_config.NumberColumn(format="percent"),
        },
    )

st.divider()

# --- Step 2: stocks within a chosen sector (guide's Part C, step 4-6) ------
st.subheader("שלב 2 — מניות בתוך סקטור")
sectors = sorted(quant_df["sector"].dropna().unique())
if not sectors:
    st.info("אין עדיין נתוני סקטורים.")
    st.stop()

default_sector = overview.iloc[0]["sector"] if not overview.empty else sectors[0]
selected_sector = st.selectbox("סקטור", sectors, index=sectors.index(default_sector) if default_sector in sectors else 0)

stocks = stocks_in_sector(quant_df, selected_sector)
if stocks.empty:
    st.caption("אין עדיין חברות בסקטור זה.")
else:
    sector_avg_eg1 = stocks.attrs.get("sector_avg_earnings_growth_f1")
    if sector_avg_eg1 is not None:
        st.caption(f"ממוצע צמיחת רווחים בסקטור (השנה, לאחר החרגת חריגים): {sector_avg_eg1:.1%}")

    def _flag(row) -> str:
        flags = []
        if row.get("above_sector_avg_earnings_growth"):
            flags.append("🟢 מעל הממוצע")
        if row.get("earnings_growth_f1_outlier") or row.get("sales_growth_next_year_outlier"):
            flags.append("⚠️ חריג אפשרי (אפקט בסיס)")
        return " ".join(flags)

    display_stocks = stocks.copy()
    display_stocks["סימונים"] = display_stocks.apply(_flag, axis=1)
    display_stocks = display_stocks.rename(columns={
        "symbol": "טיקר", "name": "שם", "industry": "ענף",
        "pe_trailing": "מכפיל רווח (נגזר)", "pe_f1": "מכפיל רווח (F1)", "pe_f2": "מכפיל רווח (F2)",
        "ps_trailing": "מכפיל מכירות (נגזר)", "ps_next_year": "מכפיל מכירות (שנה הבאה)",
        "sales_growth_next_year": "צמיחת מכירות (SG1)",
        "earnings_growth_f1": "צמיחת רווחים (EG1)", "earnings_growth_f2": "צמיחת רווחים (EG2)",
        "peg_ratio": "PEG", "peg_ratio_next": "PEG (הבא)",
    })
    cols_to_show = [
        "טיקר", "שם", "ענף", "מכפיל רווח (נגזר)", "מכפיל רווח (F1)", "מכפיל רווח (F2)",
        "מכפיל מכירות (נגזר)", "מכפיל מכירות (שנה הבאה)", "צמיחת מכירות (SG1)", "צמיחת רווחים (EG1)",
        "צמיחת רווחים (EG2)", "PEG", "PEG (הבא)", "סימונים",
    ]
    st.dataframe(
        display_stocks[cols_to_show], hide_index=True, use_container_width=True,
        column_config={
            "צמיחת מכירות (SG1)": st.column_config.NumberColumn(format="percent"),
            "צמיחת רווחים (EG1)": st.column_config.NumberColumn(format="percent"),
            "צמיחת רווחים (EG2)": st.column_config.NumberColumn(format="percent"),
        },
    )

    promote_col, button_col = st.columns([3, 1])
    symbol_to_add = promote_col.selectbox("הוספת טיקר לרשימת המעקב", stocks["symbol"])
    if button_col.button("הוספה לרשימת המעקב", key="idea_gen_add"):
        ok, msg = add_to_watchlist(symbol_to_add)
        (st.success if ok else st.error)(msg)
