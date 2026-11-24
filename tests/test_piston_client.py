"""Piston client logic, tested against a fake Piston that really executes Python locally."""
import inspect
import subprocess
import sys

import pytest

from backend.content import bank
from backend.judge import sql_judge
from backend.judge.client import CodeTooLarge, PistonClient, judge_code, piston_sql_executor, run_samples

sys.path.insert(0, "backend/content/tools")
import coding_specs as cs  # noqa: E402

BANK = bank.load()
CODING = {e["book_id"]: e for e in BANK if e["type"] == "coding"}


class FakePiston:
    """Implements just enough of POST /api/v2/execute for Python submissions."""

    def __init__(self):
        self.requests = []

    def __call__(self, path, body):
        assert path == "/api/v2/execute"
        self.requests.append(body)
        if body["language"] != "python":
            return {"message": f"{body['language']} runtime is not installed"}
        try:
            p = subprocess.run([sys.executable, "-I", "-c", body["files"][0]["content"]], input=body["stdin"],
                               capture_output=True, text=True, timeout=body["run_timeout"] / 1000)
        except subprocess.TimeoutExpired:
            return {"run": {"stdout": "", "stderr": "", "code": None, "signal": "SIGKILL", "status": "TO"}}
        return {"run": {"stdout": p.stdout, "stderr": p.stderr, "code": p.returncode, "signal": None}}


def reference_solution(book_id: str) -> str:
    spec = cs.SPECS[book_id]
    ref = inspect.getsource(spec["fn"]).replace(f"def {spec['fn'].__name__}(", "def _ref(", 1)
    params = cs.PY_PARAMS[spec["inp"]]
    return "from collections import Counter\n" + ref + "\n" + cs.python_starter(spec).replace("    pass\n", f"    return _ref({params})\n", 1)


def test_request_shape_and_limits():
    fake = FakePiston()
    PistonClient("http://x", post=fake).run("python", "print(1)", "in")
    req = fake.requests[0]
    assert req["language"] == "python" and req["files"][0]["name"] == "main.py" and req["stdin"] == "in"
    assert req["run_memory_limit"] == 256 * 1024 * 1024 and req["run_timeout"] == 3000


def test_oversize_code_is_rejected_before_any_request():
    fake = FakePiston()
    with pytest.raises(CodeTooLarge):
        PistonClient("http://x", post=fake).run("python", "x = 1\n" * 20000)
    assert fake.requests == []


def test_unsupported_language_rejected():
    with pytest.raises(ValueError):
        PistonClient("http://x", post=FakePiston()).run("ruby", "puts 1")


def test_compile_error_is_reported_and_scores_zero():
    def post(path, body):
        return {"compile": {"code": 1, "stderr": "Main.java:3: error: ';' expected"}, "run": {"code": None}}

    client = PistonClient("http://x", post=post)
    assert "expected" in client.run("java", "bad").compile_error
    res = judge_code(client, CODING["DEMO-C1"], "java", "bad")
    assert res["pct"] == 0.0 and res["error"] is None and len(res["results"]) == 1  # stops after the first compile error


def test_every_test_input_fits_piston_with_a_max_size_submission():
    """Piston rejects JSON bodies over ~100 KB. A 50 KB submission plus the test input must fit."""
    from backend.judge.client import MAX_CODE_BYTES, MAX_REQUEST_BYTES

    budget = MAX_REQUEST_BYTES - MAX_CODE_BYTES - 2048  # room for JSON framing and the other fields
    for e in CODING.values():
        for t in e["sample_tests"] + e["hidden_tests"]:
            assert len(t["stdin"].encode()) <= budget, (e["book_id"], len(t["stdin"]))


def test_every_sql_request_fits_piston():
    import json as _json
    from backend.judge.client import MAX_REQUEST_BYTES

    script = len(open("backend/judge/sql_runner_script.py", "rb").read())
    for e in (x for x in BANK if x["type"] == "sql"):
        for ds in e["hidden_datasets"]:
            req = _json.dumps({"schema": e["schema"], "tables": ds["tables"], "query": "x" * 2000})
            assert len(req) + script + 2048 <= MAX_REQUEST_BYTES, (e["book_id"], ds["name"])


