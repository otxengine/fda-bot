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
from dashboard.rtl import apply_rtl

apply_rtl()
st.title("בוט FDA")
st.caption(
    "מבט לקריאה בלבד על שירות ה-[fda-bot](https://github.com/otxengine/fda-bot) הנפרד שלכם "
    "(רץ על Render) — סורק זרימת אופציות לקטליזטורים ביו-פארמה/FDA. finresearch אף פעם לא "
    "כותב חזרה אליו."
)

history = load_fda_bot_performance_history()

if history.empty:
    st.info(
        "טרם נשאבו נתונים. המחבר `fda_bot` רץ כל 30 דקות ברגע שהתזמון פועל "
        "(`scheduler/main.py`) — שאיבת ה-catch-up הראשונה מתרחשת עם ההפעלה."
    )
    st.stop()

latest = history.iloc[-1]

# --- Headline numbers --------------------------------------------------------
st.caption(f"נתונים נכונים ל-{latest['fetched_at']} · הסריקה האחרונה של הבוט: {latest['last_scan_at'] or '—'}")

n_to_target = int(latest["n_to_target"]) if pd.notna(latest.get("n_to_target")) else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "אחוז הצלחה — עסקה בפועל (≥5%)",
    f"{latest['win_rate_to_target']:.1f}%" if pd.notna(latest.get("win_rate_to_target")) else "—",
    help="כניסה ← יציאה מתוכננת לפני האירוע (ה-target_date של הבוט עצמו, תמיד לפני החלטת "
         "ה-FDA — כל התראה אומרת לעולם לא להחזיק דרך ההחלטה). זו המשמעות האמיתית של "
         f"'האם האיתות עבד'. גודל מדגם: {n_to_target} — קטן עד שה-fda-bot ירוץ עם המעקב הזה זמן מה.",
)
k2.metric(
    "תשואה ממוצעת — עסקה בפועל",
    f"{latest['avg_return_to_target']:+.2f}%" if pd.notna(latest.get("avg_return_to_target")) else "—",
)
k3.metric("התראות במעקב", f"{int(latest['total_alerts_tracked']):,}")
k4.metric("אירועים ב-7 הימים הקרובים", int(latest["events_next_7d"]))

if n_to_target < 30:
    st.info(
        f"רק ל-**{n_to_target}** התראות יש עד כה תוצאת כניסה→יציאה מלאה — נוסף ל-fda-bot "
        "ב-2026-09-28, כך שהמספר גדל יום אחר יום ככל שהתראות מופעלות ותאריכי היציאה "
        "המתוכננים שלהן חולפים. התייחסו לאחוז ההצלחה למעלה ככיווני בלבד, לא סופי, עד "
        "שהמספר יעבור בהרבה את ה-30."
    )

with st.expander("מדד ישן יותר: תגובת שוק לאחר האירוע (לא מה שההתראות של הבוט ממליצות)"):
    st.caption(
        "change_1d_pct/change_3d_pct מודדים מחיר יום לפני אירוע ה-FDA מול אחריו — כלומר מה "
        "קורה אם **החזקתם דרך** ההחלטה הבינארית. כל התראה אומרת במפורש לצאת לפני כן. "
        "נשמר כאן לצורך רציפות, לא כמדד לאופטימיזציה."
    )
    oc1, oc2, oc3 = st.columns(3)
    oc1.metric("אחוז הצלחה (יום אחרי האירוע, ≥5%)", f"{latest['win_rate_1d']:.1f}%" if pd.notna(latest["win_rate_1d"]) else "—")
    oc2.metric("תשואה ממוצעת, יום אחד", f"{latest['avg_return_1d']:+.2f}%" if pd.notna(latest["avg_return_1d"]) else "—")
    oc3.metric("תשואה ממוצעת, 3 ימים", f"{latest['avg_return_3d']:+.2f}%" if pd.notna(latest["avg_return_3d"]) else "—")

st.caption(
    f"ל-{int(latest['n_with_1d']):,} מתוך {int(latest['total_alerts_tracked']):,} התראות במעקב יש עד כה "
    f"תוצאה של יום אחד (לאחר האירוע) · {int(latest['total_signals']):,} איתותים נסרקו מאז ומעולם · "
    f"{int(latest['historical_records']):,} אירועים היסטוריים בארכיון."
)

st.divider()

tab_signals, tab_cp, tab_score, tab_trend, tab_diag = st.tabs(
    ["הזדמנויות נוכחיות", "פילוח יחס C/P", "כיול הציון", "ביצועים לאורך זמן", "אבחון"]
)

