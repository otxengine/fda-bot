"""Macro page: regime badge (stat tile, not a chart — plan's dashboard design
principles), headline st.metric row, per-indicator trend charts (one axis
each, level + YoY/momentum — never a dual y-axis), sector rotation panel,
and a small World Bank cross-country panel."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis.macro_regime import apply_transform, load_thresholds
from dashboard.data import (
    load_additional_indicators,
    load_connector_health,
    load_indicator_series,
    load_latest_regime,
    load_regime_history,
    load_sector_rotation,
)
from dashboard.rtl import apply_rtl

# Category keys are fixed by config/macro_thresholds.yaml (rates/inflation/
# employment/growth) — translated for display only, the underlying keys
# stay English since analysis/macro_regime.py's logic keys off them.
_CATEGORY_HE = {"rates": "ריבית", "inflation": "אינפלציה", "employment": "תעסוקה", "growth": "צמיחה"}


def _cat_label(cat: str) -> str:
    return _CATEGORY_HE.get(cat, cat.title())


# Regime labels are fixed by config/macro_thresholds.yaml and stored as-is
# (English) in macro_regime_history — translated here for display only, so
# the stored/historical value and anything chat_tools.py cites stays
# unambiguous and unchanged.
_REGIME_HE = {
    "Expansion": "התרחבות",
    "Late-cycle": "שלב מאוחר במחזור",
    "Slowdown": "האטה",
    "Contraction risk": "סיכון להתכווצות",
}


def _regime_label(label: str) -> str:
    return _REGIME_HE.get(label, label)


apply_rtl()
st.title("מאקרו")

st.caption(
    "🟢/🔴 למטה = מתי בפעם האחרונה **שאבנו** נתונים מכל מקור (בריאות המקורות). "
    "זה שונה מרמת העדכניות של **הנתונים עצמם** — לנתונים כלכליים יש פיגור פרסום "
    "משלהם (למשל CPI לחודש מתפרסם שבועות אחר כך, תמ״ג הוא רבעוני). כל אינדיקטור "
    "למטה מציג תאריך **'נכון ל'** משלו כדי שתוכלו להבחין בין השניים במבט חטוף."
)

# --- Freshness (fetch recency) --------------------------------------------
health = load_connector_health()
macro_sources = ["fred", "bls", "bea", "worldbank", "federal_reserve"]
if not health.empty:
    freshness = health[health["connector_name"].isin(macro_sources)][["connector_name", "run_ts", "status"]]
    if not freshness.empty:
        stale = freshness[freshness["status"] != "ok"]
        cols = st.columns(len(freshness))
        for col, (_, row) in zip(cols, freshness.iterrows()):
            icon = "🟢" if row["status"] == "ok" else "🔴"
            col.caption(f"{icon} {row['connector_name']}: {row['run_ts']}")

st.divider()

# --- Regime badge (stat tile, not a chart) --------------------------------
regime = load_latest_regime()
if regime is None:
    st.info("טרם חושב משטר מאקרו — הריצו את התזמון (scheduler) לפחות פעם אחת (ראו run.bat).")
elif regime["indicators_with_data"] == 0:
    # No confident-looking label from zero real inputs — a specific badge
    # (e.g. "Late-cycle") computed from an all-missing-data default of 0.0
    # is exactly the false-precision failure mode the plan warns against.
    # Found via live end-to-end testing with FRED/BLS/BEA disabled.
    st.warning(
        "**אין מספיק נתונים** — לאף אחד ממדדי המאקרו אין עדיין נתונים "
        "(נדרש לפחות FRED_API_KEY; ראו .env.example). לא תוצג תווית משטר "
        "עד שלפחות מדד אחד ידווח ערך."
    )
else:
    badge_col, detail_col = st.columns([1, 2])
    with badge_col:
        st.metric("משטר מאקרו", _regime_label(regime["regime_label"]))
        st.caption(
            f"חושב ב-{regime['computed_at']} — תווית היוריסטית, לא תחזית. "
            f"מבוסס על {regime['indicators_with_data']}/{regime['total_indicators']} מדדים "
            f"(כיסוי {regime['coverage']:.0%})."
        )
        if regime["coverage"] < 1.0:
            st.caption("⚠️ נתונים חלקיים — מדדים חסרים לא נכללים בממוצע ואינם נחשבים כנייטרליים.")
    with detail_col:
        cat_cols = st.columns(len(regime["category_scores"]))
        for col, (cat, score) in zip(cat_cols, regime["category_scores"].items()):
            if score is None:
                col.metric(_cat_label(cat), "אין נתונים")
                continue
            light = "🟢" if score > 0.25 else ("🔴" if score < -0.25 else "🟡")
            col.metric(_cat_label(cat), f"{light} {score:+.2f}")

    with st.expander("למה התווית הזו? (טבלת הכללים, לפי מדד)"):
        for category, indicators in regime["details"].items():
            st.markdown(f"**{_cat_label(category)}**")
            rows = [
                {
                    "מדד": d["display_name"],
                    "ערך": d["value"],
                    "טרנספורמציה": d["transform"],
                    "אור": {1: "🟢", 0: "🟡", -1: "🔴"}[d["light"]],
                    "נכון ל": d.get("as_of_date", "—"),
                }
                for d in indicators
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    history = load_regime_history()
    if not history.empty:
        st.caption("תווית המשטר לאורך זמן")
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=history["computed_at"], y=history["regime_label"].map(_regime_label), mode="markers+lines",
                line=dict(color="#2f6fed"), name="משטר",
            )
        )
        fig.update_layout(height=200, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Indicator trend charts (one axis each; level + momentum) ------------
st.subheader("מדדים")
thresholds = load_thresholds()
for category, cat_cfg in thresholds["categories"].items():
    st.markdown(f"**{_cat_label(category)}**")
    ind_cols = st.columns(len(cat_cfg["indicators"]))
    for col, (key, ind_cfg) in zip(ind_cols, cat_cfg["indicators"].items()):
        series = load_indicator_series(ind_cfg["series_id"])
        with col:
            if series.empty:
                st.caption(f"{ind_cfg['display_name']}: אין נתונים עדיין")
                continue
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", line=dict(color="#2f6fed")))
            fig.update_layout(
                title=ind_cfg["display_name"], height=220,
                margin=dict(l=10, r=10, t=40, b=10), showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
            latest_transformed = apply_transform(series, ind_cfg.get("transform", "none"))
            as_of = series.index[-1].date().isoformat()
            if latest_transformed is not None:
                st.caption(f"אחרון ({ind_cfg.get('transform', 'level')}): {latest_transformed:.2f} — **נכון ל-{as_of}**")

st.divider()

# --- Additional indicators (not part of the regime score) ------------------
# Broader coverage added after live use surfaced that the original 9-series
# regime set left the chat with nothing to say about consumption, production,
# housing, sentiment, or trade — see connectors/fred.py's ADDITIONAL_SERIES.
st.subheader("מדדים נוספים")
st.caption("כיסוי מאקרו רחב יותר — לא חלק מציון המשטר למעלה, רק הקשר נוסף.")
additional = load_additional_indicators()
if additional.empty:
    st.caption("אין עדיין נתוני מדדים נוספים.")
else:
    for category, group in additional.groupby("category", sort=False):
        st.markdown(f"**{category}**")
        display = group[["display_name", "value", "as_of_date"]].rename(
            columns={"display_name": "מדד", "value": "ערך אחרון", "as_of_date": "נכון ל"}
        )
        display["ערך אחרון"] = display["ערך אחרון"].fillna("אין נתונים עדיין")
        display["נכון ל"] = display["נכון ל"].fillna("—")
        st.dataframe(display, hide_index=True, use_container_width=True)

st.divider()

# --- Sector rotation -------------------------------------------------------
st.subheader("רוטציית סקטורים (יחסית ל-SPY)")
lookback = st.select_slider("טווח תצפית (ימים)", options=[5, 21, 63, 126], value=21)
sectors = load_sector_rotation(lookback_days=lookback)
if sectors.empty:
    st.caption("אין עדיין נתוני ETF סקטוריאליים — התזמון שואב אותם יחד עם רשימת המעקב.")
else:
    fig = go.Figure()
    colors = ["#2f6fed" if v >= 0 else "#d64545" for v in sectors["relative_strength_vs_spy"].fillna(0)]
    fig.add_trace(
        go.Bar(
            x=sectors["sector"], y=sectors["relative_strength_vs_spy"],
            marker_color=colors, name="עוצמה יחסית מול SPY (נ.א.)",
        )
    )
    fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        sectors.rename(
            columns={
                "sector": "סקטור", "return_pct": "תשואה %",
                "relative_strength_vs_spy": "עוצמה יחסית מול SPY (נ.א.)",
                "volume_vs_avg_pct": "נפח מול ממוצע 20 יום %",
            }
        ),
        hide_index=True, use_container_width=True,
    )
    st.caption("תיאורי בלבד — ביצועים יחסיים ופרוקסי נפח, לא תחזית.")
