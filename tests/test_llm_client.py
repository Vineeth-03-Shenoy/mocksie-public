"""The LLM client is the privacy boundary: OpenAI may train on this traffic (free daily allowance)."""
import json

import pytest

from backend.config import Settings
from backend.guardrails.pii import find_pii, scrub
from backend.llm.client import BudgetExceeded, LLMClient, PIIBlocked, PIIGuard
from backend.llm.usage import UsageTracker

RAW_RESUME = """Asha Verma  asha.verma@example.com  +91 98765 43210
linkedin.com/in/asha-verma  github.com/ashav  Roll No: 1AB21CS045
Projects: Built an inventory management system using Flask and PostgreSQL where I personally wrote the billing module,
added input validation for every form, and wrote unit tests that caught a rounding bug before the demo.
Skills: Python, SQL, Git, REST APIs"""


def make(tmp_path, **over):
    s = Settings(openai_api_key="x", interviewer_model="m-int", fast_model="m-fast", judge_model="m-judge",
                 store=False, pool_small=1000, pool_judge=1000, judge_over_budget="queue",
                 judge_queue_threshold=0.9, database_path=str(tmp_path / "u.db"))
    s = Settings(**{**s.__dict__, **over})
    sent = []

    def transport(payload):
        sent.append(payload)
        return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}

    usage = UsageTracker(s.database_path, {"small": s.pool_small, "judge": s.pool_judge})
    return LLMClient(s, usage, PIIGuard(), transport), sent, usage


def msg(text):
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize("text", [
    "email me at asha.verma@example.com",
    "call +91 98765 43210 please",
    "my profile is linkedin.com/in/asha-verma",
    "see https://github.com/ashav/inventory",
    "aadhaar 1234 5678 9012",
    "PAN ABCDE1234F",
    "born 12/03/2004",
    "Roll No: 1AB21CS045",
])
def test_contact_details_never_reach_the_client(tmp_path, text):
    client, sent, _ = make(tmp_path)
    with pytest.raises(PIIBlocked):
        client.complete("interviewer", msg(text))
    assert sent == []  # blocked before any network call


@pytest.mark.parametrize("text", [
    "Use @Override in Java.", "A roll number suggests a hash map.", "Rollback undoes a transaction.",
    "Complexity is O(n log n) for 1,000,000 items.", "fib(90) is 2880067194370816120.",
])
def test_ordinary_interview_content_is_not_flagged(tmp_path, text):
    client, sent, _ = make(tmp_path)
    client.complete("interviewer", msg(text))
    assert len(sent) == 1


def test_raw_resume_text_is_blocked_even_without_contact_details(tmp_path):
    client, sent, _ = make(tmp_path)
    client.guard.register_raw(RAW_RESUME)
    leaked = "Candidate wrote: Built an inventory management system using Flask and PostgreSQL where I personally wrote the billing module"
    with pytest.raises(PIIBlocked):
        client.complete("fast", msg(leaked))
    assert sent == []


def test_full_name_and_email_registered_as_identity_are_blocked(tmp_path):
    client, sent, _ = make(tmp_path)
    client.guard.register_identity("Asha Verma")
    with pytest.raises(PIIBlocked):
        client.complete("interviewer", msg("Hello Asha Verma, welcome"))
    client.complete("interviewer", msg("Hello Asha, welcome"))  # first name only is allowed
    assert len(sent) == 1


def test_contact_details_hidden_in_tool_or_nested_params_are_caught(tmp_path):
    client, _, _ = make(tmp_path)
    with pytest.raises(PIIBlocked):
        client.complete("interviewer", msg("hi"), tools=[{"type": "function", "function": {"description": "mail a@b.co"}}])


def test_scrubbed_resume_passes_and_contains_no_pii(tmp_path):
    client, sent, _ = make(tmp_path)
    cleaned = scrub(RAW_RESUME)
    assert find_pii(cleaned) == []
    for token in ("asha.verma@example.com", "98765", "linkedin.com", "github.com", "1AB21CS045"):
        assert token not in cleaned
    client.complete("fast", msg(cleaned.replace("Asha Verma", "")))
    assert len(sent) == 1


def test_store_false_is_sent_on_every_request(tmp_path):
    client, sent, _ = make(tmp_path)
    for role in ("interviewer", "fast", "judge"):
        client.complete(role, msg("What is a stack?"))
    assert [p["store"] for p in sent] == [False, False, False]
    assert [p["model"] for p in sent] == ["m-int", "m-fast", "m-judge"]


def test_usage_is_tracked_per_model_and_pool(tmp_path):
    client, _, usage = make(tmp_path)
    client.complete("interviewer", msg("a"))
    client.complete("fast", msg("b"))
    client.complete("judge", msg("c"))
    rep = usage.report()
    assert rep["pools"]["small"]["used"] == 300 and rep["pools"]["judge"]["used"] == 150  # 150 tokens per call
    assert {m["model"] for m in rep["models"]} == {"m-int", "m-fast", "m-judge"}
    assert rep["pools"]["small"]["remaining"] == 700


def test_judge_queues_when_pool_nearly_used(tmp_path):
    client, sent, usage = make(tmp_path)
    usage.record("judge", "m-judge", 900, 0)  # 900/1000 = threshold
    with pytest.raises(BudgetExceeded):
        client.complete("judge", msg("score this"))
    assert sent == []
    client.complete("interviewer", msg("still fine"))  # live interview is never blocked by the judge pool


def test_pay_mode_keeps_going_past_the_threshold(tmp_path):
    client, sent, usage = make(tmp_path, judge_over_budget="pay")
    usage.record("judge", "m-judge", 5000, 0)
    client.complete("judge", msg("score this"))
    assert len(sent) == 1


def test_json_helper_sends_strict_schema(tmp_path):
    client, sent, _ = make(tmp_path)
    data, _ = client.complete_json("judge", msg("x"), {"type": "object"})
    assert data == {"ok": True}
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    json.dumps(sent[0])  # payload is serialisable
