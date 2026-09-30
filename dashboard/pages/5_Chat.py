"""Chat/advisor page. Grounded strictly in this app's own data via Anthropic
tool-calling (see analysis/chat_tools.py) — never the model's own general
market knowledge, never a prediction, never direct investment advice. Needs
ANTHROPIC_API_KEY (see .env.example) — a claude.ai Pro/Max subscription does
NOT cover this; it's separate, usage-based API billing.

Conversations are persisted (multiple, named, resumable — a sidebar history
list like a normal AI chat app) to chat_conversations/chat_history (see
storage/schema.sql), so they survive a dashboard/scheduler restart, not just
the current browser session. Only ever stores plain text turns — never the
intermediate tool_use/tool_result blocks a turn generates internally (see
chat_tools.run_chat_turn) — so resuming a conversation never replays stale
tool output as if it were still current.
"""

from __future__ import annotations

import streamlit as st

from analysis.chat_tools import run_chat_turn
from config.settings import get_settings
from dashboard.data import (
    delete_conversation,
    list_conversations,
    load_conversation_messages,
    save_chat_message,
    start_new_conversation,
)
from dashboard.rtl import apply_rtl
from storage.db import read_connection

apply_rtl()

api_key = get_settings().anthropic_api_key

# --- Sidebar: conversation history, like a normal AI chat app --------------
with st.sidebar:
    st.markdown("### שיחות")
    if st.button("➕ שיחה חדשה", use_container_width=True):
        st.session_state.pop("current_conversation_id", None)
        st.session_state.chat_messages = []
        st.rerun()

    st.divider()
    for conv in list_conversations():
        is_active = st.session_state.get("current_conversation_id") == conv["id"]
        row = st.columns([5, 1])
        button_label = ("🟢 " if is_active else "") + conv["title"]
        if row[0].button(button_label, key=f"open_{conv['id']}", use_container_width=True):
            st.session_state.current_conversation_id = conv["id"]
            st.session_state.chat_messages = load_conversation_messages(conv["id"])
            st.rerun()
        if row[1].button("🗑️", key=f"del_{conv['id']}", help="מחיקת שיחה זו"):
            delete_conversation(conv["id"])
            if is_active:
                st.session_state.pop("current_conversation_id", None)
                st.session_state.chat_messages = []
            st.rerun()

st.title("צ'אט")
st.caption(
    "שאלו על תנאי מאקרו, רוטציית סקטורים, רשימת המעקב שלכם או הסקרינר. "
    "התשובות מבוססות אך ורק על הנתונים השמורים באפליקציה זו — לעולם לא תחזית, "
    "לעולם לא ייעוץ השקעות. השיחות נשמרות — ניתן לחזור אליהן בכל עת מסרגל הצד."
)

if not api_key:
    st.warning(
        "הצ'אט מושבת — הגדירו ANTHROPIC_API_KEY בקובץ ה-.env כדי להפעיל אותו. "
        "שימו לב: מנוי claude.ai Pro/Max אינו כולל זאת; זהו מפתח API נפרד, "
        "בחיוב לפי שימוש, מ-console.anthropic.com."
    )
    st.stop()

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

for msg in st.session_state.chat_messages:
    if msg["role"] not in ("user", "assistant") or not isinstance(msg["content"], str):
        continue  # skip raw tool-use/tool-result turns in the rendered transcript
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("למשל: איזה סקטור נראה הכי חזק כרגע, והאם הפונדמנטלס המאקרו-כלכלי תומך בכך?")
if prompt:
    if "current_conversation_id" not in st.session_state:
        # First message of a fresh conversation — create its row now, titled
        # from this message, rather than on the earlier "New conversation" click.
        st.session_state.current_conversation_id = start_new_conversation(prompt)
    conversation_id = st.session_state.current_conversation_id

    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    save_chat_message(conversation_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("בודק את הנתונים..."):
            with read_connection() as conn:
                reply = run_chat_turn(conn, api_key, st.session_state.chat_messages)
        st.markdown(reply)

    st.session_state.chat_messages.append({"role": "assistant", "content": reply})
    save_chat_message(conversation_id, "assistant", reply)
    st.rerun()  # refreshes the sidebar so a brand-new conversation's title appears immediately
