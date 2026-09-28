"""Lightweight headline sentiment via VADER — no model download, adequate for
headline-level polarity (see plan's assumption #6: not a fine-tuned NLP model).
"""

from __future__ import annotations

from functools import lru_cache

import sqlite3


@lru_cache
def _analyzer():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    return SentimentIntensityAnalyzer()


def score_headline(headline: str) -> float:
    """Compound VADER score in [-1, 1]."""
    return _analyzer().polarity_scores(headline)["compound"]


def score_unscored_articles(conn: sqlite3.Connection) -> int:
    """Fills in sentiment_score for any news_articles row that doesn't have
    one yet. Idempotent — safe to call every scheduler tick."""
    rows = conn.execute(
        "SELECT article_id, headline FROM news_articles WHERE sentiment_score IS NULL"
    ).fetchall()
    if not rows:
        return 0
    conn.executemany(
        "UPDATE news_articles SET sentiment_score = ? WHERE article_id = ?",
        [(score_headline(headline), article_id) for article_id, headline in rows],
    )
    conn.commit()
    return len(rows)
