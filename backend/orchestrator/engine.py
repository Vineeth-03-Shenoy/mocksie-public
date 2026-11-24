"""Interview engine: connects the deterministic state machine to the interviewer model (gpt-5.4-mini) and the live
quick grade (gpt-5.4-nano). Text-first: every public method takes plain data and returns a list of events, so the
voice pipeline (milestone 2) drives the very same engine.

Who decides what:
  - the ENGINE picks every question card, counts questions, runs timers, enforces the early-end rule and the state
    transitions (backend/orchestrator/state_machine.py);
  - the MODEL phrases questions and follow-ups and may REQUEST transitions through tools; every tool call is
    validated here and may be refused;
  - the model sees exactly one card at a time, never a hidden test, and never the candidate's real name or contact
    details: everything candidate-supplied is redacted and fenced as untrusted data before it is sent.
Events: {"type": "bot", "text"} | {"type": "coding_start"|"sql_start", "problem", "timer_s"} | {"type": "state"} |
        {"type": "end"} | {"type": "notice"}; each carries "remaining_s".
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from backend.content import bank as qbank
from backend.guardrails import injection
from backend.guardrails.pii import Redactor
from backend.llm.client import LLMClient
from backend.orchestrator import grading
from backend.orchestrator.prompts import INTERVIEWER_SYSTEM
from backend.orchestrator.state_machine import PAUSED_STATES, Config, Session, State
from backend.orchestrator.tools import TOOLS

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 14
MAX_ANSWER_CHARS = 4000
QUESTION_STATES = {State.INTRO, State.RESUME_WARMUP, State.BEHAVIOURAL, State.TECH_QS, State.TECH_QS_MORE}
STAGE_OF = {State.INTRO: "intro", State.RESUME_WARMUP: "warmup", State.BEHAVIOURAL: "behavioural",
            State.TECH_QS: "tech", State.TECH_QS_MORE: "tech", State.CANDIDATE_QUESTIONS: "candidate_questions"}
CLOSE_INSTRUCTION = ("Thank the candidate warmly and close the interview in two sentences. "
                     "Do not mention scores, results or how it went.")
FALLBACK_TEXT = "Thanks. Could you tell me a little more about that?"


@dataclass
class QuestionRecord:
    id: str
    state: str
    question: str
    asked_at: int
    answers: list[str] = field(default_factory=list)   # redacted candidate text for this question
    followups: int = 0
    nudges: int = 0
    coverage: float | None = None
    covered: list[bool] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    graded: bool = False
    skipped: bool = False


ANSWER_DIRECTIVE = ("The candidate asked you something. If it is about the role, the team, tools, training, working life or the hiring process, answer briefly "
                    "(1 to 3 sentences) using ONLY the reference text. If the reference does not cover it, say so honestly and generically, for example "
                    "\"that varies by team; in general ...\", and do not invent specifics. If they have no more questions, call end_interview.")
DECLINE_DIRECTIVE = ("The candidate asked about their feedback, scores, performance or the outcome. Briefly and kindly say you cannot share that here and that "
                     "their interviewer will go through feedback with them, then invite any question about the role or process. Do not hint at how they did.")
OUTCOME_QUESTION = re.compile(
    r"\b(my|me|i)\b.{0,40}\b(feedback|scores?|marks|rating|grade|results?|performance|outcome|chances)\b"
    r"|\bhow (did|was|am) (i|my)\b|\bdid i (pass|clear|do well|get|make it|perform|fail)\b"
    r"|\b(will|would|can) i (get|be) (selected|hired|shortlisted|placed|through)\b|\b(am|was) i (selected|hired|shortlisted|rejected|through)\b"
    r"|\b(give|tell|share|show) (me )?(my |the )?(feedback|scores?|marks|results?|rating)\b|\bhow (good|bad|well)\b.{0,30}\b(i|my)\b", re.I)


def is_outcome_question(text: str) -> bool:
    """True for questions about the candidate's own feedback, scores, performance or outcome (never answered in the interview)."""
    return bool(OUTCOME_QUESTION.search(text))


