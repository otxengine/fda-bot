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

st.set_page_config(page_title="Macro — Fin Research", page_icon="📈", layout="wide")
st.title("Macro")

st.caption(
    "🟢/🔴 below = when we last **fetched** from each source (connector health). "
    "That's different from how current the **underlying data** is — economic data "
    "has its own publication lag (e.g. CPI for a month is released weeks later, "
    "GDP is quarterly). Each indicator below shows its own **'as of'** date so you "
    "can tell the two apart at a glance."
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
    st.info("No macro regime computed yet — run the scheduler at least once (see run.bat).")
elif regime["indicators_with_data"] == 0:
    # No confident-looking label from zero real inputs — a specific badge
    # (e.g. "Late-cycle") computed from an all-missing-data default of 0.0
    # is exactly the false-precision failure mode the plan warns against.
    # Found via live end-to-end testing with FRED/BLS/BEA disabled.
    st.warning(
        "**Insufficient data** — 0 of the macro indicators have any data yet "
        "(needs FRED_API_KEY at minimum; see .env.example). No regime label "
        "is shown until at least one indicator reports a value."
    )
else:
    badge_col, detail_col = st.columns([1, 2])
    with badge_col:
        st.metric("Macro Regime", regime["regime_label"])
        st.caption(
            f"Computed {regime['computed_at']} — heuristic label, not a forecast. "
            f"Based on {regime['indicators_with_data']}/{regime['total_indicators']} indicators "
            f"({regime['coverage']:.0%} coverage)."
        )
        if regime["coverage"] < 1.0:
            st.caption("⚠️ Partial data — missing indicators are excluded from the average, not treated as neutral.")
    with detail_col:
        cat_cols = st.columns(len(regime["category_scores"]))
        for col, (cat, score) in zip(cat_cols, regime["category_scores"].items()):
            if score is None:
                col.metric(cat.title(), "No data")
                continue
            light = "🟢" if score > 0.25 else ("🔴" if score < -0.25 else "🟡")
            col.metric(cat.title(), f"{light} {score:+.2f}")

    with st.expander("Why this label? (the rule table, per-indicator)"):
        for category, indicators in regime["details"].items():
            st.markdown(f"**{category.title()}**")
            rows = [
                {
                    "Indicator": d["display_name"],
                    "Value": d["value"],
                    "Transform": d["transform"],
                    "Light": {1: "🟢", 0: "🟡", -1: "🔴"}[d["light"]],
                    "Data as of": d.get("as_of_date", "—"),
                }
                for d in indicators
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    history = load_regime_history()
    if not history.empty:
        st.caption("Regime label over time")
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=history["computed_at"], y=history["regime_label"], mode="markers+lines",
                line=dict(color="#2f6fed"), name="Regime",
            )
        )
        fig.update_layout(height=200, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Indicator trend charts (one axis each; level + momentum) ------------
st.subheader("Indicators")
thresholds = load_thresholds()
for category, cat_cfg in thresholds["categories"].items():
    st.markdown(f"**{category.title()}**")
    ind_cols = st.columns(len(cat_cfg["indicators"]))
    for col, (key, ind_cfg) in zip(ind_cols, cat_cfg["indicators"].items()):
        series = load_indicator_series(ind_cfg["series_id"])
        with col:
            if series.empty:
                st.caption(f"{ind_cfg['display_name']}: no data yet")
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
                st.caption(f"Latest ({ind_cfg.get('transform', 'level')}): {latest_transformed:.2f} — **data as of {as_of}**")

st.divider()

# --- Additional indicators (not part of the regime score) ------------------
# Broader coverage added after live use surfaced that the original 9-series
# regime set left the chat with nothing to say about consumption, production,
# housing, sentiment, or trade — see connectors/fred.py's ADDITIONAL_SERIES.
st.subheader("Additional Indicators")
st.caption("Broader macro coverage — not part of the regime score above, just extra context.")
additional = load_additional_indicators()
if additional.empty:
    st.caption("No additional-indicator data yet.")
else:
    for category, group in additional.groupby("category", sort=False):
        st.markdown(f"**{category}**")
        display = group[["display_name", "value", "as_of_date"]].rename(
            columns={"display_name": "Indicator", "value": "Latest Value", "as_of_date": "Data as of"}
        )
        display["Latest Value"] = display["Latest Value"].fillna("no data yet")
        display["Data as of"] = display["Data as of"].fillna("—")
        st.dataframe(display, hide_index=True, use_container_width=True)

st.divider()

# --- Sector rotation -------------------------------------------------------
st.subheader("Sector rotation (relative to SPY)")
lookback = st.select_slider("Lookback (days)", options=[5, 21, 63, 126], value=21)
sectors = load_sector_rotation(lookback_days=lookback)
if sectors.empty:
    st.caption("No sector ETF data yet — the scheduler fetches these alongside the watchlist.")
else:
    fig = go.Figure()
    colors = ["#2f6fed" if v >= 0 else "#d64545" for v in sectors["relative_strength_vs_spy"].fillna(0)]
    fig.add_trace(
        go.Bar(
            x=sectors["sector"], y=sectors["relative_strength_vs_spy"],
            marker_color=colors, name="Relative strength vs SPY (pp)",
        )
    )
    fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        sectors.rename(
            columns={
                "sector": "Sector", "return_pct": "Return %",
                "relative_strength_vs_spy": "Relative Strength vs SPY (pp)",
                "volume_vs_avg_pct": "Volume vs 20d Avg %",
            }
        ),
        hide_index=True, use_container_width=True,
    )
    st.caption("Descriptive only — relative performance and a volume proxy, not a prediction.")
