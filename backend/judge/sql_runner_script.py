"""Sandboxed SQL execution. One self-contained script, so the SAME file runs locally in tests and
inside the Piston container (as a Python program). It never runs anything but a single read-only query.

stdin  : {"schema": [DDL...], "tables": {"Employees": [[...], ...], ...}, "query": "SELECT ..."}
stdout : {"ok": true, "columns": [...], "rows": [[...], ...]}  or  {"ok": false, "error": "..."}

Safety: an authorizer allows only SELECT / READ / FUNCTION / RECURSIVE actions (so no INSERT, DROP,
ATTACH, PRAGMA, ...), sqlite refuses multiple statements, a progress handler aborts runaway queries,
and the row count is capped. The dataset is loaded BEFORE the authorizer is installed.
"""
import json
import re
import sqlite3
import sys

MAX_ROWS = 1000
MAX_STEPS = 2000  # progress handler fires every 1000 VM instructions -> ~2M instructions
SELECT, READ, FUNCTION, RECURSIVE = 21, 20, 31, 33
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main() -> None:
    req = json.load(sys.stdin)
    con = sqlite3.connect(":memory:")
    for ddl in req["schema"]:
        con.execute(ddl)
    for table, rows in req["tables"].items():
        if not IDENT.match(table):
            raise ValueError("bad table name")
        if rows:
            con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
    con.commit()

    allowed = {SELECT, READ, FUNCTION, RECURSIVE}
    con.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    steps = {"n": 0}

    def progress():
        steps["n"] += 1
        return 1 if steps["n"] > MAX_STEPS else 0

    con.set_progress_handler(progress, 1000)

    query = req["query"].strip().rstrip(";").strip()
    if not query:
        print(json.dumps({"ok": False, "error": "empty query"}))
        return
    try:
        cur = con.execute(query)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except Exception as exc:  # noqa: BLE001 - report any SQL error to the judge, never crash
        msg = "query took too long" if "interrupted" in str(exc) else str(exc)
        print(json.dumps({"ok": False, "error": msg[:200]}))
        return
    if len(rows) > MAX_ROWS:
        print(json.dumps({"ok": False, "error": f"more than {MAX_ROWS} rows"}))
        return
    clean = [[v.hex() if isinstance(v, bytes) else v for v in r] for r in rows]
    print(json.dumps({"ok": True, "columns": cols, "rows": clean}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"runner error: {exc}"[:200]}))
