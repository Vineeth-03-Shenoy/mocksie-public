"""The SAMPLE question bank (6 concept questions, 2 coding problems, 1 SQL question) and the rules for what each audience may see."""
import json
import random

from backend.content import bank

BANK = bank.load()
CODING = [e for e in BANK if e["type"] == "coding"]
CONCEPT = [e for e in BANK if e["type"] == "concept"]
SQL = [e for e in BANK if e["type"] == "sql"]


def test_the_sample_bank_has_the_expected_shape():
    assert (len(CONCEPT), len(CODING), len(SQL)) == (6, 2, 1)
    assert len({e["id"] for e in BANK}) == len(BANK) and len({e["book_id"] for e in BANK}) == len(BANK)
    assert {e["stage"] for e in CONCEPT} == {"intro", "warmup", "behavioural", "tech", "candidate_questions"}
    assert any(e["id"] == "OPEN-001" for e in BANK)           # the engine opens the interview with this one


def test_every_entry_has_question_answer_and_key_points():
    for e in BANK:
        assert e["question"] and e["model_answer"] and e["key_points"], e["id"]
        assert 1 <= e["difficulty"] <= 3
        assert all(1 <= kp["weight"] <= 5 and kp["point"] for kp in e["key_points"])


def test_the_sample_bank_validates_against_the_schema():
    jsonschema = __import__("pytest").importorskip("jsonschema")
    schema = json.loads(bank.BANK_PATH.with_name("question_bank.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(BANK, schema)


def test_coding_entries_are_judge_ready():
    for e in CODING:
        assert e["hidden_tests"] and e["sample_tests"] and set(e["starter_code"]) == {"python", "java", "c"}, e["id"]
        assert all(t["visible"] for t in e["sample_tests"]) and not any(t["visible"] for t in e["hidden_tests"])
        assert any(t["edge"] for t in e["hidden_tests"])


def test_llm_card_never_leaks_tests_or_notes():
    for e in BANK:
        card = json.dumps(bank.llm_card(e))
        for forbidden in ("hidden_tests", "hidden_datasets", "reference_query", "go_deeper", "red_flags"):
            assert forbidden not in card
    for e in CODING + SQL:
        assert "model_answer" not in bank.llm_card(e)


def test_browser_problem_has_no_hidden_tests_or_answers():
    for e in CODING:
        payload = bank.browser_problem(e)
        text = json.dumps(payload)
        assert "hidden_tests" not in text and "model_answer" not in text
        assert payload["sample_tests"] == [{"stdin": t["stdin"], "expected": t["expected"]} for t in e["sample_tests"]]
    sql = json.dumps(bank.browser_problem(SQL[0]))
    assert "hidden_datasets" not in sql and "reference_query" not in sql and "model_answer" not in sql


def test_pool_picks_by_stage_level_and_never_repeats():
    tech = bank.pool(BANK, stage="tech")
    assert {e["id"] for e in tech} == {"CS-001", "CS-002"}
    level1 = bank.pool(BANK, stage="tech", difficulty=1)
    assert [e["id"] for e in level1] == ["CS-001"]
    again = bank.pool(BANK, stage="tech", exclude_ids={"CS-001"})
    assert [e["id"] for e in again] == ["CS-002"]
    assert bank.pick([], None) is None


def test_pool_prefers_jd_tagged_questions_but_falls_back():
    tech = bank.pool(BANK, stage="tech", jd_tags=["web"])
    assert tech[0]["id"] == "CS-002"                                  # the web question is tagged, so it comes first
    assert bank.pick(bank.pool(BANK, stage="tech"), ["no-such-tag"], random.Random(1)) is not None


def test_default_pool_is_levels_1_and_2_only_until_level3_is_unlocked():
    capped = bank.pool(BANK, stage="tech", max_level=1)
    assert [e["id"] for e in capped] == ["CS-001"]


def test_coding_problems_are_one_easy_and_one_a_little_harder():
    assert [e["difficulty"] for e in CODING] == [1, 2]
    assert len(bank.pool(BANK, stage="coding", difficulty=1)) == 1 and len(bank.pool(BANK, stage="coding", difficulty=2)) == 1


def test_sql_is_its_own_stage_and_never_in_the_voice_pool():
    assert [e["id"] for e in SQL] == ["SQL-001"] and SQL[0]["stage"] == "sql" and not SQL[0]["voice_suitable"]
    assert not any(e["type"] == "sql" for e in bank.pool(BANK, stage="tech", max_level=3))
    assert [e["id"] for e in bank.pool(BANK, stage="sql", voice_only=False)] == ["SQL-001"]


def test_optional_sections_stay_off_unless_switched_on():
    optional = {**CONCEPT[3], "id": "AI-001", "optional": True, "jd_tags": ["ai"], "stage": "optional"}
    pool_off = bank.pool(BANK + [optional], stage="tech")
    assert "AI-001" not in {e["id"] for e in pool_off}
    assert "AI-001" in {e["id"] for e in bank.pool(BANK + [optional], stage="tech", enabled_sections={"AI"})}
    assert "AI-001" in {e["id"] for e in bank.pool(BANK + [optional], stage="tech", jd_tags=["ai"])}
