"""Background scoring (architecture 4.8), run by the judge model (gpt-5.4) on the REDACTED transcript and the judge results.

1. Per-question grading: one batched call per run, run twice and averaged; a large disagreement is flagged for review.
   Quotes the model gives as evidence are verified against the candidate's own words; anything it cannot find is dropped.
2. Form mapping: every ai / ai_draft criterion of the active form template is scored 1-10 from its own evidence.
   Technical knowledge and problem solving are ANCHORED on graded answers and hidden-test results and may move only
   within a small band around the anchor; "overall" is never asked of the model, it is derived from the weights.
3. Comments (strengths, areas, suggestions) written per criterion from the final scores (comments.py), with a hard wording guard;
   the prep book is never mentioned to students. "prep_references" stay in the interviewer-only part of the results.
4. Integrity flags.
Every call goes through LLMClient (typed-name tripwire, pattern tripwire, store=false, usage pools, queue-or-pay).
Progress is saved after each call (`save_partial`), so a queued job resumes without paying twice.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend.guardrails import injection
from backend.guardrails.pii import Redactor, find_pii
from backend.llm.client import LLMClient
from backend.scoring import prompts
from backend.scoring.comments import write_comments
from backend.scoring.form import ai_criteria

RUNS = 2
DISAGREEMENT = 3.0            # score difference between the two runs that flags a question for human review
ANCHOR_BAND = {"technical": 1, "problem_solving": 1}   # how far the judge may move from the anchor; others default to 2
DEFAULT_BAND = 2
MAX_ANSWER_CHARS = 900
MAX_CODE_CHARS = 5000
MAX_RESUME_CHARS = 8000
WEAK_BELOW = 6.0


def norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------------------------- evidence
def build_evidence(rec: dict, bank_by_id: dict, redactor: Redactor) -> dict:
    d, eng = rec["data"], rec["data"]["engine"]
    questions = []
    for q in eng["questions"]:
        card = bank_by_id.get(q["id"])
        if not card or card["type"] != "concept" or q["state"] == "candidate_questions":
            continue
        questions.append({"id": q["id"], "state": q["state"], "question": card["question"], "model_answer": card.get("model_answer", ""),
                          "key_points": [{"index": i, "point": kp["point"], "weight": kp["weight"]} for i, kp in enumerate(card["key_points"])],
                          "answer": " ".join(q["answers"])[:MAX_ANSWER_CHARS * 2], "skipped": q["skipped"] or not q["answers"],
                          "quick_coverage": q["coverage"], "topic": card.get("topic", ""), "book_id": card.get("book_id", ""), "nudges": q["nudges"]})
    coding = []
    for c in eng["coding"]:
        if "pct" not in c:
            continue
        code, rules = injection.neutralize(redactor.redact(c.get("code", "") or "")[:MAX_CODE_CHARS])
        coding.append({**{k: c.get(k) for k in ("id", "kind", "level", "pct", "language", "tests_passed", "tests_total", "timeouts",
                                                 "compile_error", "skipped", "empty", "auto_submitted", "runs", "datasets_passed", "datasets_total")},
                       "code": code, "pasted_chars": c.get("pasted_chars", 0), "injection_in_code": rules})
    return {"questions": questions, "coding": coding, "resume": d.get("resume_text", "")[:MAX_RESUME_CHARS], "profile": d.get("profile") or {},
            "engine_flags": eng.get("flags", []), "profile_warnings": d.get("profile_warnings", [])}


# ---------------------------------------------------------------------------------------------- question grading
QUESTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "maxItems": 40, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "score", "points_covered", "points_missed", "evidence", "confidence"],
        "properties": {"id": {"type": "string", "maxLength": 20}, "score": {"type": "number", "minimum": 0, "maximum": 10},
                       "points_covered": {"type": "array", "maxItems": 12, "items": {"type": "integer", "minimum": 0, "maximum": 11}},
                       "points_missed": {"type": "array", "maxItems": 12, "items": {"type": "integer", "minimum": 0, "maximum": 11}},
                       "evidence": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 120}},
                       "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}}},
}


def _grade_run(llm: LLMClient, items: list[dict]) -> list[dict]:
    cards = [{k: it[k] for k in ("id", "question", "model_answer", "key_points")} for it in items]
    answers = {it["id"]: it["answer"][:MAX_ANSWER_CHARS * 2] for it in items}
    messages = [{"role": "system", "content": prompts.QUESTION_SYSTEM},
                {"role": "user", "content": json.dumps({"cards": cards}, ensure_ascii=False)},
                {"role": "user", "content": injection.wrap_untrusted("candidate_transcript", json.dumps(answers, ensure_ascii=False))}]
    data, _ = llm.complete_json("judge", messages, QUESTION_SCHEMA, name="question_grades", trusted=(1,))
    return data["questions"]


def _verified(quotes: list[str], answer: str, redactor: Redactor) -> list[str]:
    hay = norm(answer)
    out = []
    for q in quotes:
        n = norm(q)
        if n and n in hay and not find_pii(q) and not injection.detect(q):
            out.append(q.strip())
    return out


def merge_runs(items: list[dict], runs: list[list[dict]], redactor: Redactor) -> dict[str, dict]:
    """Average the runs per question; skipped / unanswered questions are 0 without spending tokens."""
    by_id = {it["id"]: it for it in items}
    out: dict[str, dict] = {}
    for it in items:
        if it["skipped"]:
            out[it["id"]] = {"score": 0.0, "covered": [], "missed": list(range(len(it["key_points"]))), "evidence": [], "confidence": "high",
                             "needs_review": False, "runs": []}
    for qid, it in by_id.items():
        if qid in out:
            continue
        got = [next((r for r in run if r["id"] == qid), None) for run in runs]
        got = [g for g in got if g]
        if not got:
            out[qid] = {"score": None, "covered": [], "missed": [], "evidence": [], "confidence": "low", "needs_review": True, "runs": []}
            continue
        n = len(it["key_points"])
        scores = [min(10.0, max(0.0, g["score"])) for g in got]
        covered = sorted(set.intersection(*[{i for i in g["points_covered"] if 0 <= i < n} for g in got]))
        quotes: list[str] = []
        for g in got:
            for q in _verified(g["evidence"], it["answer"], redactor):
                if q not in quotes:
                    quotes.append(q)
        spread = max(scores) - min(scores)
        conf = "low" if spread >= DISAGREEMENT else got[0]["confidence"]
        out[qid] = {"score": round(sum(scores) / len(scores), 1), "covered": covered, "missed": [i for i in range(n) if i not in covered],
                    "evidence": quotes[:3], "confidence": conf, "needs_review": spread >= DISAGREEMENT, "runs": scores}
    return out


# ---------------------------------------------------------------------------------------------- anchors and facts
def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def to_scale(x: float | None, lo: int, hi: int, top: float = 10.0) -> int | None:
    """Map 0..top onto the criterion's scale (0 -> lo)."""
    return None if x is None else int(round(lo + (hi - lo) * max(0.0, min(top, x)) / top))


