"""The only door to OpenAI. Everything that talks to an LLM goes through LLMClient.complete().

Guarantees, each covered by tests/test_llm_client.py:
  1. PII tripwire: any contact detail (email, phone, URL, Aadhaar, PAN, ...) in the outgoing
     payload raises PIIBlocked BEFORE the network call. OpenAI may train on this traffic.
  2. Raw-source tripwire: text registered with PIIGuard.register_raw() (the raw resume) and every
     name / "Also hide" term of the interview's Redactor (whole word, case-insensitive, each part
     of a name) must never appear in a payload. Only redacted text may.
  3. store=false is sent on every request (configurable, default false).
  4. Token usage is recorded per model per day, and the judge pool honours queue-or-pay.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend.config import Settings
from backend.guardrails.pii import Redactor, find_pii
from backend.llm.usage import UsageTracker

ROLES = {"interviewer": "small", "fast": "small", "judge": "judge"}
SHINGLE_WORDS = 12  # verbatim run of this many words from a registered raw text is a leak


class PIIBlocked(Exception):
    """Outgoing payload contained contact details or raw source text."""


class BudgetExceeded(Exception):
    """Judge pool is (nearly) used up and JUDGE_OVER_BUDGET=queue. Caller should queue the job."""


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class PIIGuard:
    def __init__(self) -> None:
        self._shingles: set[tuple[str, ...]] = set()
        self._exact: set[str] = set()
        self._redactor: Redactor | None = None

    def register_raw(self, text: str) -> None:
        """Register a raw source (resume text). No 12-word run of it may leave the process."""
        w = _words(text)
        for i in range(max(len(w) - SHINGLE_WORDS + 1, 0)):
            self._shingles.add(tuple(w[i : i + SHINGLE_WORDS]))

    def register_identity(self, *values: str) -> None:
        """Register exact strings that must never be sent (full name, email, phone)."""
        self._exact.update(v.strip().lower() for v in values if v and v.strip())

    def attach(self, redactor: Redactor) -> None:
        """Arm the tripwire with this interview's typed candidate name and 'Also hide' terms."""
        self._redactor = redactor

    def check(self, text: str, terms: bool = True) -> None:
        hits = find_pii(text)
        if hits:
            raise PIIBlocked(f"contact details in LLM payload: {sorted({k for k, _ in hits})}")
        if terms and self._redactor and self._redactor.find_terms(text):
            raise PIIBlocked("candidate name or hidden term in LLM payload")
        low = text.lower()
        for s in self._exact:
            if s in low:
                raise PIIBlocked("registered identity string in LLM payload")
        if self._shingles:
            w = _words(text)
            for i in range(max(len(w) - SHINGLE_WORDS + 1, 0)):
                if tuple(w[i : i + SHINGLE_WORDS]) in self._shingles:
                    raise PIIBlocked("raw resume text in LLM payload")


def _texts(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _texts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _texts(v)


class LLMClient:
    def __init__(self, settings: Settings, usage: UsageTracker, guard: PIIGuard | None = None,
                 transport: Callable[[dict], dict] | None = None) -> None:
        self.s, self.usage, self.guard = settings, usage, guard or PIIGuard()
        self._transport = transport or self._openai_transport
        self._models = {"interviewer": settings.interviewer_model, "fast": settings.fast_model, "judge": settings.judge_model}

    # -- budget -----------------------------------------------------------
    def judge_budget_ok(self) -> bool:
        used = self.usage.used("judge")
        return not (self.s.judge_over_budget == "queue" and used >= self.s.pool_judge * self.s.judge_queue_threshold)

    # -- main call --------------------------------------------------------
    def complete(self, role: str, messages: list[dict], *, trusted: tuple[int, ...] = (), **params: Any) -> dict:
        """Chat Completions call. Returns {"content", "tool_calls", "usage", "model"}."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        payload = {"model": self._models[role], "messages": messages, "store": self.s.store, **params}
        # The typed name/term check applies to USER-role content only (what the candidate or the resume could have written).
        # Static text we wrote (system messages, tool and response schemas) and the model's own earlier replies never carry
        # candidate data, so they skip it: a name part that is also an ordinary word ("Mark", "Grace") must not break our own
        # prompts. Pattern checks (email, phone, ...) still cover everything. Candidate data goes in non-system messages.
        for i, m in enumerate(messages):  # `trusted`: indexes of messages that are our own static text (e.g. a question card)
            for text in _texts(m):
                self.guard.check(text, terms=m.get("role") == "user" and i not in trusted)
        for k in ("tools", "response_format"):
            for text in _texts(params.get(k)):
                self.guard.check(text, terms=False)
        if role == "judge" and not self.judge_budget_ok():
            raise BudgetExceeded("judge pool nearly used for today; queue this job for tomorrow")
        resp = self._transport(payload)
        u = resp.get("usage") or {}
        self.usage.record(ROLES[role], payload["model"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        msg = resp["choices"][0]["message"]
        return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or [], "usage": u, "model": payload["model"]}

    def complete_json(self, role: str, messages: list[dict], schema: dict, name: str = "result", *,
                      trusted: tuple[int, ...] = (), **params: Any) -> Any:
        resp = self.complete(
            role, messages, trusted=trusted,
            response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            **params,
        )
        return json.loads(resp["content"]), resp

    # -- real transport ---------------------------------------------------
    def _openai_transport(self, payload: dict) -> dict:
        from openai import OpenAI  # lazy: tests never need the SDK or network

        client = OpenAI(api_key=self.s.openai_api_key)
        return client.chat.completions.create(**payload).model_dump()