def test_oversized_request_gets_a_clear_message_instead_of_an_opaque_400():
    fake = FakePiston()
    r = PistonClient("http://x", post=fake).run("python", "print(1)", "9" * 120_000)
    assert "too large" in r.sandbox_error and fake.requests == []


def test_piston_rejection_reports_the_real_reason():
    def post(path, body):
        return {"stack": "PayloadTooLargeError: request entity too large\n    at readStream"}

    r = PistonClient("http://x", post=post).run("python", "print(1)")
    assert "PayloadTooLargeError" in r.sandbox_error


def test_timeout_costs_no_more_than_a_failed_test_and_is_reported_as_evidence():
    slow = cs.python_starter(cs.SPECS["DEMO-C1"]).replace("    pass\n", "    while True:\n        pass\n", 1)
    quick, heavy = CODING["DEMO-C1"]["hidden_tests"][0], CODING["DEMO-C1"]["hidden_tests"][1]
    wrong = dict(quick, expected="999\n")  # answered wrongly, finishes in time
    entry = dict(CODING["DEMO-C1"], hidden_tests=[dict(wrong, weight=1), dict(heavy, weight=1)], time_limit_ms=300)
    res = judge_code(PistonClient("http://x", post=FakePiston()), entry, "python", slow)
    assert res["pct"] == 0.0 and res["timeouts"] == 2  # both fail; no deduction below zero
    ok = judge_code(PistonClient("http://x", post=FakePiston()), dict(entry, hidden_tests=[dict(quick, weight=1)]), "python",
                    reference_solution("DEMO-C1"))
    assert ok["pct"] == 100.0 and ok["timeouts"] == 0


def test_judge_outage_is_an_error_not_a_zero_score():
    def post(path, body):
        raise ConnectionError("refused")

    res = judge_code(PistonClient("http://x", post=post), CODING["DEMO-C1"], "python", "print(1)")
    assert res["pct"] is None and "unavailable" in res["error"]


@pytest.mark.parametrize("book_id", ["DEMO-C1", "DEMO-C2"])
def test_reference_scores_100_through_the_client(book_id):
    res = judge_code(PistonClient("http://x", post=FakePiston()), CODING[book_id], "python", reference_solution(book_id))
    assert res["pct"] == 100.0, res


def test_wrong_and_unfinished_code_score_low():
    client = PistonClient("http://x", post=FakePiston())
    starter = cs.python_starter(cs.SPECS["DEMO-C1"])
    assert judge_code(client, CODING["DEMO-C1"], "python", starter)["pct"] < 100.0
    buggy = starter.replace("    pass\n", "    return sum(x for x in nums if x > 0 and x % 2 == 0)\n", 1)  # forgets that negative evens count
    res = judge_code(client, CODING["DEMO-C1"], "python", buggy)
    assert 0 < res["pct"] < 100.0  # the tests with negative numbers fail, the rest pass; edge tests weigh double


def test_timeout_is_flagged_and_not_a_pass():
    slow = cs.python_starter(cs.SPECS["DEMO-C1"]).replace("    pass\n", "    while True:\n        pass\n", 1)
    entry = dict(CODING["DEMO-C1"], hidden_tests=CODING["DEMO-C1"]["hidden_tests"][:1], time_limit_ms=300)
    res = judge_code(PistonClient("http://x", post=FakePiston()), entry, "python", slow)
    assert res["pct"] == 0.0 and res["results"][0]["timed_out"] is True


def test_run_samples_shows_actual_vs_expected_for_visible_tests_only():
    out = run_samples(PistonClient("http://x", post=FakePiston()), CODING["DEMO-C1"], "python", reference_solution("DEMO-C1"))
    assert len(out) == len(CODING["DEMO-C1"]["sample_tests"]) and all(o["passed"] for o in out)


def test_sql_runs_through_the_same_client():
    sql = {e["id"]: e for e in BANK if e["type"] == "sql"}["SQL-001"]
    execute = piston_sql_executor(PistonClient("http://x", post=FakePiston()))
    assert sql_judge.grade(sql, sql["reference_query"], execute)["pct"] == 100.0
    assert sql_judge.grade(sql, "DROP TABLE Books", execute)["pct"] == 0.0
