"""Interview routes. Admin routes need the interviewer cookie; session routes are keyed by the candidate token and can
only read the candidate's own conversation and current task: never results, scores, notes or other interviews.

Coding / SQL tasks (one unified set of endpoints, the interview state decides which kind it is):
  GET  /api/session/{token}            view: messages, state, and the open task with its timer and saved draft
  POST /api/session/{token}/task/save    autosave the draft (also used for auto-submit at zero)
  POST /api/session/{token}/task/run     run the VISIBLE sample tests / the sample dataset (nothing hidden)
  POST /api/session/{token}/task/submit  judge against the hidden tests / datasets, hand the percentage to the engine
  POST /api/session/{token}/task/skip    "I don't know": counts as 0, never gates anything else
The candidate is never told a score. At timer zero the server auto-submits the last saved draft (even if the browser is gone).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.content import bank as qbank
from backend.guardrails.pdf_sanitize import ResumeRejected
from backend.guardrails import injection
from backend.guardrails.pii import Redactor, find_pii
from backend.judge import sql_judge
from backend.judge.client import LANGUAGES, MAX_CODE_BYTES, CodeTooLarge, PistonClient, judge_code, piston_sql_executor, run_samples
from backend.llm.client import LLMClient, PIIBlocked
from backend.orchestrator.engine import Engine
from backend.orchestrator.state_machine import PAUSED_STATES, Config, State
from backend.resume.profile import extract_profile
from backend.store import Store
from backend.voice import settings as voice_settings

DEADLINE_GRACE_S = 3        # a submit that arrives a moment after zero (network lag) is still accepted
MAX_RUNS_PER_TASK = 60
MIN_RUN_GAP_S = 1.5
MAX_ROWS_SHOWN = 50
MAX_PASTE_REPORT = 200_000
STATUS_LABEL = {"created": "Not started", "active": "In progress", "finished": "Scoring", "scoring": "Scoring", "ready": "Ready", "finalized": "Finalized"}


class NewInterview(BaseModel):
    candidate_name: str = Field(min_length=2, max_length=80)
    also_hide: str = Field(default="", max_length=500)
    jd_tags: list[str] = Field(default_factory=list, max_length=12)
    enabled_sections: list[str] = Field(default_factory=list, max_length=3)
    behavioural_questions: int = Field(default=1, ge=0, le=2)


class Message(BaseModel):
    text: str = Field(max_length=4000)


class TaskBody(BaseModel):
    language: str = Field(default="python", max_length=10)
    code: str = Field(default="", max_length=MAX_CODE_BYTES)


class TaskEvent(BaseModel):
    kind: str = Field(pattern="^paste$")
    chars: int = Field(ge=0, le=MAX_PASTE_REPORT)


class SettingsBody(BaseModel):
    company_faq: str | None = Field(default=None, max_length=3000)
    voice: dict | None = None


JD_PATH = Path(__file__).resolve().parent.parent / "content" / "jd.md"


class JudgeDown(Exception):
    """The sandbox failed (not the candidate's fault): nothing is scored, the candidate can retry."""


def build_router(*, store: Store, llm_factory: Callable[[Redactor], LLMClient], require_admin: Callable, bank: list[dict],
                 clock: Callable[[], float], max_pages: int, max_bytes: int,
                 judge: PistonClient, on_finished: Callable[[str], None] = lambda _id: None) -> tuple[APIRouter, APIRouter]:
    admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
    session = APIRouter(prefix="/api/session")
    by_id = {e["id"]: e for e in bank}
    run_sql = piston_sql_executor(judge)

    def redactor_for(rec: dict) -> Redactor:
        d = rec["data"]
        return Redactor(d["candidate_name"], d.get("also_hide", ""))

    def reference_text() -> str:
        """Role description (content/jd.md) plus the optional company FAQ from Settings: what the agent may answer from."""
        jd = JD_PATH.read_text(encoding="utf-8").strip() if JD_PATH.exists() else ""
        faq = store.get_setting("company_faq").strip()
        return jd + ("\n\nCompany FAQ:\n" + faq if faq else "")

    def engine_for(rec: dict) -> Engine:
        d = rec["data"]
        red = redactor_for(rec)
        eng = Engine(llm=llm_factory(red), redactor=red, bank=bank, profile=d.get("profile") or {}, jd_tags=d.get("jd_tags", []), reference=reference_text(),
                     enabled_sections=set(d.get("enabled_sections", [])),
                     cfg=Config(behavioural_questions=d.get("behavioural_questions", 1)), clock=clock)
        if d.get("engine"):
            eng.load(d["engine"])
        return eng

    # ---------------- tasks
    def open_task(eng: Engine) -> dict | None:
        if eng.session.state not in PAUSED_STATES or not eng.coding or "pct" in eng.coding[-1]:
            return None
        return eng.coding[-1]

    def task_view(rec: dict, eng: Engine) -> dict | None:
        t = open_task(eng)
        if not t:
            return None
        entry = by_id[t["id"]]
        left = max(0, int(eng.coding_deadline - clock())) if eng.coding_deadline else t["timer_s"]
        return {"kind": t["kind"], "problem": qbank.browser_problem(entry), "timer_s": t["timer_s"], "remaining_s": left,
                "draft": (rec["data"].get("drafts") or {}).get(t["id"]), "languages": list(LANGUAGES) if t["kind"] == "coding" else []}

    def judge_and_finish(eng: Engine, t: dict, language: str, code: str, *, skipped: bool = False, auto: bool = False) -> list[dict]:
        entry = by_id[t["id"]]
        summary: dict = {"auto_submitted": auto, "runs": t.get("runs", 0)}
        if skipped:
            pct, summary["skipped"] = 0.0, True
        elif not code.strip():
            pct, summary["empty"] = 0.0, True
        elif t["kind"] == "coding":
            res = judge_code(judge, entry, language, code)
            if res["pct"] is None:
                raise JudgeDown(res["error"])
            first = res["results"][0] if res["results"] else {}
            summary |= {"language": language, "code": code, "tests_passed": sum(r["passed"] for r in res["results"]),
                        "tests_total": len(entry["hidden_tests"]), "timeouts": res["timeouts"],
                        "compile_error": (first.get("compile_error") or "")[:300] or None}
            pct = res["pct"]
        else:
            res = sql_judge.grade(entry, code, run_sql)
            if any(r["error"] and ("judge" in r["error"] or "unavailable" in r["error"]) for r in res["results"]):
                raise JudgeDown("the SQL judge is not reachable")
            summary |= {"code": code, "datasets_passed": sum(r["passed"] for r in res["results"]), "datasets_total": len(res["results"]),
                        "errors": sorted({r["error"] for r in res["results"] if r["error"]})[:3]}
            pct = res["pct"]
        summary["pct"] = pct
        return eng.coding_finished(pct, summary=summary) if t["kind"] == "coding" else eng.sql_finished(pct, summary=summary)

    def settle(rec: dict, eng: Engine) -> list[dict]:
        """Auto-submit the saved draft when the task timer has run out."""
        t = open_task(eng)
        if not t or eng.coding_deadline is None or clock() <= eng.coding_deadline + DEADLINE_GRACE_S:
            return []
        draft = (rec["data"].get("drafts") or {}).get(t["id"]) or {}
        try:
            return judge_and_finish(eng, t, draft.get("language", "python"), draft.get("code", ""), auto=True)
        except JudgeDown:
            return []  # the next request retries; the candidate is not penalised for a judge outage

    # ---------------- shared load / act / save
    def with_engine(token: str, action: Callable[[dict, Engine], dict], *, need_status: tuple[str, ...]) -> dict:
        rec = store.by_token(token)
        if not rec:
            raise HTTPException(404, "unknown interview link")
        with store.lock(rec["id"]):
            rec = store.by_token(token)
            if rec["status"] not in need_status:
                raise HTTPException(409, f"interview is {rec['status']}")
            eng = engine_for(rec)
            try:
                out = action(rec, eng)
            except PIIBlocked:
                raise HTTPException(500, "privacy check stopped a model call; the interviewer has been notified")
            except JudgeDown:
                rec["data"]["engine"] = eng.to_dict()
                store.save(rec)
                raise HTTPException(503, "The code judge is not reachable right now. Your code is saved; try Submit again in a moment.")
            rec["data"]["engine"] = eng.to_dict()
            just_finished = eng.finished and rec["status"] in ("created", "active")
            if rec["status"] in ("created", "active", "finished"):  # later statuses (scoring, ready, finalized) are not ours to change
                rec["status"] = "finished" if eng.finished else "active"
            store.save(rec)
            if just_finished:
                on_finished(rec["id"])
            out.setdefault("status", rec["status"])
            return out

    def view_of(rec: dict, eng: Engine) -> dict:
        return {"status": ("finished" if eng.finished else "active") if eng.started_at else "created", "state": eng.session.state.value,
                "remaining_s": max(eng.session.remaining_s(), 0),
                "messages": [{"role": t["role"], "text": t["text"]} for t in eng.transcript if t["role"] in ("interviewer", "candidate")],
                "task": task_view(rec, eng)}

    def need_task(eng: Engine) -> dict:
        t = open_task(eng)
        if not t:
            raise HTTPException(409, "there is no coding or SQL task open")
        return t

    def check_language(t: dict, body: TaskBody) -> None:
        if t["kind"] == "coding" and body.language not in LANGUAGES:
            raise HTTPException(422, f"language must be one of {', '.join(LANGUAGES)}")

    def draft_of(rec: dict, t: dict, body: TaskBody) -> None:
        rec["data"].setdefault("drafts", {})[t["id"]] = {"language": body.language, "code": body.code}

    # ---------------- admin
    @admin.get("/settings")
    def get_settings():
        return {"company_faq": store.get_setting("company_faq"), "voice": voice_settings.load(store).to_dict(), "voices": list(voice_settings.VOICES)}

    @admin.put("/settings")
    def put_settings(body: SettingsBody):
        if body.company_faq is not None:
            if find_pii(body.company_faq) or injection.detect(body.company_faq):
                raise HTTPException(422, "the FAQ must not contain emails, phone numbers, links or instruction-like text (it is sent to the AI service)")
            store.set_setting("company_faq", body.company_faq.strip())
        if body.voice is not None:
            try:
                voice_settings.save(store, body.voice)
            except voice_settings.VoiceSettingsError as exc:
                raise HTTPException(422, str(exc))
        return get_settings()

    @admin.post("/interviews")
    def create(body: NewInterview):
        try:
            Redactor(body.candidate_name, body.also_hide)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        rec = store.create({"candidate_name": body.candidate_name.strip(), "also_hide": body.also_hide, "jd_tags": body.jd_tags,
                            "enabled_sections": body.enabled_sections, "behavioural_questions": body.behavioural_questions,
                            "profile": None, "profile_warnings": [], "engine": None, "drafts": {}})
        return {"id": rec["id"], "token": rec["token"]}

    @admin.put("/interviews/{interview_id}/resume")
    async def upload_resume(interview_id: str, request: Request):
        body = await request.body()
        rec = store.get(interview_id)
        if not rec:
            raise HTTPException(404, "unknown interview")
        if rec["status"] != "created":
            raise HTTPException(409, "the interview already started")
        red = redactor_for(rec)
        try:
            prof = extract_profile(body, red, llm_factory(red), max_pages=max_pages, max_bytes=max_bytes)
        except ResumeRejected as exc:
            raise HTTPException(422, str(exc))
        except PIIBlocked:
            raise HTTPException(500, "privacy check stopped the resume from being sent")
        with store.lock(interview_id):
            rec = store.get(interview_id)
            rec["data"].update(profile=prof.data, profile_warnings=prof.warnings, resume_text=prof.redacted_text)
            store.save(rec)
        return {"profile": prof.data, "warnings": prof.warnings}

    @admin.get("/interviews")
    def list_all():
        return [{"id": r["id"], "token": r["token"], "status": r["status"], "status_label": STATUS_LABEL.get(r["status"], r["status"]), "created_at": r["created_at"], "candidate_name": r["data"]["candidate_name"]}
                for r in store.list()]

    @admin.get("/interviews/{interview_id}")
    def detail(interview_id: str):
        rec = store.get(interview_id)
        if not rec:
            raise HTTPException(404, "unknown interview")
        eng = rec["data"].get("engine") or {}
        return {"id": rec["id"], "token": rec["token"], "status": rec["status"],
                "data": {k: v for k, v in rec["data"].items() if k not in ("engine", "drafts")},
                "transcript": eng.get("transcript", []), "questions": eng.get("questions", []), "flags": eng.get("flags", []),
                "coding": eng.get("coding", []), "session": eng.get("session")}

    # ---------------- candidate session (token only)
    @session.get("/{token}")
    def view(token: str):
        rec = store.by_token(token)
        if not rec:
            raise HTTPException(404, "unknown interview link")
        if not rec["data"].get("engine"):
            return {"status": rec["status"], "state": "intro", "remaining_s": 2400, "messages": [], "task": None}
        return with_engine(token, lambda r, e: (settle(r, e), view_of(r, e))[1],
                           need_status=("active", "finished", "scoring", "ready", "finalized"))

    @session.post("/{token}/start")
    def start(token: str):
        return with_engine(token, lambda r, e: {"events": e.start()}, need_status=("created",))

    @session.post("/{token}/message")
    def message(token: str, body: Message):
        def act(r, e):
            ev = settle(r, e)
            return {"events": ev + e.candidate_message(body.text)}
        return with_engine(token, act, need_status=("active",))

    @session.post("/{token}/silence")
    def silence(token: str):
        return with_engine(token, lambda r, e: {"events": e.candidate_silence()}, need_status=("active",))

    @session.post("/{token}/task/save")
    def task_save(token: str, body: TaskBody):
        def act(r, e):
            ev = settle(r, e)
            t = open_task(e)
            if t:
                draft_of(r, t, body)
            return {"saved": bool(t), "events": ev}
        return with_engine(token, act, need_status=("active",))

    @session.post("/{token}/task/event")
    def task_event(token: str, body: TaskEvent):
        """The editor reports a paste (size only, never the text). Shown to the interviewer as an integrity flag."""
        def act(r, e):
            t = open_task(e)
            if t:
                t["pasted_chars"] = t.get("pasted_chars", 0) + body.chars
            return {"recorded": bool(t)}
        return with_engine(token, act, need_status=("active",))

    @session.post("/{token}/task/run")
    def task_run(token: str, body: TaskBody):
        def act(r, e):
            ev = settle(r, e)
            t = need_task(e)
            check_language(t, body)
            if t.get("runs", 0) >= MAX_RUNS_PER_TASK:
                raise HTTPException(429, "you have used all your test runs for this task; submit when ready")
            if clock() - t.get("last_run", 0) < MIN_RUN_GAP_S:
                raise HTTPException(429, "please wait a moment between runs")
            t["runs"], t["last_run"] = t.get("runs", 0) + 1, clock()
            draft_of(r, t, body)
            entry = by_id[t["id"]]
            try:
                if t["kind"] == "coding":
                    results = run_samples(judge, entry, body.language, body.code)
                    return {"events": ev, "results": [{**x, "error": (x["error"] or "")[:300] or None} for x in results]}
                res = run_sql({"schema": entry["schema"], "tables": entry["sample_rows"], "query": body.code})
            except CodeTooLarge as exc:
                raise HTTPException(413, str(exc))
            if not res["ok"]:
                return {"events": ev, "ok": False, "error": str(res["error"])[:300]}
            return {"events": ev, "ok": True, "columns": res["columns"], "rows": res["rows"][:MAX_ROWS_SHOWN],
                    "matches_expected": sql_judge.same_rows(res["rows"], entry["sample_expected"], entry.get("ordered", False))
                    and len(res["columns"]) == entry["n_columns"]}
        return with_engine(token, act, need_status=("active",))

    @session.post("/{token}/task/submit")
    def task_submit(token: str, body: TaskBody):
        def act(r, e):
            early = settle(r, e)
            if early or not open_task(e):  # the timer had already run out: the draft was submitted instead
                return {"events": early, "submitted": True, "auto_submitted": True}
            t = need_task(e)
            check_language(t, body)
            draft_of(r, t, body)
            try:
                return {"events": judge_and_finish(e, t, body.language, body.code), "submitted": True}
            except CodeTooLarge as exc:
                raise HTTPException(413, str(exc))
        return with_engine(token, act, need_status=("active",))

    @session.post("/{token}/task/skip")
    def task_skip(token: str):
        def act(r, e):
            early = settle(r, e)
            if early or not open_task(e):
                return {"events": early, "submitted": True, "auto_submitted": True}
            return {"events": judge_and_finish(e, need_task(e), "python", "", skipped=True), "submitted": True}
        return with_engine(token, act, need_status=("active",))

    # what the voice session may ask of an interview: exactly what the typed chat does
    ops = SimpleNamespace(view=view, start=start, message=lambda token, text: message(token, Message(text=text)), silence=silence)
    return admin, session, ops
