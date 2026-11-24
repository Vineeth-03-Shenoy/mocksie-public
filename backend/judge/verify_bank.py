"""End-to-end check of the whole judge, through the REAL Piston (build step 2).

    uv run --no-project --with httpx python backend/judge/verify_bank.py [http://localhost:2000]

For every coding problem in the bank it runs a known-good reference program in Python, Java
and C against all sample and hidden tests, and for every SQL question it runs the
reference query against every dataset. Exits non-zero if anything fails, and says exactly what.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "content" / "tools"))

import coding_specs as cs  # noqa: E402
import starters  # noqa: E402
from backend.content import bank  # noqa: E402
from backend.judge import sql_judge  # noqa: E402
from backend.judge.client import PistonClient, judge_code, piston_sql_executor, run_samples  # noqa: E402

REVERSE_LIST_PY = ("    prev = None\n    cur = head\n    while cur is not None:\n        nxt = cur.next\n"
                   "        cur.next = prev\n        prev = cur\n        cur = nxt\n    return prev\n")


def reference_source(spec: dict, language: str) -> str:
    if language == "java":
        return starters.java_starter(spec, body=starters.REF_JAVA[spec["slug"]])
    if language == "c":
        return starters.c_starter(spec, body=starters.REF_C[spec["slug"]])
    if spec["slug"] == "reverse_list":
        return cs.python_starter(spec).replace("    pass\n", REVERSE_LIST_PY, 1)
    ref = inspect.getsource(spec["fn"]).replace(f"def {spec['fn'].__name__}(", "def _ref(", 1)
    return ("from collections import Counter\n" + ref + "\n"
            + cs.python_starter(spec).replace("    pass\n", f"    return _ref({cs.PY_PARAMS[spec['inp']]})\n", 1))


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:2000"
    client = PistonClient(url)
    entries = {e["book_id"]: e for e in bank.load()}
    failures = []

    print(f"Judge: {url}\n\nCoding (reference solutions, pct of weighted hidden tests):")
    for book_id, spec in sorted(cs.SPECS.items()):
        row = []
        for lang in ("python", "java", "c"):
            src = reference_source(spec, lang)
            res = judge_code(client, entries[book_id], lang, src)
            samples_ok = all(s["passed"] for s in run_samples(client, entries[book_id], lang, src))
            ok = res["pct"] == 100.0 and samples_ok
            row.append(f"{lang}={'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append((book_id, lang, res.get("error") or res["results"]))
        print(f"  {book_id:<4} {spec['slug']:<28} " + "  ".join(row))

    print("\nSQL (reference query on every dataset, plus a destructive query that must be refused):")
    execute = piston_sql_executor(client)
    for e in sorted((e for e in entries.values() if e["type"] == "sql"), key=lambda e: e["book_id"]):
        good = sql_judge.grade(e, e["reference_query"], execute)
        bad = sql_judge.grade(e, "DROP TABLE Books", execute)
        ok = good["pct"] == 100.0 and bad["pct"] == 0.0
        print(f"  {e['book_id']:<4} {e['slug']:<30} {'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append((e["book_id"], "sql", good["results"]))

    print(f"\n{'ALL OK' if not failures else str(len(failures)) + ' FAILURE(S)'}")
    for f in failures:
        print("  ", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
