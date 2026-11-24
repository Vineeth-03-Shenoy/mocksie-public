"""Turn the stored AI scoring plus the interviewer's overrides into the results page, in the college form's order.

Pure functions (no I/O): the overall score (equal-weight mean of criteria 1-9), the band and the recommendation are always
recomputed from the EFFECTIVE values, so entering or overriding any score flows into all three.
"""
from __future__ import annotations

import csv
import io

from backend.scoring import form as formmod


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


FORMULA = "Overall = equal-weight mean of criteria 1 to 9 (every criterion except Overall itself), rounded to one decimal."


def overall_mean(form: dict, values: dict[str, float | None]) -> float | None:
    """Equal-weight mean of every criterion except 'overall' that has a value (None when nothing is scored)."""
    xs = [values[c["id"]] for c in form["criteria"] if c["id"] != "overall" and values.get(c["id"]) is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def missing_inputs(form: dict, values: dict[str, float | None]) -> list[str]:
    """Interviewer-filled criteria with no value yet: while any is missing the overall score is provisional."""
    return [c["label"] for c in form["criteria"] if c["source"] == "interviewer" and values.get(c["id"]) is None]


def band(bands: list[dict], value: float | None) -> str | None:
    if value is None:
        return None
    return next((b["label"] for b in bands if value >= b["min"]), None)


def _verbatim(label: str | None, options: list[str]) -> str | None:
    """Overall assessment and Recommendation are only ever one of the template's own labels."""
    if label is not None and label not in options:
        raise ValueError(f"'{label}' is not one of the form's options")
    return label


def recommend(form: dict, overall: float | None, coding_best_pct: float) -> str:
    if overall is not None:
        for rule in form["recommendation"]:
            req = rule.get("requires", {})
            if overall >= rule["min"] and coding_best_pct >= req.get("coding_pct", 0):
                return rule["label"]
    return form["recommendation_fallback"]


def build_view(form: dict, scoring: dict, overrides: dict) -> dict:
    """scoring: {"criteria": {id: {score, evidence, confidence}}, "text": {...}, "coding_best_pct": float, ...}."""
    ai = scoring.get("criteria", {})
    ov = overrides.get("criteria", {})
    rows, effective = [], {}
    for c in form["criteria"]:
        lo, hi = c["scale"]
        a = ai.get(c["id"]) or {}
        o = ov.get(c["id"])
        row = {"id": c["id"], "label": c["label"], "scale": c["scale"], "source": c["source"], "weight": c.get("weight", 0),
               "ai_value": a.get("score"), "evidence": a.get("evidence", []), "confidence": a.get("confidence"),
               "override": o["value"] if o else None, "override_note": (o or {}).get("note", "")}
        row["value"] = _clamp(o["value"], lo, hi) if o else a.get("score")
        effective[c["id"]] = row["value"]
        rows.append(row)
    ai_values = {c["id"]: (ai.get(c["id"]) or {}).get("score") for c in form["criteria"]}
    ai_overall = overall_mean(form, ai_values)                                  # AI values only
    derived = overall_mean(form, effective)                                     # with the interviewer's scores and overrides
    for row in rows:
        if row["id"] == "overall":
            row["ai_value"], row["override"], row["override_note"] = ai_overall, None, ""
            row["evidence"] = [FORMULA]
            row["confidence"] = "high" if ai_overall is not None else None
            row["value"] = derived                                              # always derived, never typed in
            effective["overall"] = derived
    overall = derived
    missing = missing_inputs(form, effective)
    best = scoring.get("coding_best_pct", 0.0)
    text_ai, text_ov = scoring.get("text", {}), overrides.get("text", {})
    texts = [{"label": label, "ai_value": text_ai.get(label, ""), "override": text_ov.get(label), "value": text_ov.get(label) if label in text_ov else text_ai.get(label, "")}
             for label in form.get("text_fields", [])]
    assessment_ok, recommendation_ok = formmod.assessment_options(form), formmod.recommendation_options(form)
    return {"form": form["name"], "criteria": rows, "overall_score": overall, "provisional": bool(missing), "missing_inputs": missing,
            "formula": FORMULA,
            "overall_assessment": _verbatim(overrides.get("assessment") or band(form["overall_assessment"], overall), assessment_ok),
            "overall_assessment_ai": _verbatim(band(form["overall_assessment"], ai_overall), assessment_ok),
            "recommendation": _verbatim(overrides.get("recommendation") or recommend(form, overall, best), recommendation_ok),
            "recommendation_ai": _verbatim(recommend(form, ai_overall, best), recommendation_ok), "text_fields": texts}


def _safe(cell) -> str:
    """Spreadsheet formula injection: a cell that starts with = + - @ (or tab/CR) would run as a formula, so prefix it."""
    text = "" if cell is None else str(cell)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(view: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Item", "AI value", "Final value"])
    for r in view["criteria"]:
        w.writerow([_safe(r["label"]), _safe(r["ai_value"]), _safe(r["value"])])
    w.writerow(["Overall assessment", _safe(view["overall_assessment_ai"]), _safe(view["overall_assessment"])])
    for t in view["text_fields"]:
        w.writerow([_safe(t["label"]), _safe(t["ai_value"]), _safe(t["value"])])
    w.writerow(["Recommendation", _safe(view["recommendation_ai"]), _safe(view["recommendation"])])
    return buf.getvalue()