def anchors(evidence: dict, q: dict[str, dict], form: dict) -> dict[str, int | None]:
    by_state = lambda *states: [q[i["id"]]["score"] for i in evidence["questions"] if i["state"] in states and q[i["id"]]["score"] is not None]  # noqa: E731
    pcts = [c["pct"] for c in evidence["coding"] if c["kind"] == "coding"]
    sql = [c["pct"] for c in evidence["coding"] if c["kind"] == "sql"]
    scale = {c["id"]: c["scale"] for c in form["criteria"]}
    tech = by_state("tech_qs", "tech_qs_more") + [p / 10 for p in sql]
    ps_pcts = pcts + sql
    out = {
        "technical": to_scale(_mean(tech), *scale.get("technical", [1, 10])),
        "problem_solving": to_scale(_mean(ps_pcts), *scale.get("problem_solving", [1, 10]), top=100.0),
        "intro": to_scale(_mean(by_state("intro")), *scale.get("intro", [1, 10])),
        "projects": to_scale(_mean(by_state("resume_warmup")), *scale.get("projects", [1, 10])),
    }
    return {k: v for k, v in out.items() if k in scale}


def facts(evidence: dict, q: dict[str, dict]) -> dict:
    asked = len(evidence["questions"])
    return {
        "concept_questions_asked": asked, "concept_questions_skipped": sum(1 for i in evidence["questions"] if i["skipped"]),
        "mean_question_score": round(_mean([v["score"] for v in q.values() if v["score"] is not None]) or 0, 1),
        "coding": [{k: c[k] for k in ("kind", "level", "pct", "language", "tests_passed", "tests_total", "timeouts", "compile_error",
                                       "skipped", "auto_submitted", "datasets_passed", "datasets_total") if c.get(k) is not None} for c in evidence["coding"]],
        "resume_signals": {"mentions_sql": bool(evidence["profile"].get("mentions_sql")), "projects": len(evidence["profile"].get("projects", []))},
    }


