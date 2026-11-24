"""Live quick grade of one verbal answer (gpt-5.4-nano). The result drives early-end and level-3 unlock only;
the final scoring is done later by the judge model on the whole transcript."""
from __future__ import annotations

import json

from backend.guardrails import injection
from backend.guardrails.pii import Redactor, find_pii
from backend.llm.client import LLMClient
from backend.orchestrator.prompts import GRADER_SYSTEM

GRADE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["no_attempt", "points"],
    "properties": {
        "no_attempt": {"type": "boolean"},
        "points": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False, "required": ["index", "covered", "evidence"],
            "properties": {"index": {"type": "integer", "minimum": 0, "maximum": 11}, "covered": {"type": "boolean"},
                           "evidence": {"type": "string", "maxLength": 120}}}},
    },
}


def coverage(key_points: list[dict], covered: list[bool]) -> float:
    """Weighted share of key points covered, 0..1."""
    total = sum(max(kp.get("weight", 1), 0) for kp in key_points)
    if not total:
        return 0.0
    return sum(max(kp.get("weight", 1), 0) for kp, c in zip(key_points, covered) if c) / total


def quick_grade(client: LLMClient, redactor: Redactor, card: dict, answers: list[str]) -> dict:
    """Returns {"coverage": float | None, "covered": [bool...], "evidence": [str...]}. coverage None = no attempt."""
    text = "\n".join(a for a in answers if a.strip()).strip()
    n = len(card["key_points"])
    if not text:
        return {"coverage": None, "covered": [False] * n, "evidence": [""] * n}
    points = [{"index": i, "point": kp["point"]} for i, kp in enumerate(card["key_points"])]
    messages = [
        {"role": "system", "content": GRADER_SYSTEM},
        {"role": "user", "content": json.dumps({"question": card["question"], "key_points": points}, ensure_ascii=False)},
        {"role": "user", "content": injection.wrap_untrusted("candidate_transcript", text)},
    ]
    data, _ = client.complete_json("fast", messages, GRADE_SCHEMA, name="quick_grade", trusted=(1,))
    covered, evidence = [False] * n, [""] * n
    for p in data["points"]:
        if 0 <= p["index"] < n:
            covered[p["index"]] = bool(p["covered"])
            ev = p["evidence"].strip()
            evidence[p["index"]] = "" if (find_pii(ev) or injection.detect(ev)) else ev
    if data["no_attempt"]:
        return {"coverage": None, "covered": [False] * n, "evidence": [""] * n}
    return {"coverage": coverage(card["key_points"], covered), "covered": covered, "evidence": evidence}
