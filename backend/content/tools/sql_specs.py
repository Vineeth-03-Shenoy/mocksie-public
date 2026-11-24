"""Judge-ready definition of the SAMPLE SQL question (architecture 4.2, 4.7).

The statement pins the contract down (ties, NULLs, what counts as a book). Expected rows are produced by running the
reference query through the same sandboxed runner the judge uses, never typed by hand. Hidden datasets carry the edge
cases; the sample dataset is what the candidate sees on screen. This question was written for the public sample bank;
the production bank is private.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.judge.sql_judge import expected_rows, run_local  # noqa: E402

SCHEMA = [
    "CREATE TABLE Authors (author_id INTEGER PRIMARY KEY, author_name TEXT NOT NULL)",
    "CREATE TABLE Books (book_id INTEGER PRIMARY KEY, title TEXT NOT NULL, author_id INTEGER, price INTEGER)",
]
AUTHORS = [[1, "Meera Iyer"], [2, "Tom Becker"], [3, "Lina Okafor"], [4, "Raj Patel"]]
BASE = [[1, "Tidal Maps", 1, 350], [2, "Quiet Engines", 1, 420], [3, "The Long Table", 2, 280], [4, "Paper Harbours", 3, 510],
        [5, "Glass Orchard", 3, 450], [6, "Night Ferry", 3, 390], [7, "Loose Pages", None, 150]]


def T(books, authors=None):
    return {"Authors": AUTHORS if authors is None else authors, "Books": books}


def many(n, authors=4):
    """n books spread over the authors; every seventh book has no author."""
    return [[i, f"Book {i}", 1 + (i % authors) if i % 7 else None, 100 + (i * 37) % 400] for i in range(1, n + 1)]


SPEC = dict(
    slug="prolific_authors", difficulty=2,
    statement=("List the authors who have written at least TWO books, with how many books each has. Return author_name and book_count. "
               "Count books per author (two different authors may share a name: they stay separate rows). "
               "Books with no author (author_id is NULL) belong to nobody and are never counted."),
    reference=("SELECT a.author_name, COUNT(*) AS book_count FROM Authors a JOIN Books b ON b.author_id = a.author_id "
               "GROUP BY a.author_id, a.author_name HAVING COUNT(*) >= 2;"),
    sample=T(BASE),
    hidden=[("no_books", True, T([])),
            ("nobody_qualifies", False, T([[1, "A", 1, 10], [2, "B", 2, 20], [3, "C", 3, 30]])),
            ("exactly_two", True, T([[1, "A", 1, 10], [2, "B", 1, 20]])),
            ("null_author_never_counted", True, T([[1, "A", None, 10], [2, "B", None, 20], [3, "C", None, 30]])),
            ("same_name_two_authors", True, T([[1, "A", 1, 10], [2, "B", 1, 20], [3, "C", 5, 30], [4, "D", 5, 40]],
                                             AUTHORS + [[5, "Meera Iyer"]])),
            ("author_without_books", False, T([[1, "A", 2, 10], [2, "B", 2, 20], [3, "C", 2, 30]])),
            ("larger", False, T(many(150)))],
)


def column_order_note(columns: list[str]) -> str:
    return (" Return the columns in this order: " + ", ".join(columns)
            + ". Only the order and the values are checked; the column names you choose are ignored.")


def apply(bank: list[dict]) -> None:
    """Fill the 'sql' entry of the draft bank with judge-ready fields."""
    for e in bank:
        if e["book_id"] != "DEMO-S1":
            continue
        first = run_local({"schema": SCHEMA, "tables": SPEC["sample"], "query": SPEC["reference"]})
        hidden = [{"name": name, "edge": edge, "weight": 2 if edge else 1, "tables": tables,
                   "expected": expected_rows(SCHEMA, tables, SPEC["reference"])} for name, edge, tables in SPEC["hidden"]]
        e.update({
            "difficulty": SPEC["difficulty"], "difficulty_source": "authored",
            "slug": SPEC["slug"], "statement": SPEC["statement"] + column_order_note(first["columns"]),
            "schema": SCHEMA, "sample_rows": SPEC["sample"], "sample_expected": expected_rows(SCHEMA, SPEC["sample"], SPEC["reference"]),
            "columns": first["columns"], "n_columns": len(first["columns"]),
            "reference_query": SPEC["reference"], "ordered": False,
            "hidden_datasets": hidden,
        })
