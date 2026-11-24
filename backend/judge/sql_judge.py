"""SQL grading: run the candidate's query against each hidden dataset and compare result rows.

- Order-insensitive unless the question sets ordered=True.
- Rows are compared as a multiset (duplicates count); NULL equals NULL.
- Columns are compared by position; names are ignored (aliases vary) and the column count must match.
- Numbers compare numerically (int 5 == float 5.0) after rounding to 2 decimals, so AVG(x) and ROUND(AVG(x), 2) both pass.
- Score = weighted share of datasets passed (edge-case datasets weigh double). Deterministic.

`execute` is any callable taking the runner request dict and returning the runner's result dict.
Locally that is run_local(); on the real judge it is the Piston client running the same script.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

SCRIPT = Path(__file__).with_name("sql_runner_script.py")


def run_local(request: dict, timeout: float = 15.0) -> dict:
    """Run the runner script in a fresh interpreter (tests, and the build step that computes expected rows)."""
    try:
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT)], input=json.dumps(request),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "query took too long"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "runner crashed"}


def _cell(v):
    """Numbers compare numerically (int == float, floats rounded to 2 dp); NULL (None) equals NULL."""
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return round(float(v), 2) + 0.0  # + 0.0 turns -0.0 into 0.0
    return v


def normalise(rows: list[list], ordered: bool):
    """Ordered: the row list as-is. Unordered: a multiset (Counter), so duplicate rows count."""
    out = [tuple(_cell(v) for v in r) for r in rows]
    return out if ordered else Counter(out)


def same_rows(got: list[list], expected: list[list], ordered: bool = False) -> bool:
    """Columns are compared by position (names are ignored); a different column count never matches."""
    return normalise(got, ordered) == normalise(expected, ordered)


def expected_rows(schema: list[str], tables: dict, reference_query: str,
                  execute: Callable[[dict], dict] = run_local) -> list[list]:
    res = execute({"schema": schema, "tables": tables, "query": reference_query})
    if not res["ok"]:
        raise RuntimeError(f"reference query failed: {res['error']}")
    return res["rows"]


def grade(entry: dict, query: str, execute: Callable[[dict], dict] = run_local) -> dict:
    """Run `query` on every hidden dataset of a bank entry of type 'sql'."""
    ordered = entry.get("ordered", False)
    results, earned, total = [], 0, 0
    for ds in entry["hidden_datasets"]:
        res = execute({"schema": entry["schema"], "tables": ds["tables"], "query": query})
        ok = res["ok"] and len(res["columns"]) == entry["n_columns"] and same_rows(res["rows"], ds["expected"], ordered)
        w = ds.get("weight", 1)
        total += w
        earned += w if ok else 0
        results.append({"dataset": ds["name"], "passed": bool(ok), "error": None if res["ok"] else res["error"]})
    return {"pct": round(100 * earned / total, 1) if total else 0.0, "earned": earned, "total": total, "results": results}