# --- Current opportunities ---------------------------------------------------
with tab_signals:
    signals = load_fda_bot_signals()
    if signals.empty:
        st.caption("אין טיקרים בחלון 0-7 הימים הנוכחי של הבוט.")
    else:
        badge = {"BUY": "🟢 קנייה", "EARLY_BUY": "🔵 קנייה מוקדמת", "WATCH": "🟡 מעקב", "AVOID": "🔴 הימנעות"}
        sector_badge = {"strong": "💪 חזק", "weak": "📉 חלש", "neutral": "— ניטרלי", "unknown": "?"}
        macro_badge = {"risk_on": "☀️ risk-on", "risk_off": "🌪️ risk-off", "neutral": "— ניטרלי", "unknown": "?"}
        show = signals.copy()
        show["איתות"] = show["stock_signal"].map(lambda s: badge.get(s, s))
        show["סקטור"] = show["sector_momentum"].map(lambda s: sector_badge.get(s, s))
        show["מאקרו"] = show["macro_risk_flag"].map(lambda s: macro_badge.get(s, s))
        cols = [
            "איתות", "ticker", "company", "event_type", "days_until", "composite_score",
            "call_put_ratio", "entry_price", "stop_loss_price", "expected_move_pct", "סקטור", "מאקרו",
            "stock_signal_reason",
        ]
        st.dataframe(
            show[cols].rename(columns={
                "ticker": "טיקר", "company": "חברה", "event_type": "אירוע", "days_until": "ימים עד",
                "composite_score": "ציון", "call_put_ratio": "C/P", "entry_price": "כניסה",
                "stop_loss_price": "סטופ", "expected_move_pct": "תנועה צפויה %", "stock_signal_reason": "סיבה",
            }),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "סקטור/מאקרו: הקשר ביוטק (XBI מול SPY) וסיכון שוק, נוסף ל-fda-bot ב-2026-09-28 כשכבת "
            "ביטחון — מוריד רמת ביטחון בקריאת חולשה/risk-off, לעולם לא מעלה אותה."
        )

# --- C/P ratio bucket breakdown ---------------------------------------------
with tab_cp:
    cp = load_fda_bot_cp_buckets()
    if cp.empty:
        st.caption("אין עדיין נתוני קבוצות (buckets).")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cp["bucket"].astype(str), y=cp["win_rate"], marker_color="#2f6fed",
                              text=cp["n"].apply(lambda n: f"n={n}"), textposition="outside"))
        fig.update_layout(
            title="אחוז הצלחה לפי קבוצת יחס call/put (תווית העמודה = גודל מדגם)",
            yaxis_title="אחוז הצלחה (%)", height=340, margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            cp.rename(columns={"bucket": "קבוצת C/P", "n": "n", "win_rate": "אחוז הצלחה %", "avg_change": "שינוי ממוצע %"}),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "מנגנון הניקוד של הבוט עצמו מייחס ליחס call/put את האיתות השורי החזק ביותר "
            "(C/P גבוה יותר ← ציון מצרפי גבוה יותר). בדקו בלשונית האבחון אם הטבלה הזו "
            "אכן תומכת בכך."
        )

# --- Composite score calibration ---------------------------------------------
with tab_score:
    sb = load_fda_bot_score_buckets()
    if sb.empty:
        st.caption("אין עדיין נתוני כיול.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=sb["range"].astype(str), y=sb["avg_change"], marker_color="#2f6fed",
                              text=sb["n"].apply(lambda n: f"n={n}"), textposition="outside"))
        fig.update_layout(
            title="שינוי מחיר ממוצע ביום הראשון לפי קבוצת ציון מצרפי (תווית העמודה = גודל מדגם)",
            yaxis_title="שינוי ממוצע (%)", height=340, margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            sb.rename(columns={
                "range": "טווח ציון", "n": "n", "p_up5": "P(+5%)", "p_up10": "P(+10%)",
                "p_down5": "P(-5%)", "p_down10": "P(-10%)", "avg_change": "שינוי ממוצע %", "median_change": "שינוי חציוני %",
            }),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "אם זה היה מכויל היטב, השינוי הממוצע/החציוני היה אמור לעלות ככל שטווח הציון עולה "
            "(0-35 הנמוך ביותר, 80-100 הגבוה ביותר). ראו בלשונית האבחון אם זה אכן המצב."
        )

