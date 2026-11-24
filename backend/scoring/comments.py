"""The three free-text form fields (Strengths Observed, Areas for Improvement, Specific Suggestions).

They are written per CRITERION, not per question, and the criteria are chosen here in code from the scores:
  strengths   = the (up to) 3 highest-scoring AI-scored criteria
  areas       = the (up to) 3 lowest-scoring of the rest
  suggestions = one actionable suggestion for each of those weak criteria
Criteria filled by the interviewer (appearance, body language) and the derived overall score are never mentioned.

The students do not know the prep book exists, so these texts must never mention it. The prompt says so, and a hard guard
in code rejects any text that does: it is regenerated, and after MAX_ATTEMPTS failed attempts the offending sentence is removed.
"""
from __future__ import annotations

import json
import re

from backend.guardrails import injection
from backend.guardrails.pii import find_pii
from backend.scoring import prompts
from backend.scoring.form import ai_criteria

MAX_ATTEMPTS = 2
MAX_FIELD_CHARS = 600
ITEM_CHARS = 150
PICK = 3

# What may never appear in a comment: the prep book and its internals, question ids, and the interviewer-only criteria.
# The id patterns are case-SENSITIVE ("Q12" is an id, "a1" is not), the wording patterns are not.
ID_PATTERN = re.compile(r"\b(?:Q|S|F|A|B)\d{1,3}\b|DBMS-\d+|STRETCH-\d+")
WORD_PATTERN = re.compile(r"prep\s*book|prep\s*reference|model\s*answer|key\s*point|\bappearance\b|\bgrooming\b|\bbody\s*language\b|\battire\b", re.I)


def violations(text: str) -> bool:
    return bool(WORD_PATTERN.search(text) or ID_PATTERN.search(text) or find_pii(text) or injection.detect(text))


def strip_bad_sentences(text: str) -> str:
    """Last resort: drop every sentence that still breaks the rules."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(p for p in parts if p and not violations(p)).strip()


def short_label(label: str) -> str:
    """'Technical Knowledge (Core Fundamentals)' -> 'Technical Knowledge'."""
    return re.split(r"\s+\(", label, maxsplit=1)[0].strip()


def pick_criteria(form: dict, scores: dict[str, int | float]) -> tuple[list[dict], list[dict]]:
    """(strong, weak): highest and lowest scored AI criteria, never the same criterion twice, never interviewer-only ones."""
    crits = [c for c in ai_criteria(form) if scores.get(c["id"]) is not None]
    order = {c["id"]: i for i, c in enumerate(crits)}
    ranked = sorted(crits, key=lambda c: (-scores[c["id"]], order[c["id"]]))
    strong = ranked[:PICK]
    rest = [c for c in ranked if c not in strong]
    weak = list(reversed(rest))[:PICK]               # lowest first
    return strong, weak


def comments_schema(strong_ids: list[str], weak_ids: list[str]) -> dict:
    def block(ids: list[str]) -> dict:
        return {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "text"],
            "properties": {"id": {"type": "string", "enum": ids or ["none"]}, "text": {"type": "string", "maxLength": ITEM_CHARS}}}}
    return {"type": "object", "additionalProperties": False, "required": ["strengths", "areas", "suggestions"],
            "properties": {"strengths": block(strong_ids), "areas": block(weak_ids), "suggestions": block(weak_ids)}}


def _compose(items: list[dict], labels: dict[str, str], ids: list[str]) -> str:
    by_id = {i["id"]: i["text"].strip() for i in items if i.get("id") in labels}
    out: list[str] = []
    for cid in ids:
        text = by_id.get(cid, "")
        if not text:
            continue
        if text[-1] not in ".!?":
            text += "."
        piece = f"{labels[cid]}: {text}"
        if len(" ".join(out + [piece])) > MAX_FIELD_CHARS:
            break
        out.append(piece)
    return " ".join(out)


def build_input(form: dict, strong: list[dict], weak: list[dict], scores: dict, crit_evidence: dict[str, list[str]], topics: list[str]) -> dict:
    def row(c: dict) -> dict:
        return {"id": c["id"], "name": short_label(c["label"]), "what_it_covers": prompts.GUIDANCE.get(c["id"], c["label"]),
                "score_out_of_10": scores[c["id"]], "evidence": crit_evidence.get(c["id"], [])[:4]}
    return {"strong_criteria": [row(c) for c in strong], "weak_criteria": [row(c) for c in weak], "weak_technical_topics": topics[:5]}


def write_comments(llm, form: dict, scores: dict, crit_evidence: dict[str, list[str]], topics: list[str],
                   warnings: list[str]) -> dict[str, str]:
    """Returns {'strengths': str, 'areas': str, 'suggestions': str}. Every text has passed the guard."""
    strong, weak = pick_criteria(form, scores)
    if not strong:
        return {"strengths": "", "areas": "", "suggestions": ""}
    labels = {c["id"]: short_label(c["label"]) for c in strong + weak}
    spec = build_input(form, strong, weak, scores, crit_evidence, topics)
    schema = comments_schema([c["id"] for c in strong], [c["id"] for c in weak])
    base = [{"role": "system", "content": prompts.COMMENTS_SYSTEM}, {"role": "user", "content": json.dumps(spec, ensure_ascii=False)}]
    data: dict = {"strengths": [], "areas": [], "suggestions": []}
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        msgs = list(base)
        trusted = (1,)
        if feedback:
            msgs.append({"role": "user", "content": feedback})
            trusted = (1, 2)
        data, _ = llm.complete_json("judge", msgs, schema, name="form_comments", trusted=trusted)
        bad = [i["id"] for key in ("strengths", "areas", "suggestions") for i in data[key] if violations(i["text"])]
        if not bad:
            break
        feedback = ("Your last answer broke a rule. Rewrite every item. Never mention a prep book, references, model answers, key points, "
                    "question numbers or ids, appearance or body language. Write about the criterion only, in general terms.")
    else:
        warnings.append("a drafted comment broke the wording rules twice, so the offending sentences were removed")
    out = {}
    for key, ids in (("strengths", [c["id"] for c in strong]), ("areas", [c["id"] for c in weak]), ("suggestions", [c["id"] for c in weak])):
        items = [{"id": i["id"], "text": strip_bad_sentences(i["text"])} for i in data[key]]
        out[key] = _compose(items, labels, ids)
    return out
