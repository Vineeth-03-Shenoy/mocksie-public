"""Runs scoring jobs: in the background after an interview finishes, or on demand from the console.

Statuses of an interview record: created, active, finished (talking done) -> scoring -> ready -> finalized.
Inside data["scoring"]: state is "queued" (judge pool nearly used, JUDGE_OVER_BUDGET=queue: retried later, nothing lost),
"failed" (message shown to the interviewer, Retry button) or "ready". Partial judge output is kept between attempts.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from backend.guardrails.pii import Redactor
from backend.llm.client import BudgetExceeded, LLMClient
from backend.scoring import form as formmod
from backend.scoring.engine import score_interview
from backend.store import Store

log = logging.getLogger("mocksie.scoring")


class ScoringService:
    def __init__(self, store: Store, llm_factory: Callable[[Redactor], LLMClient], bank: list[dict],
                 clock: Callable[[], float] = time.time, *, background: bool = True) -> None:
        self.store, self.llm_factory, self.bank, self.clock, self.background = store, llm_factory, bank, clock, background
        self.by_id = {e["id"]: e for e in bank}
        self._running: set[str] = set()
        self._guard = threading.Lock()

    def kick(self, interview_id: str) -> None:
        """Start scoring without blocking the request that finished the interview."""
        if not self.background:
            self.score(interview_id)
        else:
            threading.Thread(target=self.score, args=(interview_id,), daemon=True, name=f"score-{interview_id}").start()

    def score(self, interview_id: str) -> str:
        with self._guard:
            if interview_id in self._running:
                return "running"
            self._running.add(interview_id)
        try:
            return self._score(interview_id)
        finally:
            with self._guard:
                self._running.discard(interview_id)

    def _update(self, interview_id: str, mutate: Callable[[dict], None]) -> dict:
        with self.store.lock(interview_id):
            rec = self.store.get(interview_id)
            mutate(rec)
            self.store.save(rec)
            return rec

    def _score(self, interview_id: str) -> str:
        rec = self.store.get(interview_id)
        if not rec or not (rec["data"].get("engine") or {}).get("finished") or rec["status"] in ("finalized", "ready"):
            return rec["status"] if rec else "missing"
        d = rec["data"]
        form = d.get("scoring", {}).get("form") or formmod.load_default()
        self._update(interview_id, lambda r: (r.update(status="scoring"), r["data"].setdefault("scoring", {}).update(state="running", form=form)))
        redactor = Redactor(d["candidate_name"], d.get("also_hide", ""))
        llm = self.llm_factory(redactor)

        def save_partial(p: dict) -> None:
            self._update(interview_id, lambda r: r["data"].__setitem__("scoring_partial", p))

        try:
            result = score_interview(self.store.get(interview_id), llm=llm, bank_by_id=self.by_id, form=form, redactor=redactor,
                                     partial=d.get("scoring_partial") or {}, save_partial=save_partial)
        except BudgetExceeded as exc:
            self._update(interview_id, lambda r: r["data"]["scoring"].update(state="queued", message=str(exc)))
            return "queued"
        except Exception as exc:  # noqa: BLE001 - the interviewer sees a plain message and a Retry button
            log.exception("scoring failed for %s", interview_id)
            msg = "scoring could not finish (" + type(exc).__name__ + "); press Retry. Nothing was lost."
            self._update(interview_id, lambda r: r["data"]["scoring"].update(state="failed", message=msg))
            return "failed"

        def done(r: dict) -> None:
            r["status"] = "ready"
            r["data"]["scoring"].update(state="ready", result=result, scored_at=self.clock(), message="")
            r["data"].pop("scoring_partial", None)
        self._update(interview_id, done)
        return "ready"