# --- Performance trend --------------------------------------------------------
with tab_trend:
    if len(history) < 2:
        st.caption("רק תצפית אחת עד כה — המגמה תיבנה ככל שהתזמון ימשיך לשאוב.")
    else:
        st.markdown("**אחוז הצלחה — עסקה בפועל (כניסה ← יציאה מתוכננת)**")
        fig0 = go.Figure()
        fig0.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_to_target"], mode="lines+markers",
                                   name="אחוז הצלחה ליעד %", line=dict(color="#55a868")))
        # Marks when this session's fixes landed (pushed to fda-bot's master,
        # 2026-09-28) so "before vs. after" is visible at a glance once the
        # trend has enough history either side of it.
        fig0.add_vline(x="2026-09-28", line_dash="dot", line_color="#888",
                        annotation_text="תיקונים הופצו", annotation_position="top")
        fig0.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="אחוז הצלחה (%)")
        st.plotly_chart(fig0, use_container_width=True)
        st.caption("גודל המדגם (n_to_target) גדל יום אחר יום — הנקודות המוקדמות כאן רועשות מעצם טבען.")

        with st.expander("מדד ישן יותר: תגובת שוק לאחר האירוע"):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_1d"], mode="lines+markers",
                                      name="אחוז הצלחה יום 1 %", line=dict(color="#2f6fed")))
            fig.add_trace(go.Scatter(x=history["fetched_at"], y=history["win_rate_3d"], mode="lines+markers",
                                      name="אחוז הצלחה יום 3 %", line=dict(color="#dd8452")))
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="אחוז הצלחה (%)")
            st.plotly_chart(fig, use_container_width=True)

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=history["fetched_at"], y=history["avg_return_1d"], mode="lines+markers",
                                       name="תשואה ממוצעת יום 1 %", line=dict(color="#2f6fed")))
            fig2.add_trace(go.Scatter(x=history["fetched_at"], y=history["avg_return_3d"], mode="lines+markers",
                                       name="תשואה ממוצעת יום 3 %", line=dict(color="#dd8452")))
            fig2.add_hline(y=0, line_dash="dot", line_color="#888")
            fig2.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="תשואה ממוצעת (%)")
            st.plotly_chart(fig2, use_container_width=True)

