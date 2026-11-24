"""Piston client (architecture 4.7): runs candidate code and SQL in a sandbox. Python, Java, C.

Piston runs each submission in an isolated container process with networking off, a time limit and
a memory limit; this client adds the size caps and turns the response into a plain result.
The transport is injectable so the logic is tested without Docker; the default uses httpx.

Piston API v2 (https://github.com/engineer-man/piston): POST /api/v2/execute, GET/POST /api/v2/packages.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

LANGUAGES = {"python": "main.py", "java": "Main.java", "c": "main.c"}
MAX_CODE_BYTES = 50 * 1024
MAX_STDOUT_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 98 * 1024  # Piston's JSON body limit is 100 KB (found when a 644 KB test failed)
RUN_TIMEOUT_MS = 3000
RUN_MEMORY_BYTES = 256 * 1024 * 1024
SQL_SCRIPT = Path(__file__).with_name("sql_runner_script.py")


class CodeTooLarge(ValueError):
    pass


@dataclass
class RunResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    compile_error: str | None = None
    sandbox_error: str | None = None  # Piston itself failed (not the candidate's fault)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.compile_error and not self.sandbox_error


def normalise(text: str) -> str:
    """Judge comparison rule: strip trailing whitespace per line and trailing blank lines."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


class PistonClient:
    def __init__(self, base_url: str, post: Callable[[str, dict], dict] | None = None, timeout_s: float = 30.0):
        self.base = base_url.rstrip("/")
        self._post = post or (lambda path, body: httpx.post(self.base + path, json=body, timeout=timeout_s).json())

    def run(self, language: str, source: str, stdin: str = "", *, run_timeout_ms: int = RUN_TIMEOUT_MS,
            memory_bytes: int = RUN_MEMORY_BYTES) -> RunResult:
        if language not in LANGUAGES:
            raise ValueError(f"unsupported language {language!r}")
        if len(source.encode("utf-8")) > MAX_CODE_BYTES:
            raise CodeTooLarge(f"code is larger than {MAX_CODE_BYTES // 1024} KB")
        body = {
            "language": language, "version": "*",
            "files": [{"name": LANGUAGES[language], "content": source}],
            "stdin": stdin, "run_timeout": run_timeout_ms, "compile_timeout": 10000,
            "run_memory_limit": memory_bytes, "compile_memory_limit": memory_bytes,
        }
        size = len(json.dumps(body).encode("utf-8"))
        if size > MAX_REQUEST_BYTES:  # Piston's API rejects bodies over ~100 KB with an opaque 400
            return RunResult(sandbox_error=f"request too large for the judge ({size // 1024} KB, limit {MAX_REQUEST_BYTES // 1024} KB)")
        try:
            resp = self._post("/api/v2/execute", body)
        except Exception as exc:  # noqa: BLE001 - network/Piston down
            return RunResult(sandbox_error=f"judge unavailable: {exc}")
        if "run" not in resp:
            detail = resp.get("message") or str(resp.get("stack", "")).split("\n")[0] or json.dumps(resp)
            return RunResult(sandbox_error=f"judge rejected the request: {detail}"[:200])
        comp, run = resp.get("compile") or {}, resp["run"]
        if comp and comp.get("code") not in (0, None):
            return RunResult(compile_error=(comp.get("stderr") or comp.get("output") or "compile failed")[:1000], exit_code=comp.get("code"))
        status = run.get("status")  # "TO" = timeout (newer Piston); older versions only set signal
        timed_out = status == "TO" or (status is None and run.get("signal") == "SIGKILL")
        return RunResult(stdout=(run.get("stdout") or "")[:MAX_STDOUT_BYTES], stderr=(run.get("stderr") or "")[:2000],
                         exit_code=run.get("code"), timed_out=timed_out)


def judge_code(client: PistonClient, entry: dict, language: str, source: str) -> dict:
    """Run a submission against the hidden tests. Deterministic: pass/fail per test, weighted
    (edge cases count double). `source` is NEVER sent to an LLM by this function.

    A timed-out test is simply a failed test: no extra deduction. `timeouts` is evidence only; the
    scoring judge may mention it under criterion 6 ("correct but too slow on large inputs")."""
    results, earned, total = [], 0, 0
    for t in entry["hidden_tests"]:
        r = client.run(language, source, t["stdin"], run_timeout_ms=entry.get("time_limit_ms", RUN_TIMEOUT_MS))
        if r.sandbox_error:
            return {"error": r.sandbox_error, "pct": None, "results": results}  # do not score a judge outage
        passed = r.ok and normalise(r.stdout) == normalise(t["expected"])
        total += t["weight"]
        earned += t["weight"] if passed else 0
        results.append({"passed": passed, "edge": t["edge"], "timed_out": r.timed_out, "compile_error": r.compile_error})
        if r.compile_error:  # no point running the rest
            break
    pct = round(100 * earned / total, 1) if total else 0.0
    if results and results[0].get("compile_error"):
        pct = 0.0
    return {"pct": pct, "earned": earned, "total": total, "results": results, "error": None,
            "timeouts": sum(1 for r in results if r["timed_out"])}


def run_samples(client: PistonClient, entry: dict, language: str, source: str) -> list[dict]:
    """The candidate's 'Run sample tests' button: visible tests only, shows actual vs expected."""
    out = []
    for t in entry["sample_tests"]:
        r = client.run(language, source, t["stdin"], run_timeout_ms=entry.get("time_limit_ms", RUN_TIMEOUT_MS))
        out.append({"stdin": t["stdin"], "expected": t["expected"], "actual": r.stdout, "passed": r.ok and normalise(r.stdout) == normalise(t["expected"]),
                    "error": r.compile_error or r.sandbox_error or (r.stderr if r.exit_code not in (0, None) else None)})
    return out


def piston_sql_executor(client: PistonClient) -> Callable[[dict], dict]:
    """Executor for sql_judge.grade(): runs the same sql_runner_script.py inside Piston (as Python)."""
    script = SQL_SCRIPT.read_text(encoding="utf-8")

    def execute(request: dict) -> dict:
        r = client.run("python", script, json.dumps(request), run_timeout_ms=5000)
        if r.sandbox_error:
            return {"ok": False, "error": r.sandbox_error}
        if r.timed_out:
            return {"ok": False, "error": "query took too long"}
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "runner crashed"}

    return execute
