"""Dashboard entrypoint. Run via `streamlit run dashboard/app.py` (or run.bat,
which starts this alongside the scheduler process). This process is
read-only against the DB — see dashboard/data.py and plan's ADR-3.

Hebrew/RTL (see dashboard/rtl.py): uses st.navigation()/st.Page() instead of
Streamlit's auto-discovered pages/ folder nav, purely so each page can have
a Hebrew sidebar label independent of its (unchanged, English) filename —
the page files themselves didn't move or rename. The home/intro content
below is declared as its own st.Page (a callable, not a file) rather than
rendered directly in this script's own body — st.navigation()'s selected
page is appended to whatever this script already rendered, not a
replacement, so putting the intro directly here would make it show above
EVERY page, not just the home page."""

from __future__ import annotations

import streamlit as st

from dashboard.rtl import apply_rtl

st.set_page_config(page_title="מחקר פיננסי", page_icon="📈", layout="wide")
apply_rtl()


def _home() -> None:
    st.title("📈 מחקר שווקים פיננסיים אישי")
    st.caption(
        "כלי מחקר וקבלת החלטות אישי ומקומי. לעולם לא מבצע עסקאות. "
        "הנתונים מתעדכנים רק כשתהליך התזמון (scheduler) פועל — ראו את "
        "פאנל בריאות המקורות בעמוד חדשות והתראות לבדיקת עדכניות."
    )
    st.markdown(
        """
עמודי המערכת מופיעים בסרגל הצד:

- **מאקרו** — תג המשטר המחושב ומגמות המדדים המרכזיים
- **רשימת מעקב** — מבט מעמיק לכל טיקר (מחיר, אינדיקטורים טכניים, פונדמנטלס, חדשות)
- **סקרינר** — סריקה מדורגת על פני היקום הרחב
- **חדשות והתראות** — כותרות אחרונות, אירועים מסומנים ובריאות מקורות הנתונים
- **צ'אט** — שאלו שאלות המבוססות אך ורק על הנתונים השמורים באפליקציה זו
- **יצירת רעיונות** — סריקת סקטורים וכימות לפי המתודולוגיה שהוזנה
- **בוט FDA** — מעקב אחר שירות ה-FDA הנפרד (github.com/otxengine/fda-bot)

זהו כלי מחקר היוריסטי, לא ייעוץ השקעות — כל ציון מאקרו/סקרינר הוא תווית
תיאורית מבוססת-כללים, לעולם לא תחזית.
"""
    )


pages = [
    st.Page(_home, title="בית", icon="🏠", default=True),
    st.Page("pages/1_Macro.py", title="מאקרו", icon="📈"),
    st.Page("pages/2_Watchlist.py", title="רשימת מעקב", icon="👀"),
    st.Page("pages/3_Screener.py", title="סקרינר", icon="🔎"),
    st.Page("pages/4_News_and_Flags.py", title="חדשות והתראות", icon="🚩"),
    st.Page("pages/5_Chat.py", title="צ'אט", icon="💬"),
    st.Page("pages/6_Idea_Generation.py", title="יצירת רעיונות", icon="💡"),
    st.Page("pages/7_FDA_Bot.py", title="בוט FDA", icon="🧬"),
]

pg = st.navigation(pages)
pg.run()