# --- Diagnostics --------------------------------------------------------------
with tab_diag:
    st.subheader("מה הנתונים החיים אומרים")

    cp = load_fda_bot_cp_buckets()
    sb = load_fda_bot_score_buckets()

    if not cp.empty and cp["win_rate"].notna().sum() >= 3:
        cp_valid = cp.dropna(subset=["win_rate"])
        is_monotonic = cp_valid["win_rate"].is_monotonic_increasing
        best = cp_valid.loc[cp_valid["win_rate"].idxmax()]
        worst = cp_valid.loc[cp_valid["win_rate"].idxmin()]
        if is_monotonic:
            st.success("קבוצות יחס C/P עולות כרגע באופן מונוטוני עם אחוז ההצלחה — עקבי עם הנחת המשקל של הבוט עצמו.")
        else:
            st.warning(
                f"**יחס C/P אינו מנבא באופן מונוטוני כרגע.** הקבוצה עם הביצועים הטובים ביותר "
                f"היא **{best['bucket']}** (אחוז הצלחה {best['win_rate']:.0f}%, n={int(best['n'])}), לא "
                f"קבוצת היחס הגבוה ביותר. הגרועה ביותר היא **{worst['bucket']}** (אחוז הצלחה {worst['win_rate']:.0f}%, "
                f"n={int(worst['n'])}). מנוע הניקוד של הבוט (`backend/signals/analyzer.py`) מייחס "
                f"ליחס call/put משקל של 22% מהציון המצרפי, במיוחד כי הונח שיחס C/P גבוה יותר "
                f"ינבא תוצאות טובות יותר ('wins avg C/P=5.35 vs losses=1.52', לפי הערת הכותרת של הקוד עצמו) "
                f"— הפילוח החי הנוכחי לא תומך בכך."
            )

    if not sb.empty and sb["avg_change"].notna().sum() >= 3:
        sb_valid = sb.dropna(subset=["avg_change"])
        is_monotonic = sb_valid["avg_change"].is_monotonic_increasing
        if is_monotonic:
            st.success("קבוצות הציון המצרפי עולות כרגע באופן מונוטוני עם התשואה הממוצעת — הציון מכויל היטב כרגע.")
        else:
            worst_mid = sb_valid.loc[sb_valid["avg_change"].idxmin()]
            st.warning(
                f"**הציון המצרפי אינו מנבא באופן מונוטוני כרגע.** השינוי הממוצע ביום הראשון אינו "
                f"עולה בצורה חלקה מקבוצת 0-35 עד 80-100 — לקבוצה **{worst_mid['range']}** יש את "
                f"השינוי הממוצע הגרוע ביותר ({worst_mid['avg_change']:+.2f}%, n={int(worst_mid['n'])}) למרות שאינה "
                f"הקבוצה עם הציון הנמוך ביותר. ציון 0-100 מכויל היטב אמור להראות תשואות עולות עם הציון."
            )

    st.divider()
    st.subheader("ממצאים — תוקנו במאגר (repo) של fda-bot (2026-09-28)")
    st.caption(
        "נמצאו על ידי קריאת הקוד של github.com/otxengine/fda-bot ושאיבת הארכיון ההיסטורי המלא שלו "
        "(890 שורות) בסשן זה, ואז תוקנו שם ישירות (הוגשו (committed) מקומית באותו מאגר; **עדיין נדרשים "
        "push ופריסה מחדש ב-Render** כדי שהתיקונים ייכנסו לתוקף בשירות החי — המספרים בעמוד זה לא "
        "ישקפו את התיקון עד אז)."
    )

    st.markdown(
        """
**1. באג קריסה שכנראה קטע סריקות אופציות מלאות — תוקן.**
ענף ה-`EARLY_BUY` ב-`backend/signals/analyzer.py` הפנה למשתנה לא מוגדר `iv_val`, מה שגרם ל-
`NameError` בכל פעם שטיקר הכשיר ל-`EARLY_BUY` (תנאי שכיח למדי). `run_options_scan()`
(המשימה השעתית שבונה את כל טבלת היסטוריית האיתותים) הריצה כל טיקר דרך `try/except` חיצוני
**אחד** עבור כל הלולאה, כך שפגיעה בבאג הזה בטיקר מס' 40 מתוך 100 מחקה בשקט את הטיקרים
41-100 באותה ריצה. תוקן המשתנה הלא-מוגדר, *וגם* נוסף `try/except` לכל טיקר בנפרד כך שחריגה
עתידית בניתוח טיקר אחד לא תוכל עוד לקטוע את שאר הסריקה.

**2. לולאת הלמידה המשתפרת מעצמה — תוקנה.**
`backend/signals/learning_engine.py` השתמש ב-`SONNET = "claude-sonnet-4-6"`, שאינו מזהה מודל
תקף — עקבי עם `/api/learning` שהחזיר אפס תובנות למרות 890 רשומות היסטוריות (הרבה מעבר למינימום
5 הרשומות שלו). עודכן ל-`claude-sonnet-5`.

**3. נתוני אחוז הצלחה/כיול היו מדוללים על ידי אוכלוסייה גרועה שיטתית ולא-סחירה — תוקן,
הממצא העמוק ביותר.** שאיבת ארכיון `/api/history` המלא (890 שורות) וחלוקתו לפי מקור הראתה כי
**48%** מתת-הקבוצה המנוקדת (136 מתוך 282 שורות) הגיעו מ-`broad_scan/iv` — ניחוש אלגוריתמי של
קפיצת IV **ללא תאריך FDA מאושר**, שמנגנון ההתראות של הבוט עצמו כבר מסרב לסחור עליו ברוב
(לא כל) נתיבי הקוד. אוכלוסייה זו ממוצעת **-2.58% ליום באחוז הצלחה של 4.4%**. שורות של קטליזטור
FDA אמיתי ממוצעות **+1.39% ליום ב-16.7%** — איתות אמיתי ונקי יותר שהסטטיסטיקה המצרפית טישטשה.
בתוך שורות קטליזטור אמיתי בלבד (n=30, קטן — יש להתייחס ככיווני לא כמסקנה), הציון המצרפי
מתואם עם התשואה ליום הבא ב-**r=0.32**, בעוד יחס call/put בלבד עמד על **r=0.07** — ההפך
מטענת "C/P הוא המנבא החזק ביותר" בהערת הכותרת, שטבלת הקבוצות הגסה כבר רמזה עליו.
תיקונים שבוצעו: `/api/calibration`, נתוני קבוצות ה-C/P, ופרומפט מנוע הלמידה השבועי כעת כולם
מחריגים שורות `broad_scan/iv`; `run_options_scan` (נתיב הסריקה היחיד שלא עשה זאת) כעת מיישם
את אותו סינון מקור-אמיתי כמו כל נתיב התראה אחר; `/api/history` כעת חושף לכל שורה את `source`/
`is_real_fda_event` כך שניתן לבדוק זאת ישירות במקום להסיק מ-event_type. *משקלי* הציון המצרפי
במכוון **לא** כוילו ידנית מתוך מדגם ה-n=30 — זו עבודה עבור הכיול הבייסיאני, המדורג-לפי-מדגם,
של מנוע הלמידה שתוקן כעת, לא ניחוש חד-פעמי מ-30 שורות.

**4. קבוע `REAL_SOURCES` כפול — תוקן.** אותה קבוצת סינון מקורות הועתקה באופן זהה ל-4 מקומות
ב-`main.py` (פעמיים), `scheduler.py`, ו-`unified_scanner.py` — וזו בדיוק הסיבה שאחד מהארבעה
(`run_options_scan`) מעולם לא קיבל את הסינון כשהשלושה האחרים כן. אוחד לתוך `backend/constants.py`,
ומיובא בכל מקום.

**5. קטן: שורת לוג משולשת — תוקן.** `_notify_penny_signals()` תיעד את אותה שורת "Penny BUY alert"
שלוש פעמים ברצף; כעת פעם אחת.

**6. חדש: ה-API ההיסטורי של BiopharmCatalyst עצמו מחובר כעת.** `fetch_bpc_historical()`
(קטליזטורים היסטוריים אמיתיים, תאריכים אמיתיים, `source="biopharmcatalyst"`) היה ממומש
במלואו אך מעולם לא נקרא בשום מקום — מדגם קטליזטור-FDA-אמיתי היה דליל (n=30) רק משום שהזרעת
הנתונים הסתמכה על כך שמעקב האירועים של הבוט עצמו תפס אותו בזמן אמת. משימה יומית חדשה (3:00
לפי שעון מזרח ארה"ב) מזריעה את `HistoricalResult` מרשימת ההיסטוריה העצמאית של BPC במקום זאת,
ומגדילה את המדגם הנקי שעליו מבוססים נתוני הכיול/הלמידה למעלה. `BPC_API_KEY` מאושר כבר מוגדר
ב-Render; נוסף גם ל-`render.yaml` כך שזה מתועד.

**7. הגדול מכולם: הבוט מדד את התוצאה הלא נכונה עבור האסטרטגיה שלו — תוקן.** כל התראת
BUY/EARLY_BUY אומרת במפורש למשתמש לצאת **יום לפני** החלטת ה-FDA, לעולם לא להחזיק דרכה.
אבל `HistoricalResult.change_1d_pct` (שהזין את `/api/calibration` וכל מה שסעיף 3 למעלה
מבוסס עליו) מודד מחיר יום לפני האירוע ← מחיר יום **אחרי** — תגובת השוק להחלטה עצמה, בדיוק
החלון שהאסטרטגיה אומרת להיות מחוצה לו. בנפרד, `run_options_scan` מעולם לא כתב `AlertLog`
כלל עבור תגליות ה-BUY/EARLY_BUY שלו (פער שני, עצמאי — התראות נתיב הסריקה הזה היו בלתי
נראות לחלוטין למעקב התוצאות, ושלחו רק הודעת טלגרם). תוקן על ידי הוספת מעקב כניסה→יציאה-מתוכננת
אמיתי: `AlertLog` כעת שומר `entry_price`/`target_date` בזמן הפעלת ההתראה (נוסף לכל 5 נקודות
יצירת ההתראה, בתוספת 2 הכתיבות החסרות ב-`run_options_scan`), ו-`run_alert_outcome_tracker`
כעת מחשב `change_to_target_pct` — מחיר ב-`entry_price` מול מחיר ב-`target_date` — וממלא
אותו בהדרגה ככל שתאריך היעד של כל התראה חולף. חשוף כ-**אחוז הצלחה — עסקה בפועל** למעלה;
זה המספר שצריך למעשה לשפוט לפיו, לא מדדי תגובת-השוק-לאחר-האירוע (נשמרו בסעיף המכווץ למעלה
לצורך רציפות). הוא מתחיל מ-0 מדגמים וגדל יום אחר יום — שום דבר כאן לא משנה את העבר, רק את
אופן מדידת התוצאות מכאן ואילך.

*כל 7 הסעיפים למעלה נדחפו (pushed) ל-`master` (commits `f6be33b`, `1b7d053`, ומעקב הכניסה→יציאה) —
Render אמור לפרוס מחדש אוטומטית. המספרים בעמוד זה לא ישקפו אותם עד שהבוט יסרוק מחדש לאחר הפריסה,
ו-`win_rate_to_target` בפרט יקרא ריק לזמן מה — הוא מתמלא רק ככל שהתראות חדשות מופעלות ותאריכי
היציאה המתוכננים שלהן אכן חולפים.*
        """
    )
