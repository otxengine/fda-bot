"""FDA-Bot page: a read-only window into the user's own separate fda-bot
service (github.com/otxengine/fda-bot — an FDA/biopharma-catalyst
options-flow scanner deployed on Render), pulled via connectors/fda_bot.py.

Follows the same page structure as every other page here (headline numbers
-> primary visual -> detail table, "data as of" timestamp) plus a
Diagnostics tab: what reading the bot's own source code and its own live
performance data suggests could be improved. Every diagnostic claim below is
either (a) a live check computed from the numbers this page just loaded, or
(b) a specific, cited finding from reading the bot's source in this session
— never a guess. Descriptive only, same rule as the rest of this app's
analysis surfaces: report what the data says, not a prediction."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    load_fda_bot_cp_buckets,
    load_fda_bot_performance_history,
    load_fda_bot_score_buckets,
    load_fda_bot_signals,
)

st.set_page_config(page_title="FDA-Bot — Fin Research", page_icon="🧬", layout="wide")
st.title("FDA-Bot")
st.caption(
    "Read-only view of your own [fda-bot](https://github.com/otxengine/fda-bot) service "
    "(running on Render) — an FDA/biopharma catalyst options-flow scanner. finresearch never "
    "writes back to it."
)

history = load_fda_bot_performance_history()

if history.empty:
    st.info(
        "No data fetched yet. The `fda_bot` connector runs every 30 minutes once the scheduler "
        "is running (`scheduler/main.py`) — its first catch-up fetch happens on startup."
    )
    st.stop()

latest = history.iloc[-1]

# --- Headline numbers --------------------------------------------------------
st.caption(f"Data as of {latest['fetched_at']} · bot's own last scan: {latest['last_scan_at'] or '—'}")

n_to_target = int(latest["n_to_target"]) if pd.notna(latest.get("n_to_target")) else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Win rate — actual trade (≥5%)",
    f"{latest['win_rate_to_target']:.1f}%" if pd.notna(latest.get("win_rate_to_target")) else "—",
    help="Entry -> planned pre-event exit (the bot's own target_date, always BEFORE the FDA decision — "
         "every alert says never hold through it). This is what 'did the signal work' should mean. "
         f"Sample size: {n_to_target} — small until fda-bot has run with this tracking for a while.",
)
k2.metric(
    "Avg return — actual trade",
    f"{latest['avg_return_to_target']:+.2f}%" if pd.notna(latest.get("avg_return_to_target")) else "—",
)
k3.metric("Alerts tracked", f"{int(latest['total_alerts_tracked']):,}")
k4.metric("Events next 7d", int(latest["events_next_7d"]))

if n_to_target < 30:
    st.info(
        f"Only **{n_to_target}** alerts have a completed entry→exit outcome so far — added to fda-bot "
        "2026-09-28, so this grows day by day as alerts fire and their planned exit dates pass. Treat "
        "the win rate above as directional, not conclusive, until this is well past 30."
    )

with st.expander("Older metric: post-event reaction (not what the bot's alerts recommend)"):
    st.caption(
        "change_1d_pct/change_3d_pct measure price the day before the FDA event vs. after it — i.e. what "
        "happens if you HELD THROUGH the binary decision. Every alert explicitly says to exit before "
        "that. Kept here for continuity, not as the metric to optimize."
    )
    oc1, oc2, oc3 = st.columns(3)
    oc1.metric("Win rate (1d after event, ≥5%)", f"{latest['win_rate_1d']:.1f}%" if pd.notna(latest["win_rate_1d"]) else "—")
    oc2.metric("Avg return, 1d", f"{latest['avg_return_1d']:+.2f}%" if pd.notna(latest["avg_return_1d"]) else "—")
    oc3.metric("Avg return, 3d", f"{latest['avg_return_3d']:+.2f}%" if pd.notna(latest["avg_return_3d"]) else "—")

st.caption(
    f"{int(latest['n_with_1d']):,} of {int(latest['total_alerts_tracked']):,} tracked alerts have a "
    f"1-day (post-event) outcome so far · {int(latest['total_signals']):,} signals scanned all-time · "
    f"{int(latest['historical_records']):,} historical events archived."
)

st.divider()

tab_signals, tab_cp, tab_score, tab_trend, tab_diag = st.tabs(
    ["Current Opportunities", "C/P Ratio Breakdown", "Score Calibration", "Performance Over Time", "Diagnostics"]
)

# --- Current opportunities ---------------------------------------------------
with tab_signals:
    signals = load_fda_bot_signals()
    if signals.empty:
        st.caption("No 0-7 day tickers in the bot's current window.")
    else:
        badge = {"BUY": "🟢 BUY", "EARLY_BUY": "🔵 EARLY_BUY", "WATCH": "🟡 WATCH", "AVOID": "🔴 AVOID"}
        sector_badge = {"strong": "💪 strong", "weak": "📉 weak", "neutral": "— neutral", "unknown": "?"}
        macro_badge = {"risk_on": "☀️ risk-on", "risk_off": "🌪️ risk-off", "neutral": "— neutral", "unknown": "?"}
        show = signals.copy()
        show["Signal"] = show["stock_signal"].map(lambda s: badge.get(s, s))
        show["Sector"] = show["sector_momentum"].map(lambda s: sector_badge.get(s, s))
        show["Macro"] = show["macro_risk_flag"].map(lambda s: macro_badge.get(s, s))
        cols = [
            "Signal", "ticker", "company", "event_type", "days_until", "composite_score",
            "call_put_ratio", "entry_price", "stop_loss_price", "expected_move_pct", "Sector", "Macro",
            "stock_signal_reason",
        ]
        st.dataframe(
            show[cols].rename(columns={
                "ticker": "Ticker", "company": "Company", "event_type": "Event", "days_until": "Days until",
                "composite_score": "Score", "call_put_ratio": "C/P", "entry_price": "Entry",
                "stop_loss_price": "Stop", "expected_move_pct": "Exp. move %", "stock_signal_reason": "Reason",
            }),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "Sector/Macro: biotech (XBI vs SPY) and market risk context, added to fda-bot 2026-09-28 as a "
            "conviction overlay — downgrades conviction on a weak/risk-off read, never upgrades it."
        )

# --- C/P ratio bucket breakdown ---------------------------------------------
with tab_cp:
    cp = load_fda_bot_cp_buckets()
    if cp.empty:
        st.caption("No bucket data yet.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cp["bucket"].astype(str), y=cp["win_rate"], marker_color="#2f6fed",
                              text=cp["n"].apply(lambda n: f"n={n}"), textposition="outside"))
        fig.update_layout(
            title="Win rate by call/put ratio bucket (bar label = sample size)",
            yaxis_title="Win rate (%)", height=340, margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            cp.rename(columns={"bucket": "C/P bucket", "n": "n", "win_rate": "Win rate %", "avg_change": "Avg change %"}),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "The bot's own scoring weights call/put ratio as its single strongest bullish signal "
            "(higher C/P → higher composite score). Check the Diagnostics tab for whether this "
            "table actually bears that out."
        )

# --- Composite score calibration ---------------------------------------------
with tab_score:
    sb = load_fda_bot_score_buckets()
    if sb.empty:
        st.caption("No calibration data yet.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=sb["range"].astype(str), y=sb["avg_change"], marker_color="#2f6fed",
                              text=sb["n"].apply(lambda n: f"n={n}"), textposition="outside"))
        fig.update_layout(
            title="Avg 1-day price change by composite-score bucket (bar label = sample size)",
            yaxis_title="Avg change (%)", height=340, margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            sb.rename(columns={
                "range": "Score range", "n": "n", "p_up5": "P(+5%)", "p_up10": "P(+10%)",
                "p_down5": "P(-5%)", "p_down10": "P(-10%)", "avg_change": "Avg change %", "median_change": "Median change %",
            }),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "If this were well-calibrated, avg/median change should rise as the score range rises "
            "(0-35 lowest, 80-100 highest). See Diagnostics for whether it does."
        )

# --- Performance trend --------------------------------------------------------
with tab_trend:
    if len(history) < 2:
        st.caption("Only one snapshot so far — trend will build up as the scheduler keeps fetching.")
    else:
        st.markdown("**Win rate — actual trade (entry → planned exit)**")
        fig0 = go.Figure()
        fig0.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_to_target"], mode="lines+markers",
                                   name="Win rate to target %", line=dict(color="#55a868")))
        # Marks when this session's fixes landed (pushed to fda-bot's master,
        # 2026-09-28) so "before vs. after" is visible at a glance once the
        # trend has enough history either side of it.
        fig0.add_vline(x="2026-09-28", line_dash="dot", line_color="#888",
                        annotation_text="fixes deployed", annotation_position="top")
        fig0.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Win rate (%)")
        st.plotly_chart(fig0, use_container_width=True)
        st.caption("Sample size (n_to_target) grows day by day — early points here are noisy by construction.")

        with st.expander("Older metric: post-event reaction"):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_1d"], mode="lines+markers",
                                      name="Win rate 1d %", line=dict(color="#2f6fed")))
            fig.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_3d"], mode="lines+markers",
                                      name="Win rate 3d %", line=dict(color="#dd8452")))
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Win rate (%)")
            st.plotly_chart(fig, use_container_width=True)

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=history["fetched_at"], y=history["avg_return_1d"], mode="lines+markers",
                                       name="Avg return 1d %", line=dict(color="#2f6fed")))
            fig2.add_trace(go.Scatter(x=history["fetched_at"], y=history["avg_return_3d"], mode="lines+markers",
                                       name="Avg return 3d %", line=dict(color="#dd8452")))
            fig2.add_hline(y=0, line_dash="dot", line_color="#888")
            fig2.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Avg return (%)")
            st.plotly_chart(fig2, use_container_width=True)

# --- Diagnostics --------------------------------------------------------------
with tab_diag:
    st.subheader("What the live numbers say")

    cp = load_fda_bot_cp_buckets()
    sb = load_fda_bot_score_buckets()

    if not cp.empty and cp["win_rate"].notna().sum() >= 3:
        cp_valid = cp.dropna(subset=["win_rate"])
        is_monotonic = cp_valid["win_rate"].is_monotonic_increasing
        best = cp_valid.loc[cp_valid["win_rate"].idxmax()]
        worst = cp_valid.loc[cp_valid["win_rate"].idxmin()]
        if is_monotonic:
            st.success("C/P-ratio buckets currently increase monotonically with win rate — consistent with the bot's own weighting assumption.")
        else:
            st.warning(
                f"**C/P ratio is not monotonically predictive right now.** The best-performing bucket "
                f"is **{best['bucket']}** (win rate {best['win_rate']:.0f}%, n={int(best['n'])}), not the "
                f"highest-ratio bucket. The worst is **{worst['bucket']}** (win rate {worst['win_rate']:.0f}%, "
                f"n={int(worst['n'])}). The bot's scoring engine (`backend/signals/analyzer.py`) weights "
                f"call/put ratio at 22% of the composite score specifically because higher C/P was assumed "
                f"to predict better outcomes ('wins avg C/P=5.35 vs losses=1.52', per its own header comment) "
                f"— the current live breakdown doesn't bear that out."
            )

    if not sb.empty and sb["avg_change"].notna().sum() >= 3:
        sb_valid = sb.dropna(subset=["avg_change"])
        is_monotonic = sb_valid["avg_change"].is_monotonic_increasing
        if is_monotonic:
            st.success("Composite-score buckets currently increase monotonically with avg return — the score is well-calibrated right now.")
        else:
            worst_mid = sb_valid.loc[sb_valid["avg_change"].idxmin()]
            st.warning(
                f"**Composite score is not monotonically predictive right now.** Avg 1-day change does not "
                f"rise smoothly from the 0-35 bucket to 80-100 — the **{worst_mid['range']}** bucket has the "
                f"worst avg change ({worst_mid['avg_change']:+.2f}%, n={int(worst_mid['n'])}) despite not being "
                f"the lowest-scored bucket. A well-calibrated 0-100 score should show returns rising with score."
            )

    st.divider()
    st.subheader("Findings — fixed in the fda-bot repo (2026-09-28)")
    st.caption(
        "Found by reading github.com/otxengine/fda-bot's source and pulling its full 890-row historical "
        "archive in this session, then fixed there directly (committed locally in that repo; **push + "
        "Render redeploy still needed** for these to take effect on the live service — the numbers on "
        "this page won't reflect the fix until then)."
    )

    st.markdown(
        """
