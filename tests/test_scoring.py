"""Step 5, pure parts: form template, anchors, merging of judge runs, overall score, bands, recommendation, CSV."""
import pytest

from backend.guardrails.pii import Redactor
from backend.scoring import engine as se
from backend.scoring import form as formmod
from backend.scoring import results as res

FORM = formmod.load_default()
RED = Redactor("Priya Rhea Testname")


# ---------------------------------------------------------------- the form template
def test_default_form_is_in_college_order_with_ten_criteria_and_equal_weighting():
    ids = [c["id"] for c in FORM["criteria"]]
    assert ids == ["appearance", "body_language", "communication", "intro", "technical", "problem_solving", "projects", "resume", "attitude", "overall"]
    assert all(c.get("weight", 0) == 0 for c in FORM["criteria"]) and FORM["overall_formula"] == "equal_mean"
    assert [c["id"] for c in formmod.ai_criteria(FORM)] == ["communication", "intro", "technical", "problem_solving", "projects", "resume", "attitude"]
    assert FORM["overall_assessment"][0]["min"] > FORM["overall_assessment"][-1]["min"]      # sorted high to low


@pytest.mark.parametrize("bad", [
    {"criteria": []}, {"criteria": [{"id": "a", "label": "A", "scale": [5, 5], "source": "ai"}]},
    {"criteria": [{"id": "a", "label": "A", "scale": [1, 10], "source": "robot"}]},
    {"criteria": [{"id": "a", "label": "A", "scale": [1, 10], "source": "ai"}, {"id": "a", "label": "B", "scale": [1, 10], "source": "ai"}]},
    {"criteria": [{"id": "a", "label": "A", "scale": [1, 10], "source": "ai", "weight": -1}]},
])
def test_invalid_templates_are_rejected(bad):
    with pytest.raises(formmod.FormError):
        formmod.validate(bad)


def test_another_colleges_form_needs_no_code_change():
    other = formmod.validate({"name": "Other", "criteria": [
        {"id": "comm", "label": "Communication", "scale": [1, 5], "source": "ai", "weight": 0.5},
        {"id": "tech", "label": "Tech", "scale": [1, 5], "source": "ai", "weight": 0.5}],
        "overall_assessment": [{"label": "Good", "min": 3}, {"label": "Poor", "min": 0}], "recommendation": [], "text_fields": ["Comments"]})
    view = res.build_view(other, {"criteria": {"comm": {"score": 4}, "tech": {"score": 2}}, "text": {"Comments": "ok"}, "coding_best_pct": 0}, {})
    assert view["overall_score"] == 3.0 and view["overall_assessment"] == "Good" and view["text_fields"][0]["value"] == "ok"


# ---------------------------------------------------------------- overall, bands, recommendation
def ai(**scores):
    return {"criteria": {k: {"score": v, "evidence": [], "confidence": "high"} for k, v in scores.items()}, "text": {}, "coding_best_pct": 0.0}


FULL = dict(communication=8, intro=8, technical=8, problem_solving=8, projects=8, resume=8, attitude=8)


def test_overall_is_the_equal_mean_and_not_asked_of_the_model():
    scoring = ai(communication=10, intro=10, technical=10, problem_solving=4, projects=10, resume=10, attitude=10)
    v = res.build_view(FORM, scoring, {})
    row = {r["id"]: r for r in v["criteria"]}
    assert row["overall"]["ai_value"] == round((10 * 6 + 4) / 7, 1) == 9.1
    assert row["appearance"]["ai_value"] is None and row["body_language"]["source"] == "interviewer"


def test_interviewer_fields_have_no_ai_value_but_count_in_the_mean_once_entered():
    v = res.build_view(FORM, ai(**FULL), {"criteria": {"appearance": {"value": 3, "note": "late"}}})
    row = {r["id"]: r for r in v["criteria"]}
    assert row["appearance"]["value"] == 3 and row["appearance"]["ai_value"] is None and v["overall_score"] == round((7 * 8 + 3) / 8, 1) == 7.4