def prep_references(evidence: dict, q: dict[str, dict]) -> list[dict]:
    weak = [(q[i["id"]]["score"] if q[i["id"]]["score"] is not None else 0, i) for i in evidence["questions"]
            if i["state"] in ("tech_qs", "tech_qs_more") and (q[i["id"]]["score"] or 0) < WEAK_BELOW]
    return [{"label": f"{i['topic']} (Prep Book question {i['book_id']}: {i['question'][:60]})", "topic": i["topic"]} for _, i in sorted(weak, key=lambda t: t[0])[:5]]


# ---------------------------------------------------------------------------------------------- form mapping
def form_schema(ids: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["criteria"],
            "properties": {
                "criteria": {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": {
                    "type": "object", "additionalProperties": False, "required": ["id", "score", "evidence", "confidence"],
                    "properties": {"id": {"type": "string", "enum": ids}, "score": {"type": "integer", "minimum": 1, "maximum": 10},
                                   "evidence": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 160}},
                                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}}}}


def _form_call(llm: LLMClient, form: dict, evidence: dict, q: dict[str, dict], anch: dict) -> dict:
    crits = ai_criteria(form)
    spec = {"criteria": [{"id": c["id"], "label": c["label"], "scale": c["scale"], "guidance": prompts.GUIDANCE.get(c["id"], c["label"]),
                          "anchor": anch.get(c["id"])} for c in crits],
            "facts": facts(evidence, q)}
    per_q = [{"stage": i["state"], "question": i["question"], "answer": i["answer"][:MAX_ANSWER_CHARS], "score": q[i["id"]]["score"],
              "skipped": i["skipped"]} for i in evidence["questions"]]
    code = [{"kind": c["kind"], "pct": c["pct"], "language": c.get("language"), "code": c["code"]} for c in evidence["coding"] if c.get("code")]
    messages = [{"role": "system", "content": prompts.FORM_SYSTEM},
                {"role": "user", "content": json.dumps(spec, ensure_ascii=False)},
                {"role": "user", "content": injection.wrap_untrusted("candidate_transcript", json.dumps(per_q, ensure_ascii=False))},
                {"role": "user", "content": injection.wrap_untrusted("resume", evidence["resume"] or "(no resume provided)")},
                {"role": "user", "content": injection.wrap_untrusted("candidate_code", json.dumps(code, ensure_ascii=False) if code else "(no code submitted)")}]
    data, _ = llm.complete_json("judge", messages, form_schema([c["id"] for c in crits]), name="form_scores", trusted=(1,))
    return data


def assemble(form: dict, evidence: dict, q: dict[str, dict], anch: dict, refs: list[dict], raw: dict, redactor: Redactor) -> dict:
    """Criterion scores and evidence only; the comment boxes are written afterwards from these final scores (comments.py)."""
    corpus = " ".join([i["answer"] for i in evidence["questions"]] + [evidence["resume"]] + [c["code"] for c in evidence["coding"]])
    warnings: list[str] = []
    crit_out: dict[str, dict] = {}
    scales = {c["id"]: c["scale"] for c in form["criteria"]}
    for item in raw["criteria"]:
        cid = item["id"]
        lo, hi = scales[cid]
        score = item["score"]
        a = anch.get(cid)
        band = ANCHOR_BAND.get(cid, DEFAULT_BAND)
        moved = False
        if a is not None and abs(score - a) > band:
            score, moved = a + (band if score > a else -band), True
        score = max(lo, min(hi, score))
        quotes = _verified(item["evidence"], corpus, redactor)
        notes = []
        if cid == "technical":
            notes.append(f"{sum(1 for i in evidence['questions'] if i['state'] in ('tech_qs', 'tech_qs_more'))} technical questions graded; mean score {facts(evidence, q)['mean_question_score']}/10")
        if cid == "problem_solving":
            notes += [f"{c['kind'].upper()} level {c['level']}: {c['pct']}% of hidden tests" + (f" ({c['tests_passed']}/{c['tests_total']})" if c.get("tests_total") else "")
                      + (f", {c['timeouts']} timed out (too slow on large inputs)" if c.get("timeouts") else "")
                      + (", skipped" if c.get("skipped") else "") for c in evidence["coding"]]
        conf = item["confidence"]
        if moved:
            conf = "low" if conf == "high" else conf
            notes.append(f"adjusted toward the evidence-based anchor ({a})")
        if any(q[i["id"]]["needs_review"] for i in evidence["questions"]) and cid in ("technical", "intro", "projects"):
            conf = "low"
        crit_out[cid] = {"score": score, "evidence": notes + quotes, "confidence": conf, "anchor": a}
    best = max([c["pct"] for c in evidence["coding"] if c["kind"] == "coding"] or [0.0])
    return {"criteria": crit_out, "text": {}, "coding_best_pct": best,
            "prep_references": refs, "warnings": warnings}


