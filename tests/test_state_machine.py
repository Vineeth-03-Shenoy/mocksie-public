import pytest

from backend.orchestrator.state_machine import Config, PAUSED_STATES, Session, State


def run_to(s, target):
    guard = 0
    while s.state != target:
        s.advance()
        guard += 1
        assert guard < 20, f"never reached {target}"


def path(s):
    seen = [s.state]
    while s.state != State.SCORING:
        seen.append(s.advance())
    return seen


# ---- coding gate ------------------------------------------------------------
def test_passing_coding_unlocks_harder_question():
    s = Session()
    run_to(s, State.CODING_1)
    s.record_coding(90)
    assert s.advance() == State.CODING_2


def test_failed_coding_goes_back_to_verbal():
    s = Session()
    run_to(s, State.CODING_1)
    s.record_coding(50)
    assert s.advance() == State.TECH_QS_MORE


def test_coding_skipped_if_not_enough_time():
    s = Session()
    s.state = State.TECH_QS
    s.elapsed_s = 40 * 60 - 60
    assert s.advance() == State.CANDIDATE_QUESTIONS


def test_coding_levels_default_1_then_2_and_level3_is_off():
    s = Session()
    assert (s.coding_level(1), s.coding_level(2)) == (1, 2)
    hard = Session(cfg=Config(coding_start_level=2))
    assert hard.coding_level(2) == 2  # would be 3, but level 3 is off by default
    assert Session(cfg=Config(coding_start_level=2, allow_level3_coding=True)).coding_level(2) == 3


# ---- early end / end rules --------------------------------------------------
def test_three_misses_in_a_row_end_early():
    s = Session()
    for _ in range(3):
        s.record_grade(None)
    assert s.advance() == State.CLOSE


def test_low_coverage_counts_as_a_miss_and_a_good_answer_resets():
    s = Session()
    s.record_grade(0.1)
    s.record_grade(0.2)
    s.record_grade(0.5)
    assert s.strikes == 0
    s.record_grade(0.24)
    assert s.strikes == 1


def test_llm_cannot_end_early_in_the_flow():
    s = Session()
    s.elapsed_s = 180
    assert s.request_end() is False
    run_to(s, State.CANDIDATE_QUESTIONS)
    assert s.request_end() is True


# ---- level 3 gating ---------------------------------------------------------
def test_level3_concepts_need_two_strong_answers_in_a_row():
    s = Session()
    assert s.max_tech_level() == 2
    s.record_grade(0.9)
    assert s.max_tech_level() == 2
    s.record_grade(0.9)
    assert s.max_tech_level() == 3
    s.record_grade(0.5)  # not strong: streak resets
    assert s.max_tech_level() == 2


# ---- behavioural stage ------------------------------------------------------
def test_behavioural_stage_sits_between_warmup_and_tech():
    s = Session()
    seq = path(s)
    assert seq.index(State.RESUME_WARMUP) + 1 == seq.index(State.BEHAVIOURAL) == seq.index(State.TECH_QS) - 1


def test_behavioural_can_be_switched_off_and_is_bounded():
    s = Session(cfg=Config(behavioural_questions=0))
    assert State.BEHAVIOURAL not in path(s)
    for bad in (-1, 3):
        with pytest.raises(ValueError):
            Config(behavioural_questions=bad)


# ---- SQL stage --------------------------------------------------------------
def to_after_coding(s, coding_pct):
    run_to(s, State.CODING_1)
    s.record_coding(coding_pct)
    s.advance()                      # CODING_2 or TECH_QS_MORE
    if s.state == State.CODING_2:
        s.record_coding(coding_pct)
    return s.advance()


def test_no_trigger_no_sql():
    assert to_after_coding(Session(), 50) == State.CANDIDATE_QUESTIONS


@pytest.mark.parametrize("setup", ["resume", "claimed", "coding"])
def test_each_trigger_alone_asks_sql(setup):
    s = Session()
    pct = 50
    if setup == "resume":
        s.resume_sql_heavy = True
    elif setup == "claimed":
        s.note_claimed_skill("SQL")
    else:
        pct = 85
    assert to_after_coding(s, pct) == State.SQL


def test_sql_goes_after_coding_and_before_wrap_up_and_never_twice():
    s = Session()
    s.resume_sql_heavy = True
    run_to(s, State.CODING_1)
    s.record_coding(90)
    seq = [s.advance() for _ in range(3)]
    assert seq == [State.CODING_2, State.SQL, State.CANDIDATE_QUESTIONS]
    assert not s.should_ask_sql()


def test_sql_needs_time_remaining():
    s = Session()
    s.resume_sql_heavy = True
    s.state = State.TECH_QS_MORE
    s.elapsed_s = 40 * 60 - 7 * 60   # 7 min left < 8 min SQL timer
    assert s.advance() == State.CANDIDATE_QUESTIONS


def test_sql_result_never_gates_anything():
    s = Session()
    s.resume_sql_heavy = True
    run_to(s, State.SQL)
    s.record_sql(0.0)
    assert s.advance() == State.CANDIDATE_QUESTIONS
    assert s.sql_pct == 0.0


def test_claiming_other_skills_does_not_trigger_sql():
    s = Session()
    s.note_claimed_skill("java")
    assert not s.claimed_sql


def test_voice_is_paused_for_coding_and_sql_only():
    assert PAUSED_STATES == {State.CODING_1, State.CODING_2, State.SQL}
