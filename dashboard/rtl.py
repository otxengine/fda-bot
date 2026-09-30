"""RTL + Hebrew UI styling, shared across every page (see plan's dashboard
design principles — this keeps the visual language consistent rather than
each page reinventing it).

Streamlit has no built-in RTL/locale support — st.set_page_config() only
sets the browser tab title/icon/layout, never text direction. This module
injects CSS to flip the whole app to RTL: the sidebar moves to the visual
right (which is where a Hebrew reader expects primary navigation), body
text right-aligns, and Streamlit's own chrome (tabs, metrics, dataframes,
expanders) gets nudged to read correctly right-to-left. Charts (Plotly)
are deliberately NOT flipped — a right-to-left time axis reads backwards
for a time series (this app's charts are ALL time series or ranked
tables), so numbers/dates stay LTR inside an otherwise-RTL page, matching
how Hebrew documents conventionally embed numbers and date ranges.

Call apply_rtl() once per page script, right after st.set_page_config()."""

from __future__ import annotations

import streamlit as st

_RTL_CSS = """
<style>
/* Right-to-left base direction for the whole app */
html, body, [class*="css"] {
    direction: rtl;
}

/* Streamlit's main content and sidebar containers */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    direction: rtl;
}
[data-testid="stSidebar"] {
    direction: rtl;
}

/* Headings/paragraphs/lists right-align by default in RTL, but Streamlit's
   own layout primitives (columns, metrics) sometimes need an explicit nudge */
h1, h2, h3, h4, h5, h6, p, li, label {
    text-align: right;
}

/* st.metric: value block right-aligned, matches RTL reading order */
[data-testid="stMetric"] {
    text-align: right;
}

/* Tabs: keep the tab strip itself LTR-ordered visually (first tab on the
   right) — st.tabs already renders left-to-right in DOM order, and RTL
   flips that visually, which is what we want (first-declared tab = the
   rightmost, read first) */
[data-testid="stTabs"] {
    direction: rtl;
}

/* Dataframes/tables: keep cell CONTENTS readable — numbers/dates/tickers
   stay LTR inside an RTL page (see module docstring) */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    direction: ltr;
}

/* Code blocks and anything explicitly monospace stay LTR (paths, tickers,
   raw JSON, series IDs like FRED:FEDFUNDS) */
code, pre, [data-testid="stCodeBlock"] {
    direction: ltr;
    text-align: left;
    unicode-bidi: embed;
}

/* Expander header text right-aligned */
[data-testid="stExpander"] summary {
    text-align: right;
}

/* Chat message bubbles: right-align own text, keep the layout sane */
[data-testid="stChatMessage"] {
    direction: rtl;
    text-align: right;
}

/* Plotly charts: force LTR internally so numeric/time axes don't reverse
   (see module docstring) — the chart's surrounding caption/title (plain
   Streamlit markdown, not part of the Plotly SVG) still follows RTL above */
[data-testid="stPlotlyChart"] {
    direction: ltr;
}
</style>
"""


def apply_rtl() -> None:
    st.markdown(_RTL_CSS, unsafe_allow_html=True)
