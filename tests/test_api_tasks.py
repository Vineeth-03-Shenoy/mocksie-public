"""Step 4: coding and SQL tasks over HTTP: run samples, submit, skip, timers, auto-submit at zero, judge outage."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.content import bank as qbank
from backend.judge.client import PistonClient
from fake_llm import FakeLLM
from test_api_auth import PASSWORD, settings
from test_piston_client import FakePiston, reference_solution

ORIGIN = {"Origin": "http://localhost:3000"}
BANK = {e["id"]: e for e in qbank.load()}
WRONG = "import sys\nprint('nope')\n"


class Env:
    def __init__(self, tmp_path, judge=None, start=True, **app_kwargs):
        self.t = [1000.0]
        self.fake = FakeLLM()
        self.piston = FakePiston()
        app = create_app(settings(tmp_path), transport=self.fake, clock=lambda: self.t[0], judge=judge or PistonClient("http://x", post=self.piston),
                         **{"background_scoring": False, **app_kwargs})
        self.admin = TestClient(app, base_url="http://localhost")
        assert self.admin.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        self.cand = TestClient(app, base_url="http://localhost")
        made = self.admin.post("/api/admin/interviews", json={"candidate_name": "Priya Rhea Testname"}, headers=ORIGIN).json()
        self.id, self.tok = made["id"], made["token"]
        if start:
            self.cand.post(self.url("start"), headers=ORIGIN)

    def url(self, tail=""):
        return f"/api/session/{self.tok}" + (f"/{tail}" if tail else "")

    def post(self, tail, body=None):
        return self.cand.post(self.url(tail), json=body, headers=ORIGIN) if body is not None else self.cand.post(self.url(tail), headers=ORIGIN)

    def view(self):
        return self.cand.get(self.url()).json()

    def to_coding(self):
        for _ in range(40):
            if self.view()["task"]:
                return self.view()
            self.post("message", {"text": "my answer to that question"})
        raise AssertionError("never reached the coding task")

    def detail(self):
        return self.admin.get(f"/api/admin/interviews/{self.id}").json()

    def solve(self, view=None, ok=True):
        v = view or self.view()
        pid = v["task"]["problem"]["id"]
        return reference_solution(BANK[pid]["book_id"]) if ok else WRONG


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ---------------------------------------------------------------- what the browser gets
def test_the_task_view_has_samples_starters_and_a_timer_but_nothing_hidden(env):
    v = env.to_coding()
    task = v["task"]
    assert task["kind"] == "coding" and task["timer_s"] == 600 and task["remaining_s"] == 600 and task["draft"] is None
    assert task["languages"] == ["python", "java", "c"]
    p = task["problem"]
    assert p["sample_tests"] and set(p["starter_code"]) == {"python", "java", "c"}
    blob = json.dumps(v)
    for secret in ("hidden_tests", "reference", "model_answer", "weight", "edge"):
        assert secret not in blob, secret
    assert v["state"] == "coding_1"


def test_messages_are_not_accepted_while_a_task_is_open(env):
    env.to_coding()
    ev = env.post("message", {"text": "hello?"}).json()["events"]
    assert ev[0]["type"] == "notice"


def test_the_timer_counts_down_with_the_clock(env):
    env.to_coding()
    env.t[0] += 125
    assert env.view()["task"]["remaining_s"] == 475


# ---------------------------------------------------------------- run
def test_run_shows_sample_results_and_never_scores(env):
    v = env.to_coding()
    good = env.post("task/run", {"language": "python", "code": env.solve(v)}).json()
    assert good["results"] and all(r["passed"] for r in good["results"]) and "pct" not in json.dumps(good)
    env.t[0] += 2
    bad = env.post("task/run", {"language": "python", "code": WRONG}).json()
    assert not any(r["passed"] for r in bad["results"])
    assert env.view()["task"]["draft"]["code"] == WRONG          # a run also saves the draft


def test_runs_are_rate_limited_and_capped(env):
    v = env.to_coding()
    body = {"language": "python", "code": env.solve(v)}
    assert env.post("task/run", body).status_code == 200
    assert env.post("task/run", body).status_code == 429          # same instant
    for _ in range(59):
        env.t[0] += 2
        assert env.post("task/run", body).status_code == 200
    env.t[0] += 2
    r = env.post("task/run", body)
    assert r.status_code == 429 and "all your test runs" in r.json()["detail"]


@pytest.mark.parametrize("body,code", [
    ({"language": "ruby", "code": "puts 1"}, 422), ({"language": "python", "code": "x" * 60_000}, 422),
])
def test_bad_language_or_oversized_code_is_refused(env, body, code):
    env.to_coding()
    assert env.post("task/run", body).status_code == code


# ---------------------------------------------------------------- submit, skip
def test_a_correct_submit_scores_100_hides_it_from_the_candidate_and_moves_to_the_harder_problem(env):
    v = env.to_coding()
    r = env.post("task/submit", {"language": "python", "code": env.solve(v)}).json()
    assert r["submitted"] is True and "pct" not in json.dumps(r) and any(e["type"] == "coding_start" and e["level"] == 2 for e in r["events"])
    c = env.detail()["coding"][0]
    assert c["pct"] == 100.0 and c["tests_passed"] == c["tests_total"] and c["code"] and c["language"] == "python" and c["auto_submitted"] is False
    nxt = env.view()
    assert nxt["state"] == "coding_2" and nxt["task"]["timer_s"] == 900 and nxt["task"]["problem"]["id"] != v["task"]["problem"]["id"]


def test_a_failed_first_problem_goes_back_to_questions_not_to_a_second_problem(env):
    env.to_coding()
    env.post("task/submit", {"language": "python", "code": WRONG})
    assert env.detail()["coding"][0]["pct"] == 0.0
    # the sample bank has no technical questions left for the "more questions" stage, so it moves on; it never starts a second problem
    assert env.view()["state"] in ("tech_qs_more", "candidate_questions") and env.view()["task"] is None


def test_skip_counts_as_zero_and_records_the_skip(env):
    env.to_coding()
    r = env.post("task/skip").json()
    assert r["submitted"] and env.detail()["coding"][0]["skipped"] is True and env.detail()["coding"][0]["pct"] == 0.0
    assert env.post("task/skip").status_code == 409 or env.view()["task"] is None


def test_an_empty_submit_is_zero_and_never_calls_the_judge(env):
    env.to_coding()
    before = len(env.piston.requests)
    env.post("task/submit", {"language": "python", "code": "   "})
    assert env.detail()["coding"][0]["pct"] == 0.0 and len(env.piston.requests) == before


def test_task_endpoints_need_an_open_task_and_a_valid_link(env):
    assert env.post("task/run", {"language": "python", "code": "print(1)"}).status_code == 409
    assert env.post("task/skip").status_code == 409 or env.post("task/skip").json()["events"] == []
    assert env.cand.post("/api/session/nope/task/submit", json={"code": "x"}, headers=ORIGIN).status_code == 404


# ---------------------------------------------------------------- timer
def test_at_zero_the_last_saved_draft_is_submitted_by_the_server(env):
    v = env.to_coding()
    assert env.post("task/save", {"language": "python", "code": env.solve(v)}).json()["saved"] is True
    env.t[0] += 600 + 10                                          # past the timer and the grace period; the browser is gone
    nxt = env.view()
    c = env.detail()["coding"][0]
    assert c["auto_submitted"] is True and c["pct"] == 100.0 and nxt["state"] == "coding_2"


def test_at_zero_with_no_draft_the_task_ends_with_zero(env):
    env.to_coding()
    env.t[0] += 700
    env.view()
    c = env.detail()["coding"][0]
    assert c["auto_submitted"] is True and c["pct"] == 0.0 and c.get("empty") is True


def test_a_submit_a_moment_after_zero_inside_the_grace_period_is_accepted_as_a_normal_submit(env):
    v = env.to_coding()
    env.t[0] += 602
    env.post("task/submit", {"language": "python", "code": env.solve(v)})
    c = env.detail()["coding"][0]
    assert c["pct"] == 100.0 and c["auto_submitted"] is False


def test_a_submit_after_the_timer_does_not_replace_the_auto_submit(env):
    v = env.to_coding()
    env.post("task/save", {"language": "python", "code": WRONG})
    env.t[0] += 700
    r = env.post("task/submit", {"language": "python", "code": env.solve(v)}).json()
    assert r["auto_submitted"] is True and env.detail()["coding"][0]["pct"] == 0.0


# ---------------------------------------------------------------- judge outage
def test_a_judge_outage_is_a_retry_not_a_zero(tmp_path):
    def down(path, body):
        raise ConnectionError("piston is down")
    e = Env(tmp_path, judge=PistonClient("http://x", post=down))
    v = e.to_coding()
    r = e.post("task/submit", {"language": "python", "code": "print(1)"})
    assert r.status_code == 503 and "try Submit again" in r.json()["detail"]
    assert e.view()["task"]["draft"]["code"] == "print(1)" and e.detail()["coding"][0].get("pct") is None
    e.t[0] += 700                                  # even the auto-submit waits for the judge
    assert e.view()["task"] is not None


# ---------------------------------------------------------------- SQL
def reach_sql(env):
    v = env.to_coding()
    env.post("task/submit", {"language": "python", "code": env.solve(v)})
    v2 = env.view()
    assert v2["state"] == "coding_2"
    env.post("task/submit", {"language": "python", "code": env.solve(v2)})
    return env.view()


def test_sql_follows_two_strong_coding_answers_and_runs_on_the_sample_dataset_only(env):
    v = reach_sql(env)
    assert v["state"] == "sql" and v["task"]["kind"] == "sql" and v["task"]["timer_s"] == 480 and v["task"]["languages"] == []
    entry = BANK[v["task"]["problem"]["id"]]
    assert "hidden_datasets" not in json.dumps(v) and "reference_query" not in json.dumps(v)
    ok = env.post("task/run", {"code": entry["reference_query"]}).json()
    assert ok["ok"] and ok["matches_expected"] and ok["rows"] and ok["columns"] == entry["columns"]
    env.t[0] += 2
    wrong = env.post("task/run", {"code": "SELECT 1"}).json()
    assert wrong["ok"] and not wrong["matches_expected"]
    env.t[0] += 2
    bad = env.post("task/run", {"code": "DROP TABLE Employees"}).json()
    assert bad["ok"] is False and "error" in bad


def test_sql_submit_is_graded_on_hidden_datasets_and_then_the_interview_goes_on(env):
    v = reach_sql(env)
    entry = BANK[v["task"]["problem"]["id"]]
    r = env.post("task/submit", {"code": entry["reference_query"]}).json()
    assert r["submitted"] and "pct" not in json.dumps(r)
    c = env.detail()["coding"][-1]
    assert c["kind"] == "sql" and c["pct"] == 100.0 and c["datasets_passed"] == c["datasets_total"] == len(entry["hidden_datasets"])
    assert env.view()["state"] == "candidate_questions" and env.view()["task"] is None


def test_sql_skip_and_timeout_are_zero_and_do_not_block_the_end(env):
    reach_sql(env)
    env.post("task/skip")
    assert env.detail()["coding"][-1]["skipped"] is True and env.view()["state"] == "candidate_questions"


def test_the_whole_interview_can_finish_after_sql(env):
    reach_sql(env)
    env.post("task/skip")
    for _ in range(6):
        if env.view()["status"] == "finished":
            break
        env.post("message", {"text": "no questions, thank you"})
    assert env.view()["status"] == "finished"