def test_an_override_flows_into_the_overall_score_band_and_recommendation():
    scoring = {**ai(**FULL), "coding_best_pct": 100.0}
    base = res.build_view(FORM, scoring, {})
    assert base["overall_score"] == 8.0 and base["overall_assessment"].startswith("Very good") and base["recommendation"].startswith("Yes")
    low = res.build_view(FORM, scoring, {"criteria": {"technical": {"value": 2}, "problem_solving": {"value": 2}}})
    assert low["overall_score"] < 8.0 and not low["recommendation"].startswith("Yes")
    assert low["criteria"][4]["ai_value"] == 8 and low["criteria"][4]["value"] == 2          # the AI value is never lost
    assert low["overall_assessment_ai"] == base["overall_assessment"]


def test_overrides_are_clamped_to_the_scale():
    v = res.build_view(FORM, ai(**FULL), {"criteria": {"intro": {"value": 99}}})
    assert {r["id"]: r for r in v["criteria"]}["intro"]["value"] == 10


@pytest.mark.parametrize("score,label", [(9, "Excellent"), (8.5, "Excellent"), (7.4, "Very good"), (7, "Very good"), (6, "Good"), (4.5, "Average"), (3, "Needs Intensive Training Before Placement")])
def test_bands_follow_the_template_cut_offs(score, label):
    assert res.band(FORM["overall_assessment"], score).startswith(label)


def test_recommendation_needs_the_coding_guard_rule():
    assert res.recommend(FORM, 8.6, 60).startswith("Yes")
    assert not res.recommend(FORM, 8.6, 59.9).startswith("Yes")          # high score but the coding guard fails
    assert res.recommend(FORM, 6.0, 90).startswith("Conditional")
    assert res.recommend(FORM, 3.0, 100) == FORM["recommendation_fallback"]
    assert res.recommend(FORM, None, 100) == FORM["recommendation_fallback"]


def test_overall_with_nothing_scored_is_empty_not_zero():
    v = res.build_view(FORM, {"criteria": {}, "text": {}, "coding_best_pct": 0}, {})
    assert v["overall_score"] is None and v["overall_assessment"] is None


def test_text_overrides_replace_the_ai_draft_but_keep_it():
    scoring = {**ai(**FULL), "text": {"Strengths Observed": "AI text"}}
    v = res.build_view(FORM, scoring, {"text": {"Strengths Observed": "My text"}})
    t = {x["label"]: x for x in v["text_fields"]}["Strengths Observed"]
    assert t["ai_value"] == "AI text" and t["value"] == "My text" and t["override"] == "My text"


def test_csv_neutralises_spreadsheet_formulas_from_model_text():
    scoring = {**ai(**FULL), "text": {"Strengths Observed": "=HYPERLINK(\"http://evil\")", "Areas for Improvement": "+1", "Specific Suggestions to the Student": "fine"}}
    out = res.to_csv(res.build_view(FORM, scoring, {}))
    assert "'=HYPERLINK" in out and "'+1" in out and "\n=" not in out
    assert out.splitlines()[0] == "Item,AI value,Final value" and "Overall Employability and Suitability,8.0,8.0" in out


# ---------------------------------------------------------------- merging judge runs
ITEMS = [{"id": "A", "answer": "A stack is last in first out and I used it for undo", "skipped": False, "key_points": [{"point": "p"}] * 4},
         {"id": "B", "answer": "", "skipped": True, "key_points": [{"point": "p"}] * 3}]


def run(score, ev):
    return [{"id": "A", "score": score, "points_covered": [0, 1], "points_missed": [2, 3], "evidence": ev, "confidence": "high"}]


def test_two_runs_are_averaged_and_skipped_questions_are_zero_without_a_call():
    q = se.merge_runs(ITEMS, [run(6, ["last in first out"]), run(8, ["I used it for undo"])], RED)
    assert q["A"]["score"] == 7.0 and q["A"]["needs_review"] is False and q["A"]["covered"] == [0, 1]
    assert q["B"]["score"] == 0.0 and q["B"]["runs"] == []


def test_large_disagreement_between_runs_is_flagged_and_lowers_confidence():
    q = se.merge_runs(ITEMS, [run(3, []), run(7, [])], RED)
    assert q["A"]["needs_review"] is True and q["A"]["confidence"] == "low" and q["A"]["score"] == 5.0


