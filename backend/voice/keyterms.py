"""Keyterms for Deepgram Flux: technical words from the question bank that speech recognition often gets wrong.

Flux accepts plain terms only (no weights; no commas, semicolons or newlines). They are sent with a Configure message
after connecting, so the URL stays short, and refreshed for each question. The docs give no maximum, so we cap the list.
"""
from __future__ import annotations

import re

MAX_TERMS = 100
MAX_TERM_CHARS = 40
ACRONYM = re.compile(r"\b[A-Z][A-Z0-9+#]{1,9}\b")                 # SQL, ACID, TCP, B+
CAMEL = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b")               # HashMap, PostgreSQL
SYMBOLED = re.compile(r"\b[A-Za-z]+(?:\+\+|#)")                    # C++, C#
COMMON_ACRONYMS = {"A", "I", "IT", "OK", "OR", "AND", "NOT", "IF", "IS", "AS", "AT", "BY", "ON", "OF", "TO", "IN", "ID", "NO", "SO", "US"}
# Ordinary English words that appear in capitals in SQL and code samples. Boosting them would bend normal speech towards them.
AMBIGUOUS = {"ALL", "NONE", "NULL", "TRUE", "FALSE", "ORDER", "GROUP", "WHERE", "LEFT", "RIGHT", "INNER", "OUTER", "COUNT", "SUM", "GET", "PUT",
             "POST", "DELETE", "DROP", "DESC", "ASC", "HAVING", "LIMIT", "FROM", "INTO", "VALUES", "BY", "CAP", "GATE", "CD", "CI", "ANY", "NEW", "SET"}


def clean(term: str) -> str:
    term = re.sub(r"[,;\n\r]+", " ", term).strip(" .:()\"'")
    return re.sub(r"\s+", " ", term)[:MAX_TERM_CHARS]


def terms_in(text: str) -> list[str]:
    found = ACRONYM.findall(text) + CAMEL.findall(text) + SYMBOLED.findall(text)
    return [t for t in (clean(f) for f in found) if t and t.upper() not in COMMON_ACRONYMS and t.upper() not in AMBIGUOUS]


def for_card(card: dict | None) -> list[str]:
    """Terms for one question card: acronyms and technical names found in the question and the key points."""
    if not card:
        return []
    text = " ".join([card.get("question", "")] + [kp.get("point", "") for kp in card.get("key_points", [])])
    out = terms_in(text)   # section names such as "Stretch questions" are labels, not things a candidate says, so topics are not used
    return out


def build(bank: list[dict], current: dict | None = None) -> list[str]:
    """Current question first, then the most frequent terms across the whole bank. De-duplicated, case-insensitively, capped."""
    seen: dict[str, str] = {}
    for t in for_card(current):
        seen.setdefault(t.lower(), t)
    freq: dict[str, list] = {}
    for card in bank:
        if card.get("excluded"):
            continue
        for t in for_card(card):
            entry = freq.setdefault(t.lower(), [0, t])
            entry[0] += 1
    for _, t in sorted(freq.values(), key=lambda e: (-e[0], e[1].lower())):
        if len(seen) >= MAX_TERMS:
            break
        seen.setdefault(t.lower(), t)
    return list(seen.values())[:MAX_TERMS]