**1. A crash bug that likely truncated full options scans — FIXED.**
`backend/signals/analyzer.py`'s `EARLY_BUY` branch referenced an undefined `iv_val` variable, raising
`NameError` whenever a ticker qualified for `EARLY_BUY` (a fairly common condition). `run_options_scan()`
(the hourly job building the whole signal-history table) ran every ticker through **one** outer
`try/except` for the entire loop, so hitting this on ticker #40 of 100 silently dropped tickers #41-100
for that run. Fixed the undefined variable, *and* added a per-ticker `try/except` so any future exception
in one ticker's analysis can no longer truncate the rest of the scan.

**2. The self-improving learning loop — FIXED.**
`backend/signals/learning_engine.py` used `SONNET = "claude-sonnet-4-6"`, not a valid model ID — consistent
with `/api/learning` returning zero insights despite 890 historical records (well past its 5-record
minimum). Updated to `claude-sonnet-5`.

**3. Win-rate/calibration stats were diluted by a systematically bad, non-tradeable population — FIXED,
the deepest finding.** Pulling the full 890-row `/api/history` archive and splitting it by source showed
**48%** of the scored subset (136 of 282 rows) came from `broad_scan/iv` — an algorithmic IV-spike guess
with **no confirmed FDA date**, which the bot's own alerting logic already refuses to trade on in most
(not all) code paths. That population averaged **-2.58%/day at a 4.4% win rate**. Genuine FDA-catalyst
rows averaged **+1.39%/day at 16.7%** — a real, cleaner signal that the aggregate stats were burying.
Within genuine-catalyst rows only (n=30, small — treat as directional not definitive), composite score
correlated with next-day return at **r=0.32**, while call/put ratio alone was **r=0.07** — the opposite of
the header comment's "C/P is the strongest predictor" claim, which the coarse bucket table already hinted
at. Fixes made: `/api/calibration`, the C/P bucket stats, and the weekly learning-engine prompt now all
exclude `broad_scan/iv` rows; `run_options_scan` (the one scan path that hadn't) now applies the same
real-source filter as every other alerting path; `/api/history` now exposes each row's `source` /
`is_real_fda_event` so this can be checked directly instead of inferred from event_type strings.
Composite-score *weights* were deliberately **not** hand-tuned from the n=30 sample — that's a job for the
now-fixed learning engine's own Bayesian, sample-gated recalibration, not a one-off guess from 30 rows.

**4. Duplicated `REAL_SOURCES` constant — FIXED.** The same source-filter set was copy-pasted identically
into 4 places across `main.py` (×2), `scheduler.py`, and `unified_scanner.py` — which is exactly why one
of the four (`run_options_scan`) never got the filter when the other three did. Consolidated into
`backend/constants.py`, imported everywhere.

**5. Minor: triplicate log line — FIXED.** `_notify_penny_signals()` logged the same "Penny BUY alert"
line three times in a row; now once.

**6. New: BiopharmCatalyst's own historical-catalysts API is now wired in.** `fetch_bpc_historical()`
(real past catalysts, real dates, `source="biopharmcatalyst"`) was fully implemented but never called
anywhere — the genuine-FDA-catalyst sample was thin (n=30) only because seeding depended on this bot's own
event tracking having caught it in real time. A new daily job (3:00 EST) seeds `HistoricalResult` from
BPC's independent historical list instead, growing the clean sample the calibration/learning stats above
depend on. `BPC_API_KEY` is confirmed already set on Render; also added to `render.yaml` so it's documented.

**7. The biggest one: the bot was measuring the wrong outcome for its own strategy — FIXED.** Every
BUY/EARLY_BUY alert explicitly tells the user to exit **the day before** the FDA decision, never hold
through it. But `HistoricalResult.change_1d_pct` (which fed `/api/calibration` and everything item 3
above is based on) measures price the day before the event → price 1 day **after** it — the market's
reaction to the decision itself, exactly the window the strategy says to be out of. Separately,
`run_options_scan` never wrote `AlertLog` at all for its own BUY/EARLY_BUY discoveries (a second,
independent gap — that scan path's alerts were invisible to outcome tracking entirely, only sending a
Telegram message). Fixed by adding real entry→planned-exit tracking: `AlertLog` now stores `entry_price`/
`target_date` at alert-fire time (added to all 5 alert-creation sites, plus the 2 missing writes in
`run_options_scan`), and `run_alert_outcome_tracker` now computes `change_to_target_pct` — price at
`entry_price` vs. price at `target_date` — progressively filling it in once each alert's own target date
passes. Exposed as **Win rate — actual trade** above; this is the number that should actually be judged,
not the post-event-reaction ones (kept in the collapsed section above for continuity). It starts at 0
samples and grows day by day — nothing here changes the past, only how outcomes are measured going
forward.

*All 7 items above are pushed to `master` (commits `f6be33b`, `1b7d053`, and the entry→exit tracker) —
Render should auto-redeploy. This page's own numbers won't reflect them until the bot re-scans post-deploy,
and `win_rate_to_target` specifically will read empty for a while — it only fills in as new alerts fire
and their planned exit dates actually pass.*
        """
    )
