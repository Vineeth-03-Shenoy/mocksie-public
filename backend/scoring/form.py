"""The evaluation form template (architecture 4.9): criteria, scales, bands and recommendation rules live in
data, never in code. The overall score is the equal-weight mean of every criterion except "overall" itself. A copy of the active template is stored with each scored interview so later edits cannot change
a finished result."""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "content" / "eval_form.default.json"
SOURCES = {"ai", "interviewer", "ai_draft"}


class FormError(ValueError):
    pass


def load_default() -> dict:
    return validate(json.loads(DEFAULT_PATH.read_text(encoding="utf-8")))


def validate(form: dict) -> dict:
    form = copy.deepcopy(form)
    ids = [c.get("id") for c in form.get("criteria", [])]
    if not ids or len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        raise FormError("criteria need unique, non-empty ids")
    for c in form["criteria"]:
        lo, hi = c.get("scale", [1, 10])
        if not (isinstance(lo, int) and isinstance(hi, int) and lo < hi):
            raise FormError(f"criterion {c['id']}: scale must be two integers, low < high")
        if c.get("source") not in SOURCES:
            raise FormError(f"criterion {c['id']}: source must be one of {sorted(SOURCES)}")
        if c.get("weight", 0) < 0:
            raise FormError(f"criterion {c['id']}: weight cannot be negative")
        c["scale"] = [lo, hi]
        c.setdefault("weight", 0)
        if not c.get("label"):
            raise FormError(f"criterion {c['id']}: label is required")
    for key in ("overall_assessment", "recommendation"):
        form[key] = sorted(form.get(key, []), key=lambda b: -b["min"])
    form.setdefault("recommendation_fallback", "Not Yet – Needs Additional Training")
    form.setdefault("text_fields", [])
    for key, options in (("overall_assessment", assessment_options(form)), ("recommendation", recommendation_options(form))):
        if not all(isinstance(o, str) and o.strip() for o in options) or len(set(options)) != len(options):
            raise FormError(f"{key}: option labels must be unique and non-empty")
    return form


def assessment_options(form: dict) -> list[str]:
    """The ONLY labels allowed for 'Overall assessment' (verbatim from the template)."""
    return [b["label"] for b in form["overall_assessment"]]


def recommendation_options(form: dict) -> list[str]:
    """The ONLY labels allowed for 'Recommendation' (verbatim from the template): every rule label plus the fallback."""
    return [b["label"] for b in form["recommendation"]] + [form["recommendation_fallback"]]


def ai_criteria(form: dict) -> list[dict]:
    """Criteria the judge scores. 'overall' (id) is always derived from the others, never asked of the model."""
    return [c for c in form["criteria"] if c["source"] in ("ai", "ai_draft") and c["id"] != "overall"]
