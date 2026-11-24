"""Deterministic interview flow (architecture 4.4 and section 11).

INTRO -> RESUME_WARMUP -> BEHAVIOURAL (0-2 Qs) -> TECH_QS -> CODING_1
      -> CODING_2 (if >=80% and time) or TECH_QS_MORE
      -> SQL (only if a trigger fired and time remains) -> CANDIDATE_QUESTIONS -> CLOSE -> SCORING

The LLM may *request* transitions via tools; this module validates them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    INTRO = "intro"
    RESUME_WARMUP = "resume_warmup"
    BEHAVIOURAL = "behavioural"
    TECH_QS = "tech_qs"
    CODING_1 = "coding_1"
    TECH_QS_MORE = "tech_qs_more"
    CODING_2 = "coding_2"
    SQL = "sql"
    CANDIDATE_QUESTIONS = "candidate_questions"
    CLOSE = "close"
    SCORING = "scoring"


# Voice pipeline is stopped in these states (architecture 4.6; SQL "runs like coding").
CODING_STATES = {State.CODING_1, State.CODING_2}
PAUSED_STATES = CODING_STATES | {State.SQL}


@dataclass
class Config:
    hard_stop_s: int = 40 * 60
    coding_pass_pct: float = 80.0
    early_end_strikes: int = 3
    miss_coverage: float = 0.25        # key-point coverage below this is a "miss"
    strong_coverage: float = 0.75      # at or above this is a "strong" answer (assumption, tune in dry runs)
    level3_after_strong: int = 2       # strong answers in a row before level-3 concept questions
    behavioural_questions: int = 1     # 0-2
    coding_start_level: int = 1        # harder problem = start + 1
    allow_level3_coding: bool = False  # the single level-3 problem is off by default
    sql_timer_s: int = 8 * 60
    sql_trigger_pct: float = 80.0      # best coding score that triggers the SQL question
    coding_timer_s: dict[int, int] = field(default_factory=lambda: {1: 600, 2: 900, 3: 1200})
    # question counts per stage (the flow engine uses these; the state machine only validates transitions)
    warmup_questions: int = 1
    tech_questions: int = 4            # before the first coding problem
    tech_more_questions: int = 3       # after coding 1 when coding 2 is not taken
    max_followups: int = 2             # follow-ups per question before the engine moves on
    candidate_question_turns: int = 3  # exchanges allowed when the candidate asks their own questions

    def __post_init__(self) -> None:
        if not 0 <= self.behavioural_questions <= 2:
            raise ValueError("behavioural_questions must be 0-2")
        if not 1 <= self.coding_start_level <= 3:
            raise ValueError("coding_start_level must be 1-3")
        if not 0 <= self.warmup_questions <= 2 or self.tech_questions < 1 or self.tech_more_questions < 0 or self.max_followups < 0:
            raise ValueError("invalid question counts")


@dataclass
class Session:
    cfg: Config = field(default_factory=Config)
    state: State = State.INTRO
    elapsed_s: int = 0
    strikes: int = 0                   # consecutive misses (no answer, skip, or low coverage)
    strong_streak: int = 0             # consecutive strong answers
    coding1_pct: float | None = None
    best_coding_pct: float = 0.0
    sql_pct: float | None = None       # evidence only; never gates anything
    sql_asked: bool = False
    resume_sql_heavy: bool = False     # from the cleaned resume profile
    claimed_sql: bool = False          # set by the note_claimed_skill("sql") tool

    # ---- grading signals ------------------------------------------------
    def record_grade(self, coverage: float | None) -> None:
        """Live quick grade of one verbal answer. None = no answer within the timeout, or a skip."""
        miss = coverage is None or coverage < self.cfg.miss_coverage
        self.strikes = self.strikes + 1 if miss else 0
        strong = coverage is not None and coverage >= self.cfg.strong_coverage
        self.strong_streak = self.strong_streak + 1 if strong else 0

    def max_tech_level(self) -> int:
        """Concept questions are levels 1-2; level 3 only after enough strong answers in a row."""
        return 3 if self.strong_streak >= self.cfg.level3_after_strong else 2

    def coding_level(self, n: int) -> int:
        """Level for the n-th coding problem (1 or 2). Level 3 only if explicitly allowed."""
        level = self.cfg.coding_start_level + (n - 1)
        return min(level, 3 if self.cfg.allow_level3_coding else 2)

    def note_claimed_skill(self, skill: str) -> None:
        """LLM tool: the candidate says they are good at something. Only 'sql' matters today."""
        if skill.strip().lower() == "sql":
            self.claimed_sql = True

    def should_end_early(self) -> bool:
        return self.strikes >= self.cfg.early_end_strikes

    def record_coding(self, pct: float) -> None:
        """pct of weighted hidden tests passed (0 for a skip)."""
        self.best_coding_pct = max(self.best_coding_pct, pct)
        if self.state == State.CODING_1:
            self.coding1_pct = pct

    def record_sql(self, pct: float) -> None:
        self.sql_pct = pct

    # ---- time -----------------------------------------------------------
    def remaining_s(self) -> int:
        return self.cfg.hard_stop_s - self.elapsed_s

    def can_start_coding(self, difficulty: int) -> bool:
        return self.remaining_s() >= self.cfg.coding_timer_s[difficulty]

    def should_ask_sql(self) -> bool:
        triggered = self.resume_sql_heavy or self.claimed_sql or self.best_coding_pct >= self.cfg.sql_trigger_pct
        return (not self.sql_asked) and triggered and self.remaining_s() >= self.cfg.sql_timer_s

    # ---- flow -----------------------------------------------------------
    def _after_coding_block(self) -> State:
        if self.should_ask_sql():
            self.sql_asked = True
            return State.SQL
        return State.CANDIDATE_QUESTIONS

    def advance(self) -> State:
        s = self.state
        if s not in (State.CLOSE, State.SCORING) and (self.remaining_s() <= 0 or self.should_end_early()):
            self.state = State.CLOSE
            return self.state

        if s == State.INTRO:
            self.state = State.RESUME_WARMUP
        elif s == State.RESUME_WARMUP:
            self.state = State.BEHAVIOURAL if self.cfg.behavioural_questions > 0 else State.TECH_QS
        elif s == State.BEHAVIOURAL:
            self.state = State.TECH_QS
        elif s == State.TECH_QS:
            self.state = State.CODING_1 if self.can_start_coding(self.coding_level(1)) else self._after_coding_block()
        elif s == State.CODING_1:
            passed = (self.coding1_pct or 0) >= self.cfg.coding_pass_pct
            if passed and self.can_start_coding(self.coding_level(2)) and self.coding_level(2) > self.coding_level(1):
                self.state = State.CODING_2
            else:
                self.state = State.TECH_QS_MORE
        elif s in (State.CODING_2, State.TECH_QS_MORE):
            self.state = self._after_coding_block()
        elif s == State.SQL:
            self.state = State.CANDIDATE_QUESTIONS
        elif s == State.CANDIDATE_QUESTIONS:
            self.state = State.CLOSE
        elif s == State.CLOSE:
            self.state = State.SCORING
        return self.state

    def request_end(self) -> bool:
        """LLM tool end_interview(): accepted from CANDIDATE_QUESTIONS onwards, unless the
        early-end rule fired or the hard stop hit. (Leaving full screen ends it outside the LLM.)"""
        return (
            self.state in (State.CANDIDATE_QUESTIONS, State.CLOSE, State.SCORING)
            or self.remaining_s() <= 0
            or self.should_end_early()
        )