class Engine:
    def __init__(self, *, llm: LLMClient, redactor: Redactor, bank: list[dict], profile: dict | None = None,
                 cfg: Config | None = None, jd_tags: list[str] | None = None, enabled_sections: set[str] | None = None,
                 seed: int | None = None, clock: Callable[[], float] = time.time, reference: str = "") -> None:
        self.llm, self.redactor, self.bank, self.clock = llm, redactor, bank, clock
        self.reference = reference.strip()   # role description + company FAQ, written by the interviewer (trusted)
        self._by_id = {e["id"]: e for e in bank}
        self.profile = profile or {}
        self.jd_tags = jd_tags or []
        self.enabled_sections = set(enabled_sections or ())
        self.seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
        self.rng = random.Random(self.seed)
        self.session = Session(cfg=cfg or Config(), resume_sql_heavy=bool(self.profile.get("mentions_sql")))
        self.started_at: float | None = None
        self.used_ids: set[str] = set()
        self.questions: list[QuestionRecord] = []
        self.current: QuestionRecord | None = None
        self.transcript: list[dict] = []
        self.counts: dict[str, int] = {}
        self.candidate_turns = 0
        self.flags: list[str] = []            # injection / output-guard notes for the interviewer console
        self.coding: list[dict] = []          # one record per coding / SQL problem
        self.coding_deadline: float | None = None
        self.target_level = 1
        self.finished = False
        self._events: list[dict] = []
        self._messages: list[dict] = []       # chat history sent to the interviewer model (redacted)

    # ------------------------------------------------------------------ time and events
    def _tick(self) -> None:
        if self.started_at is None:
            self.started_at = self.clock()
        self.session.elapsed_s = int(self.clock() - self.started_at)

    def _emit(self, **ev: Any) -> None:
        ev.setdefault("remaining_s", max(self.session.remaining_s(), 0))
        self._events.append(ev)

    def _begin(self) -> None:
        self._events = []
        self._tick()

    # ------------------------------------------------------------------ public API
    def start(self) -> list[dict]:
        self._begin()
        self.session.state = State.INTRO
        info = self._advance_and_setup()
        self._say("Greet the candidate warmly in one sentence and say this is a practice interview of about forty minutes. " + info["instruction"])
        return self._finish_turn()

    def candidate_message(self, text: str) -> list[dict]:
        self._begin()
        s = self.session
        if self.finished or s.state in PAUSED_STATES or s.state in (State.CLOSE, State.SCORING):
            self._emit(type="notice", text="Nothing to answer right now.")
            return self._events
        clean = self._intake(text)
        if s.state == State.CANDIDATE_QUESTIONS:
            self.candidate_turns += 1
            if self.candidate_turns > s.cfg.candidate_question_turns:
                s.state = State.CLOSE
                self._say(self._advance_and_setup()["instruction"], tools=False)
            else:
                if is_outcome_question(clean):   # code decides: feedback / scores / outcome are never answered
                    self._say(DECLINE_DIRECTIVE)
                else:
                    self._say(ANSWER_DIRECTIVE, with_reference=True)
            return self._finish_turn()
        q = self.current
        q.answers.append(clean)
        left = s.cfg.max_followups - q.followups
        if left <= 0:  # follow-up limit reached: the engine moves on without asking the model
            self._complete_current(skip=False)
            self._say("The candidate has answered. " + self._advance_and_setup()["instruction"])
        else:
            self._say(f"The candidate replied. Follow-ups left for this question: {left}. Ask one short follow-up on a missing key point, "
                      "or call mark_answer_complete if the answer is sufficient or they cannot say more.")
            if self.current is q and not q.graded:
                q.followups += 1
        return self._finish_turn()

    def candidate_silence(self) -> list[dict]:
        """No answer within the answer timeout (the browser decides when). First time: a gentle nudge; then a miss."""
        self._begin()
        q = self.current
        if self.finished or q is None or q.graded or self.session.state not in QUESTION_STATES:
            return self._events
        if q.nudges == 0 and not q.answers:
            q.nudges += 1
            self._say("The candidate has been silent. Say one gentle sentence inviting them to take a moment or ask for the question again.", tools=False)
        else:
            self._complete_current(skip=True)
            self._say("The candidate did not answer. " + self._advance_and_setup()["instruction"])
        return self._finish_turn()

    def coding_finished(self, pct: float, *, summary: dict | None = None) -> list[dict]:
        """Called by the coding API on Submit, Skip (pct 0) or auto-submit at zero."""
        self._begin()
        s = self.session
        if s.state not in (State.CODING_1, State.CODING_2):
            return self._events
        s.record_coding(pct)
        self.coding[-1].update({"pct": pct, **(summary or {})})
        self.coding_deadline = None
        self._say("The candidate finished the coding problem. Welcome them back in one sentence without saying how they did. "
                  + self._advance_and_setup()["instruction"])
        return self._finish_turn()

    def sql_finished(self, pct: float, *, summary: dict | None = None) -> list[dict]:
        self._begin()
        if self.session.state != State.SQL:
            return self._events
        self.session.record_sql(pct)
        self.coding[-1].update({"pct": pct, **(summary or {})})
        self.coding_deadline = None
        self._say("The candidate finished the SQL question. Welcome them back in one sentence without saying how they did. "
                  + self._advance_and_setup()["instruction"])
        return self._finish_turn()

    def tick_events(self) -> list[dict]:
        """Call periodically: enforces the 40-minute hard stop even if nobody says anything."""
        self._begin()
        s = self.session
        if self.finished or s.state in (State.CLOSE, State.SCORING) or s.state in PAUSED_STATES or s.remaining_s() > 0:
            return self._events  # coding/SQL end through their own timer, which auto-submits at zero
        s.advance()
        self._say(self._advance_and_setup()["instruction"], tools=False)
        return self._finish_turn()

    # ------------------------------------------------------------------ intake
    def _intake(self, text: str) -> str:
        clean = self.redactor.redact((text or "").strip()[:MAX_ANSWER_CHARS])
        clean, rules = injection.neutralize(clean)
        if rules:
            self.flags.append("instruction-like text in an answer was removed: " + ", ".join(rules))
        self._log("candidate", clean)
        self._messages.append({"role": "user", "content": injection.wrap_untrusted("candidate_transcript", clean)})
        return clean

    def _log(self, role: str, text: str, **extra: Any) -> None:
        self.transcript.append({"t": self.session.elapsed_s, "role": role, "text": text, "state": self.session.state.value,
                                "q": self.current.id if self.current else None, **extra})

    # ------------------------------------------------------------------ card selection
    def _pick(self, stage: str, **kw) -> dict | None:
        entries = qbank.pool(self.bank, stage=stage, exclude_ids=self.used_ids, enabled_sections=self.enabled_sections,
                             jd_tags=self.jd_tags, **kw)
        return qbank.pick(entries, self.jd_tags, self.rng)

    def _next_card(self, state: State) -> dict | None:
        s = self.session
        if state == State.INTRO:  # "Tell me about yourself" first when available
            first = self._by_id.get("OPEN-001")
            if first and first["id"] not in self.used_ids:
                return first
        if state in (State.TECH_QS, State.TECH_QS_MORE):
            top = s.max_tech_level()
            return self._pick("tech", difficulty=min(self.target_level, top), max_level=top) or self._pick("tech", max_level=top)
        return self._pick(STAGE_OF[state])

    def _quota(self, state: State) -> int:
        c = self.session.cfg
        return {State.INTRO: 1, State.RESUME_WARMUP: c.warmup_questions, State.BEHAVIOURAL: c.behavioural_questions,
                State.TECH_QS: c.tech_questions, State.TECH_QS_MORE: c.tech_more_questions,
                State.CANDIDATE_QUESTIONS: 1}.get(state, 0)

    def _open_question(self, card: dict) -> None:
        state = self.session.state
        self.used_ids.add(card["id"])
        self.counts[state.value] = self.counts.get(state.value, 0) + 1
        self.current = QuestionRecord(id=card["id"], state=state.value, question=card["question"], asked_at=self.session.elapsed_s)
        self.questions.append(self.current)

    # ------------------------------------------------------------------ completing a question and moving on
    def _complete_current(self, *, skip: bool) -> None:
        q = self.current
        if q is None or q.graded:
            return
        q.graded = True
        card = self._by_id[q.id]
        if self.session.state not in QUESTION_STATES or card["type"] != "concept":
            return
        g = grading.quick_grade(self.llm, self.redactor, card, q.answers)
        q.coverage, q.covered, q.evidence = g["coverage"], g["covered"], g["evidence"]
        q.skipped = skip or g["coverage"] is None
        tech = self.session.state in (State.TECH_QS, State.TECH_QS_MORE)
        if tech or q.coverage is None:
            self.session.record_grade(q.coverage)  # strikes and the strong streak come from technical questions;
        else:                                       # elsewhere only "no answer / skip" is a strike, low coverage is not
            self.session.strikes = 0
        if tech:
            c, cfg = q.coverage, self.session.cfg
            if c is not None and c >= cfg.strong_coverage:
                self.target_level = min(self.target_level + 1, 3)
            elif c is None or c < cfg.miss_coverage:
                self.target_level = max(self.target_level - 1, 1)
        self._log("grade", f"coverage={q.coverage}", coverage=q.coverage)

    def _advance_and_setup(self) -> dict:
        """Move the flow forward until something needs the model's voice: the next question, a coding or SQL task,
        the candidate's own questions, or the close. Returns {"state", "card"?, "instruction"} for the model."""
        s = self.session
        for _ in range(12):
            st = s.state
            if st in QUESTION_STATES:
                if self.counts.get(st.value, 0) < self._quota(st) and not self._time_up():
                    card = self._next_card(st)
                    if card:
                        self._open_question(card)
                        return {"state": st.value, "card": qbank.llm_card(card), "instruction": self._ask_instruction(st)}
                s.advance()
            elif st in (State.CODING_1, State.CODING_2):
                info = None if self._task_done(st) else self._start_coding_problem()
                if info:
                    return info
                s.advance()
            elif st == State.SQL:
                info = None if self._task_done(st) else self._start_sql()
                if info:
                    return info
                s.advance()
            elif st == State.CANDIDATE_QUESTIONS:
                if self.counts.get(st.value, 0) == 0:
                    card = self._next_card(st)
                    if card:
                        self._open_question(card)
                        return {"state": st.value, "card": qbank.llm_card(card),
                                "instruction": "Ask whether the candidate has any questions about the role, the team or the hiring process. Keep answers short."}
                s.state = State.CLOSE
            else:  # CLOSE (SCORING is set by _finish_turn after the farewell)
                self.current = None
                return {"state": "close", "instruction": CLOSE_INSTRUCTION}
        return {"state": s.state.value, "instruction": CLOSE_INSTRUCTION}

    def _task_done(self, st: State) -> bool:
        """True when this coding/SQL state's problem was already presented and finished (so the flow must move on)."""
        rec = next((r for r in reversed(self.coding) if r["state"] == st.value), None)
        return bool(rec and "pct" in rec)

    def _time_up(self) -> bool:
        return self.session.remaining_s() <= 0 or self.session.should_end_early()

    @staticmethod
    def _ask_instruction(state: State) -> str:
        if state == State.INTRO:
            return "Then ask the current card."
        if state == State.RESUME_WARMUP:
            return "Ask the current card. Where natural, tie it to something in the candidate profile (a project or skill)."
        return "Acknowledge the previous answer neutrally in a few words (never say right or wrong), then ask the current card."

    def _start_coding_problem(self) -> dict | None:
        s = self.session
        n = 1 if s.state == State.CODING_1 else 2
        level = s.coding_level(n)
        card = self._pick("coding", difficulty=level)
        if card is None:
            return None
        self.used_ids.add(card["id"])
        secs = s.cfg.coding_timer_s[level]
        self.coding_deadline = self.clock() + secs
        self.coding.append({"id": card["id"], "state": s.state.value, "kind": "coding", "level": level, "timer_s": secs, "started_at": s.elapsed_s})
        self.current = None
        self._emit(type="coding_start", problem=qbank.browser_problem(card), timer_s=secs, level=level)
        return {"state": s.state.value, "instruction": f"Tell the candidate a coding problem is now on their screen, they have {secs // 60} minutes, "
                "they can run the visible sample tests and submit, and the conversation pauses until they finish. One or two sentences. Do not describe the solution."}

    def _start_sql(self) -> dict | None:
        s = self.session
        card = self._pick("sql")
        if card is None:
            return None
        self.used_ids.add(card["id"])
        secs = s.cfg.sql_timer_s
        self.coding_deadline = self.clock() + secs
        self.coding.append({"id": card["id"], "state": s.state.value, "kind": "sql", "level": card["difficulty"], "timer_s": secs, "started_at": s.elapsed_s})
        self.current = None
        self._emit(type="sql_start", problem=qbank.browser_problem(card), timer_s=secs)
        return {"state": s.state.value, "instruction": f"Tell the candidate a short SQL question is on their screen only (do not read it aloud), they have {secs // 60} minutes, and they can submit or skip."}

    # ------------------------------------------------------------------ the model turn
    def _context_message(self) -> dict:
        s = self.session
        ctx = {"state": s.state.value, "minutes_left": max(s.remaining_s(), 0) // 60,
               "question_number_in_stage": self.counts.get(s.state.value, 0),
               "current_card": qbank.llm_card(self._by_id[self.current.id]) if self.current else None}
        return {"role": "user", "content": "ORCHESTRATOR (trusted): " + json.dumps(ctx, ensure_ascii=False)}

    def _profile_message(self) -> dict | None:
        """The resume summary, fenced as data. No label text of ours sits in this (term-checked) message."""
        if not self.profile:
            return None
        return {"role": "user", "content": injection.wrap_untrusted("candidate_profile", json.dumps(self.profile, ensure_ascii=False))}

    def _reference_message(self) -> dict:
        text = self.reference or "(no role description or company FAQ has been provided)"
        return {"role": "user", "content": "ORCHESTRATOR (trusted): REFERENCE for answering the candidate's questions (written by the interviewer):\n" + text}

    def _say(self, directive: str, tools: bool = True, with_reference: bool = False) -> str:
        """One model turn, including tool rounds. The spoken text goes to the transcript and the event list."""
        msgs: list[dict] = [{"role": "system", "content": INTERVIEWER_SYSTEM}, self._context_message()]
        trusted = [1]  # our own card text: exempt from the typed-name check (contact patterns are still checked)
        prof = self._profile_message()
        if prof:
            msgs.append(prof)
        if with_reference:
            msgs.append(self._reference_message())
            trusted.append(len(msgs) - 1)
        msgs += self._messages[-HISTORY_MESSAGES:]
        msgs.append({"role": "user", "content": "ORCHESTRATOR (trusted): " + directive})
        trusted.append(len(msgs) - 1)
        text, last_instruction = "", ""
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self.llm.complete("interviewer", msgs, trusted=tuple(trusted), **({"tools": TOOLS} if tools else {}))
            text = (resp["content"] or "").strip()
            if not resp["tool_calls"]:
                break
            msgs.append({"role": "assistant", "content": resp["content"], "tool_calls": resp["tool_calls"]})
            for c in resp["tool_calls"]:
                result = self._run_tool(c["function"]["name"], c["function"].get("arguments") or "{}")
                last_instruction = result.get("instruction", last_instruction)
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, ensure_ascii=False)})
                trusted.append(len(msgs) - 1)
        if not text and last_instruction:  # the model only called tools: ask once more, without tools, for the words
            msgs.append({"role": "user", "content": "ORCHESTRATOR (trusted): " + last_instruction})
            trusted.append(len(msgs) - 1)
            text = (self.llm.complete("interviewer", msgs, trusted=tuple(trusted))["content"] or "").strip()
        text = self._guard_output(text)
        if text:
            self._messages.append({"role": "assistant", "content": text})
            self._log("interviewer", text)
            self._emit(type="bot", text=text)
        return text

    def _guard_output(self, text: str) -> str:
        """Never let the model read out the current card's model answer or a key point."""
        text = re.sub(r"[*_`#>]+", "", text).strip()
        lines: list[str] = []
        for line in (ln.strip() for ln in text.splitlines()):
            if line and (not lines or lines[-1] != line):  # the model sometimes repeats a sentence on the next line
                lines.append(line)
        text = " ".join(lines)
        card = self._by_id.get(self.current.id) if self.current else None
        if card and card["type"] == "concept":
            spoken = " ".join(re.findall(r"\w+", text.lower()))
            question = " ".join(re.findall(r"\w+", card["question"].lower()))
            for source in [kp["point"] for kp in card["key_points"]] + [card.get("model_answer", "")]:
                words = re.findall(r"\w+", source.lower())
                size = min(7, len(words))        # a whole short key point, or any 7-word run of a long one
                if size < 4 or " ".join(words[:size]) in question:
                    continue
                if any(" ".join(words[i:i + size]) in spoken for i in range(len(words) - size + 1)):
                    self.flags.append("an interviewer reply repeated the model answer and was replaced")
                    return FALLBACK_TEXT
        return text

    # ------------------------------------------------------------------ tools (requests, validated here)
    def _run_tool(self, name: str, raw_args: str) -> dict:
        s = self.session
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            return {"ok": False, "error": "arguments were not valid JSON"}
        if name == "note_claimed_skill":
            s.note_claimed_skill(str(args.get("skill", "")))
            return {"ok": True}
        if name in ("mark_answer_complete", "next_question"):
            q = self.current
            if q is None or s.state not in QUESTION_STATES:
                return {"ok": False, "error": "there is no open question to complete"}
            if name == "mark_answer_complete" and not q.answers:
                return {"ok": False, "error": "the candidate has not answered yet; wait for their reply"}
            self._complete_current(skip=(name == "next_question" and not q.answers))
            return {"ok": True, **self._advance_and_setup()}
        if name == "start_coding":
            if s.state in (State.CODING_1, State.CODING_2):
                return {"ok": True, "note": "coding is already starting"}
            return {"ok": False, "error": "coding starts automatically after the technical questions; keep asking questions"}
        if name == "end_interview":
            if not s.request_end():
                return {"ok": False, "error": "the interview cannot end yet; continue with the current question"}
            s.state = State.CLOSE
            return {"ok": True, **self._advance_and_setup()}
        return {"ok": False, "error": f"unknown tool {name}"}

    # ------------------------------------------------------------------ end of turn
    def _finish_turn(self) -> list[dict]:
        s = self.session
        if s.state == State.CLOSE:
            if not any(e["type"] == "bot" for e in self._events):  # a farewell must always be spoken
                self._say(CLOSE_INSTRUCTION, tools=False)
            s.state = State.SCORING
        if s.state == State.SCORING and not self.finished:
            self.finished = True
            self._emit(type="end", reason="finished")
        self._emit(type="state", state=s.state.value)
        return self._events

    # ------------------------------------------------------------------ persistence
    _SESSION_FIELDS = ("elapsed_s", "strikes", "strong_streak", "coding1_pct", "best_coding_pct", "sql_pct", "sql_asked",
                       "resume_sql_heavy", "claimed_sql")

    def to_dict(self) -> dict:
        s = self.session
        return {
            "seed": self.seed, "started_at": self.started_at, "cfg": asdict(s.cfg),
            "session": {**{k: getattr(s, k) for k in self._SESSION_FIELDS}, "state": s.state.value},
            "used_ids": sorted(self.used_ids), "questions": [asdict(q) for q in self.questions],
            "current": self.current.id if self.current else None, "transcript": self.transcript, "counts": self.counts,
            "candidate_turns": self.candidate_turns, "flags": self.flags, "coding": self.coding,
            "coding_deadline": self.coding_deadline, "target_level": self.target_level, "finished": self.finished,
            "messages": self._messages,
        }

    def load(self, d: dict) -> "Engine":
        cfg = Config(**{**d["cfg"], "coding_timer_s": {int(k): v for k, v in d["cfg"]["coding_timer_s"].items()}})
        self.session = Session(cfg=cfg, state=State(d["session"]["state"]), **{k: v for k, v in d["session"].items() if k != "state"})
        self.seed, self.started_at = d["seed"], d["started_at"]
        self.used_ids = set(d["used_ids"])
        self.rng = random.Random(self.seed + len(self.used_ids))
        self.questions = [QuestionRecord(**q) for q in d["questions"]]
        self.current = next((q for q in reversed(self.questions) if q.id == d["current"]), None)
        self.transcript, self.counts, self.candidate_turns = d["transcript"], d["counts"], d["candidate_turns"]
        self.flags, self.coding, self.coding_deadline = d["flags"], d["coding"], d["coding_deadline"]
        self.target_level, self.finished, self._messages = d["target_level"], d["finished"], d["messages"]
        return self