def integrity_flags(evidence: dict, q: dict[str, dict]) -> list[str]:
    flags = list(evidence["engine_flags"]) + [w for w in evidence["profile_warnings"] if "hidden text" in w or "instruction-like" in w]
    for c in evidence["coding"]:
        if c.get("pasted_chars", 0) >= 200:
            flags.append(f"{c['kind'].upper()} task: {c['pasted_chars']} characters were pasted into the editor")
        if c.get("injection_in_code"):
            flags.append(f"{c['kind'].upper()} task: instruction-like text found in the submitted code and removed: {', '.join(c['injection_in_code'])}")
        if c.get("auto_submitted"):
            flags.append(f"{c['kind'].upper()} task: the timer ran out and the saved draft was submitted automatically")
    silent = sum(1 for i in evidence["questions"] if i["nudges"])
    if silent >= 2:
        flags.append(f"long silences: the candidate was nudged to answer on {silent} questions")
    flags += [f"graders disagreed on question {qid}: flagged for your review" for qid, v in q.items() if v["needs_review"]]
    return flags


# ---------------------------------------------------------------------------------------------- the job
def score_interview(rec: dict, *, llm: LLMClient, bank_by_id: dict, form: dict, redactor: Redactor,
                    partial: dict, save_partial: Callable[[dict], None], runs: int = RUNS) -> dict:
    """Run (or resume) scoring. Raises BudgetExceeded when the judge pool is nearly used and the mode is 'queue'."""
    evidence = build_evidence(rec, bank_by_id, redactor)
    gradable = [i for i in evidence["questions"] if not i["skipped"]]
    run_results: list[list[dict]] = list(partial.get("question_runs", []))
    while gradable and len(run_results) < runs:
        run_results.append(_grade_run(llm, gradable))
        save_partial({"question_runs": run_results})
    q = merge_runs(evidence["questions"], run_results, redactor)
    anch = anchors(evidence, q, form)
    refs = prep_references(evidence, q)
    raw: Any = partial.get("form_raw")
    if raw is None:
        raw = _form_call(llm, form, evidence, q, anch)
        save_partial({"question_runs": run_results, "form_raw": raw})
    result = assemble(form, evidence, q, anch, refs, raw, redactor)
    scores = {cid: c["score"] for cid, c in result["criteria"].items()}
    topics = sorted({i["topic"] for i in evidence["questions"] if i["state"] in ("tech_qs", "tech_qs_more") and (q[i["id"]]["score"] or 0) < WEAK_BELOW and i["topic"]})
    texts = write_comments(llm, form, scores, {cid: [e for e in c["evidence"]] for cid, c in result["criteria"].items()}, topics, result["warnings"])
    labels = form.get("text_fields", [])
    result["text"] = {labels[i]: t for i, t in enumerate([texts["strengths"], texts["areas"], texts["suggestions"]][:len(labels)])}
    result["questions"] = [{"id": i["id"], "state": i["state"], "question": i["question"], "topic": i["topic"], "book_id": i["book_id"],
                            "skipped": i["skipped"], "answer": i["answer"], "quick_coverage": i["quick_coverage"], **q[i["id"]],
                            "key_points": [kp["point"] for kp in i["key_points"]]} for i in evidence["questions"]]
    result["coding"] = [{k: v for k, v in c.items() if k != "injection_in_code"} for c in evidence["coding"]]
    result["integrity"] = integrity_flags(evidence, q)
    return result