def test_invented_quotes_are_dropped_and_real_ones_kept():
    q = se.merge_runs(ITEMS, [run(7, ["last in first out", "I built a compiler from scratch", "Priya said so"])], RED)
    assert "Priya said so" not in q["A"]["evidence"]
    assert q["A"]["evidence"] == ["last in first out"]


def test_a_question_the_judge_forgot_is_marked_for_review_not_zero():
    q = se.merge_runs(ITEMS, [[]], RED)
    assert q["A"]["score"] is None and q["A"]["needs_review"] is True


# ---------------------------------------------------------------- anchors
def evid(qs, coding):
    return {"questions": qs, "coding": coding, "resume": "", "profile": {}, "engine_flags": [], "profile_warnings": []}


def test_anchors_come_from_graded_answers_and_hidden_tests_and_sql_counts_for_both():
    qs = [{"id": "T1", "state": "tech_qs", "answer": "x", "skipped": False, "nudges": 0}, {"id": "T2", "state": "tech_qs_more", "answer": "x", "skipped": False, "nudges": 0},
          {"id": "I1", "state": "intro", "answer": "x", "skipped": False, "nudges": 0}, {"id": "W1", "state": "resume_warmup", "answer": "x", "skipped": False, "nudges": 0}]
    q = {"T1": {"score": 8.0}, "T2": {"score": 6.0}, "I1": {"score": 9.0}, "W1": {"score": 3.0}}
    coding = [{"kind": "coding", "pct": 100.0}, {"kind": "coding", "pct": 40.0}, {"kind": "sql", "pct": 100.0}]
    a = se.anchors(evid(qs, coding), q, FORM)
    assert a["technical"] == 8        # mean(8, 6, SQL 100% -> 10) = 8
    assert a["problem_solving"] == 8  # mean(100, 40, 100)=80% -> 1 + 9*0.8 = 8.2
    assert a["intro"] == 9 and a["projects"] == 4      # 3.0 of 10 -> 1 + 9 * 0.3 = 3.7 -> 4


def test_no_coding_means_no_problem_solving_anchor_and_zero_percent_maps_to_the_scale_floor():
    assert se.anchors(evid([], []), {}, FORM)["problem_solving"] is None
    assert se.anchors(evid([], [{"kind": "coding", "pct": 0.0}]), {}, FORM)["problem_solving"] == 1


def test_assemble_keeps_technical_and_problem_solving_close_to_their_anchors():
    qs = [{"id": "T1", "state": "tech_qs", "answer": "stack lifo", "skipped": False, "nudges": 0, "question": "q", "topic": "t", "book_id": "Q1", "key_points": []}]
    ev = evid(qs, [{"kind": "coding", "pct": 20.0, "level": 1, "code": "", "tests_passed": 1, "tests_total": 5, "timeouts": 2}])
    q = {"T1": {"score": 4.0, "needs_review": False}}
    anch = se.anchors(ev, q, FORM)
    raw = {"criteria": [{"id": i, "score": 10, "evidence": ["stack lifo"], "confidence": "high"} for i in ("communication", "intro", "technical", "problem_solving", "projects", "resume", "attitude")],
           "strengths": "Good.", "areas": "Gaps.", "suggestions": "Practise."}
    out = se.assemble(FORM, ev, q, anch, [], raw, RED)
    assert out["criteria"]["technical"]["score"] == anch["technical"] + 1 == 6
    assert out["criteria"]["problem_solving"]["score"] == anch["problem_solving"] + 1
    assert out["criteria"]["problem_solving"]["confidence"] == "medium" or out["criteria"]["problem_solving"]["confidence"] == "low"
    notes = " ".join(out["criteria"]["problem_solving"]["evidence"])
    assert "20.0% of hidden tests (1/5)" in notes and "2 timed out" in notes                 # evidence only, no extra deduction
    assert out["criteria"]["communication"]["score"] == 10 and out["criteria"]["communication"]["evidence"] == ["stack lifo"]


def test_the_comment_prompt_forbids_guessing_the_candidates_gender():
    from backend.scoring import prompts
    assert "they" in prompts.FORM_SYSTEM and "never use he, she" in prompts.FORM_SYSTEM
