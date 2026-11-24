"""A scripted stand-in for OpenAI (the LLMClient transport). Behaviour is switched with .mode and the .grades queue."""
from __future__ import annotations

import json

PROFILE = {"degree": "B.Tech", "branch": "CSE", "expected_graduation_year": 2026, "skills": ["Python", "SQL"],
           "programming_languages": ["Python"], "projects": [{"title": "Cache", "summary": "An LRU cache.", "technologies": ["Java"]}],
           "internships": [], "certifications": [], "mentions_sql": False, "topics": ["databases"]}


def reply(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def call(name, **args):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLLM:
    """mode: "complete" (call mark_answer_complete when asked to decide) | "followup" (always ask a follow-up)."""

    def __init__(self, mode: str = "complete", profile: dict | None = None) -> None:
        self.mode, self.calls, self.grades, self.profile = mode, [], [], profile or PROFILE
        self.script: list = []          # explicit responses (dicts from reply()) consumed first
        self.interviewer_calls = 0
        self.q_scores: list[float | dict] = []   # per judge RUN: a number for every card, or {card id: score}
        self.form_scores: dict[str, int] = {}     # criterion id -> score (default 7)
        self.form_quote = ""                      # a (real or invented) quote returned as evidence
        self.judge_runs = 0
        self.fail_judge_with: Exception | None = None
        self.fail_form = False
        self.comment_script: list[str] = []       # per comments call: a text used for every item (to test the wording guard)
        self.comment_calls = 0

    def __call__(self, payload: dict) -> dict:
        self.calls.append(payload)
        rf = payload.get("response_format")
        if rf:
            name = rf["json_schema"]["name"]
            if name == "quick_grade":
                return self._grade(payload)
            if name == "candidate_profile":
                return reply(json.dumps(self.profile))
            if name == "question_grades":
                return self._judge_questions(payload)
            if name == "form_scores":
                return self._judge_form(payload)
            if name == "form_comments":
                return self._judge_comments(payload)
        self.interviewer_calls += 1
        if self.script:
            return self.script.pop(0)
        last = payload["messages"][-1]
        if last["role"] == "tool":
            return reply("Okay. Here is the next thing to look at.")
        if "call mark_answer_complete" in last["content"] and self.mode == "complete":
            return reply(None, [call("mark_answer_complete")])
        if "call mark_answer_complete" in last["content"]:
            return reply("Could you say a little more about that?")
        return reply("Hello, here is a question for you.")

    def _grade(self, payload: dict) -> dict:
        n = len(json.loads(payload["messages"][1]["content"])["key_points"])
        g = self.grades.pop(0) if self.grades else "strong"
        if g is None:
            return reply(json.dumps({"no_attempt": True, "points": []}))
        pts = [{"index": i, "covered": g == "strong", "evidence": "my words" if g == "strong" else ""} for i in range(n)]
        return reply(json.dumps({"no_attempt": False, "points": pts}))

    # ---- inspection helpers
    def interviewer_payloads(self):
        return [c for c in self.calls if not c.get("response_format")]

    def all_text(self) -> str:
        return json.dumps(self.calls, ensure_ascii=False)

    # ---- judge (gpt-5.4) behaviour
    def _judge_questions(self, payload: dict) -> dict:
        if self.fail_judge_with:
            raise self.fail_judge_with
        cards = json.loads(payload["messages"][1]["content"])["cards"]
        text = payload["messages"][2]["content"]
        answers = json.loads(text[text.index("\n") + 1:text.rindex("\n")])
        run = self.judge_runs
        self.judge_runs += 1
        spec = self.q_scores[min(run, len(self.q_scores) - 1)] if self.q_scores else 7.0
        out = []
        for c in cards:
            score = spec.get(c["id"], 7.0) if isinstance(spec, dict) else spec
            ans = answers.get(c["id"], "")
            n = len(c["key_points"])
            out.append({"id": c["id"], "score": score, "points_covered": list(range(n // 2)), "points_missed": list(range(n // 2, n)),
                        "evidence": [ans[:30]] if ans else [], "confidence": "high"})
        return reply(json.dumps({"questions": out}))

    def _judge_form(self, payload: dict) -> dict:
        if self.fail_judge_with or self.fail_form:
            raise self.fail_judge_with or RuntimeError("form call failed")
        ids = payload["response_format"]["json_schema"]["schema"]["properties"]["criteria"]["items"]["properties"]["id"]["enum"]
        crits = [{"id": i, "score": self.form_scores.get(i, 7), "evidence": [self.form_quote] if self.form_quote else [], "confidence": "high"} for i in ids]
        return reply(json.dumps({"criteria": crits}))

    def _judge_comments(self, payload: dict) -> dict:
        self.comment_calls += 1
        props = payload["response_format"]["json_schema"]["schema"]["properties"]
        text = self.comment_script.pop(0) if self.comment_script else None
        out = {}
        for key, default in (("strengths", "Showed a clear grasp of this area."), ("areas", "Needs more depth on the fundamentals."),
                             ("suggestions", "Revise the core topics and practise explaining them aloud.")):
            ids = props[key]["items"]["properties"]["id"]["enum"] if props[key]["maxItems"] else []
            out[key] = [{"id": i, "text": text or default} for i in ids]
        return reply(json.dumps(out))
