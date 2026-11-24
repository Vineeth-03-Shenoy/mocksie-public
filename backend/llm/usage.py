"""Token usage per model per day, stored in SQLite (shown in the console).

Pools follow the free daily allowance: the interviewer and fast models share the "small"
pool; the judge model has its own. Assumption: the allowance resets at 00:00 UTC. Confirm in
the OpenAI usage dashboard.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA = """CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT NOT NULL, pool TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, pool, model))"""


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class UsageTracker:
    def __init__(self, path: str, limits: dict[str, int]):
        self.path, self.limits = path, limits
        with self._db() as db:
            db.execute(SCHEMA)

    def _db(self):
        return sqlite3.connect(self.path)

    def record(self, pool: str, model: str, input_tokens: int, output_tokens: int, day: str | None = None) -> None:
        with self._db() as db:
            db.execute(
                """INSERT INTO llm_usage(day, pool, model, input_tokens, output_tokens, requests)
                   VALUES(?,?,?,?,?,1)
                   ON CONFLICT(day, pool, model) DO UPDATE SET
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens,
                     requests = requests + 1""",
                (day or today(), pool, model, int(input_tokens), int(output_tokens)),
            )

    def used(self, pool: str, day: str | None = None) -> int:
        with self._db() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_usage WHERE day=? AND pool=?",
                (day or today(), pool),
            ).fetchone()
        return int(row[0])

    def report(self, day: str | None = None) -> dict:
        """What the console shows: per model, plus each pool against its limit."""
        day = day or today()
        with self._db() as db:
            rows = db.execute(
                "SELECT pool, model, input_tokens, output_tokens, requests FROM llm_usage WHERE day=?", (day,)
            ).fetchall()
        pools = {p: {"limit": lim, "used": self.used(p, day), "remaining": max(lim - self.used(p, day), 0)}
                 for p, lim in self.limits.items()}
        return {
            "day": day,
            "models": [{"pool": p, "model": m, "input": i, "output": o, "requests": r} for p, m, i, o, r in rows],
            "pools": pools,
        }
