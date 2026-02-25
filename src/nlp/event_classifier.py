"""
event_classifier.py
Rule-based event type classifier using cc_news.categories + title keywords.
No ML model needed for this step — categories column already does heavy lifting.
Read-only: never writes to any table.
"""

import re

# Maps to event_type values stored in FE_NEWS_SENTIMENT
EVENT_TYPES = [
    "REGULATION",
    "HACK_EXPLOIT",
    "ADOPTION_PARTNERSHIP",
    "MACRO",
    "WHALE_MOVEMENT",
    "ETF_INSTITUTIONAL",
    "LIQUIDATION",
    "OTHER",
]


# Category-based rules (highest confidence)
_CATEGORY_RULES = {
    "REGULATION":           "REGULATION",
    "SECURITY INCIDENTS":   "HACK_EXPLOIT",
    "MACROECONOMICS":       "MACRO",
    "FIAT":                 "MACRO",
}

# Title keyword patterns (fallback when categories are generic)
_TITLE_PATTERNS = [
    ("REGULATION",          re.compile(
        r"\b(sec|cftc|ban|law|regulat|legislat|comply|compliance|sanction|lawsuit|court|bill|act)\b",
        re.IGNORECASE)),
    ("HACK_EXPLOIT",        re.compile(
        r"\b(hack|exploit|breach|stolen|theft|vulnerab|attack|drainer|rug\s?pull|scam|phishing)\b",
        re.IGNORECASE)),
    ("ADOPTION_PARTNERSHIP",re.compile(
        r"\b(partner|integrat|adopt|launch|list|acqui|merge|collaborat|ecosystem|integrates)\b",
        re.IGNORECASE)),
    ("ETF_INSTITUTIONAL",   re.compile(
        r"\b(etf|institutional|blackrock|fidelity|grayscale|vanguard|treasury|reserve|sovereign)\b",
        re.IGNORECASE)),
    ("WHALE_MOVEMENT",      re.compile(
        r"\b(whale|transfer|moved?\s+\d|billion|large\s+transaction|on-?chain|wallet)\b",
        re.IGNORECASE)),
    ("LIQUIDATION",         re.compile(
        r"\b(liquidat|forced\s+clos|margin\s+call|short\s+squeeze|long\s+squeeze)\b",
        re.IGNORECASE)),
    ("MACRO",               re.compile(
        r"\b(fed|fomc|cpi|inflation|rate\s+cut|rate\s+hike|gdp|recession|dollar|treasury\s+yield)\b",
        re.IGNORECASE)),
]


def classify_event(categories_str: str, title: str) -> str:
    """
    Classify a news article into an event type.

    Priority:
      1. Categories column exact match (most reliable)
      2. Title keyword regex patterns
      3. Fallback: OTHER

    Args:
        categories_str: pipe-delimited string from cc_news.categories
        title: article title from cc_news.title

    Returns:
        One of EVENT_TYPES strings
    """
    if categories_str:
        tokens = [t.strip().upper() for t in categories_str.split("|")]
        for token in tokens:
            if token in _CATEGORY_RULES:
                return _CATEGORY_RULES[token]

    if title:
        for event_type, pattern in _TITLE_PATTERNS:
            if pattern.search(title):
                return event_type

    return "OTHER"
