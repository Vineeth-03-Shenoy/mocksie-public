"""Question bank access with the visibility and selection rules from the architecture.

- The LLM only ever sees ONE card: question, model answer, key points (llm_card).
- The browser never sees hidden tests, model answers or the book's panel notes (browser_problem).
- Concept questions come from levels 1-2; level 3 only when the caller raises max_level
  (the orchestrator does that after two strong answers in a row).
- Optional book sections (AI/RAG, automation, agentic) are off unless switched on in Settings
  or the JD tags them.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

BANK_PATH = Path(__file__).with_name("question_bank.json")
OPTIONAL_SECTIONS = ("AI", "AUTO", "AGENT")

_LLM_FIELDS = ("id", "question", "model_answer", "key_points", "follow_ups")
_CODING_PUBLIC = ("id", "question", "statement", "io", "signature", "starter_code", "difficulty")
_SQL_PUBLIC = ("id", "question", "statement", "schema", "sample_rows", "sample_expected", "columns", "difficulty")


def load(path: Path = BANK_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def section_of(entry: dict) -> str:
    return entry["id"].split("-")[0]


def llm_card(entry: dict) -> dict:
    """What the live interviewer model may see for the current question. Nothing else."""
    card = {k: entry[k] for k in _LLM_FIELDS if k in entry}
    if entry["type"] in ("coding", "sql"):
        card["problem"] = entry.get("statement") or entry["question"]
        card.pop("model_answer", None)  # never reveal the approach or the reference query
        card.pop("key_points", None)
    return card


def browser_problem(entry: dict) -> dict:
    """What the candidate's browser receives when coding/SQL starts. Visible samples only."""
    keys = _SQL_PUBLIC if entry["type"] == "sql" else _CODING_PUBLIC
    out = {k: entry[k] for k in keys if k in entry}
    if entry["type"] == "coding":
        out["sample_tests"] = [{"stdin": t["stdin"], "expected": t["expected"]} for t in entry.get("sample_tests", [])]
    return out


def optional_allowed(entry: dict, enabled_sections: frozenset[str] | set[str], jd_tags: list[str] | None) -> bool:
    """An optional entry is allowed if its section is switched on, or the JD tags its topic."""
    if not entry.get("optional"):
        return True
    if section_of(entry) in enabled_sections:
        return True
    topic_tags = set(entry["jd_tags"]) - {"advanced"}
    return bool(jd_tags and topic_tags & set(jd_tags))


def pool(bank: list[dict], *, stage: str, difficulty: int | None = None, max_level: int | None = None,
         jd_tags: list[str] | None = None, exclude_ids: set[str] | frozenset[str] = frozenset(),
         enabled_sections: set[str] | frozenset[str] = frozenset(), voice_only: bool = True) -> list[dict]:
    """Eligible entries for a stage, never repeating. Excluded entries never appear.

    stage="tech" also returns optional entries that are switched on (they are ordinary tech questions).
    """
    out = []
    for e in bank:
        if e.get("excluded") or e["id"] in exclude_ids:
            continue
        entry_stage = "tech" if e.get("optional") else e["stage"]
        if entry_stage != stage or not optional_allowed(e, enabled_sections, jd_tags):
            continue
        if difficulty is not None and e["difficulty"] != difficulty:
            continue
        if max_level is not None and e["difficulty"] > max_level:
            continue
        if voice_only and e["type"] == "concept" and not e["voice_suitable"]:
            continue
        if e["type"] == "coding" and "hidden_tests" not in e:
            continue
        if e["type"] == "sql" and "hidden_datasets" not in e:
            continue
        out.append(e)
    if jd_tags:  # prefer JD-tagged topics, keep the rest as the fallback
        out.sort(key=lambda e: not (set(e["jd_tags"]) & set(jd_tags)))
    return out


def pick(entries: list[dict], jd_tags: list[str] | None = None, rng: random.Random | None = None) -> dict | None:
    """Random pick at the level, preferring JD-tagged topics, falling back to the rest."""
    if not entries:
        return None
    rng = rng or random.Random()
    tagged = [e for e in entries if jd_tags and set(e["jd_tags"]) & set(jd_tags)]
    return rng.choice(tagged or entries)
