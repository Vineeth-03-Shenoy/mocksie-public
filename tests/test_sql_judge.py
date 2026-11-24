"""The SQL judge: sample question, comparator rules, and the read-only sandbox."""
import json

import pytest

from backend.content import bank
from backend.judge import sql_judge

BANK = bank.load()
SQL = {e["id"]: e for e in BANK if e["type"] == "sql"}
E = SQL["SQL-001"]          # authors with at least two books

# (query, should_pass): the reference answer, valid alternatives, and plausible mistakes
CASES = [
    ("SELECT a.author_name, COUNT(*) FROM Authors a JOIN Books b ON a.author_id = b.author_id GROUP BY a.author_id HAVING COUNT(*) >= 2", True),
    ("SELECT author_name, (SELECT COUNT(*) FROM Books b WHERE b.author_id = a.author_id) AS n FROM Authors a "
     "WHERE (SELECT COUNT(*) FROM Books b WHERE b.author_id = a.author_id) > 1", True),
    ("SELECT a.author_name, COUNT(*) FROM Authors a JOIN Books b ON a.author_id = b.author_id GROUP BY a.author_name HAVING COUNT(*) >= 2", False),  # same name merges two authors
    ("SELECT a.author_name, COUNT(*) FROM Authors a JOIN Books b ON a.author_id = b.author_id GROUP BY a.author_id HAVING COUNT(*) > 2", False),   # off by one
    ("SELECT a.author_name, COUNT(*) FROM Authors a JOIN Books b ON a.author_id = b.author_id WHERE COUNT(*) >= 2 GROUP BY a.author_id", False),   # WHERE vs HAVING
    ("SELECT a.author_name, COUNT(*) FROM Authors a LEFT JOIN Books b ON a.author_id = b.author_id GROUP BY a.author_id HAVING COUNT(b.book_id) >= 2", True),
    ("SELECT author_name, COUNT(*) FROM Authors GROUP BY author_name HAVING COUNT(*) >= 2", False),                                                  # counts authors, not books
    ("SELECT a.author_name, COUNT(*) FROM Authors a, Books b GROUP BY a.author_id HAVING COUNT(*) >= 2", False),                                    # cross join
]


def test_the_sample_question_is_judge_ready():
    assert E["hidden_datasets"] and E["sample_expected"] == [["Meera Iyer", 2], ["Lina Okafor", 3]] and E["columns"] == ["author_name", "book_count"]
    assert E["n_columns"] == 2 and any(d["edge"] for d in E["hidden_datasets"])
    assert "in this order: author_name, book_count" in E["statement"] and "NULL" in E["statement"]


def test_reference_query_scores_100_and_trivial_answers_score_less():
    assert sql_judge.grade(E, E["reference_query"])["pct"] == 100.0
    for wrong in ("SELECT 1", "SELECT * FROM Authors WHERE 1 = 0", "SELECT author_name FROM Authors"):
        assert sql_judge.grade(E, wrong)["pct"] < 100.0, wrong


@pytest.mark.parametrize("query,should_pass", CASES)
def test_alternatives_pass_and_plausible_mistakes_fail(query, should_pass):
    res = sql_judge.grade(E, query)
    assert (res["pct"] == 100.0) is should_pass, res


def test_sandbox_allows_only_a_single_read_only_query():
    ds = E["hidden_datasets"][0]
    for bad in ("DROP TABLE Books", "INSERT INTO Books VALUES (99,'x',1,1)", "DELETE FROM Books", "PRAGMA table_info(Books)",
                "ATTACH DATABASE ':memory:' AS x", "SELECT 1; DROP TABLE Books", "CREATE TABLE t(a)", "UPDATE Books SET price = 0"):
        res = sql_judge.run_local({"schema": E["schema"], "tables": ds["tables"], "query": bad})
        assert res["ok"] is False, bad


def test_runaway_and_huge_queries_are_stopped():
    t = E["hidden_datasets"][0]["tables"]
    loop = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    res = sql_judge.run_local({"schema": E["schema"], "tables": t, "query": loop})
    assert res["ok"] is False and "too long" in res["error"]
    big = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 5000) SELECT x FROM c"
    res = sql_judge.run_local({"schema": E["schema"], "tables": t, "query": big})
    assert res["ok"] is False and "rows" in res["error"]


def test_sql_errors_are_reported_not_raised():
    res = sql_judge.grade(E, "SELEC nonsense")
    assert res["pct"] == 0.0 and all(r["error"] for r in res["results"])


def test_comparator_is_a_multiset_numeric_null_safe_and_positional():
    same = sql_judge.same_rows
    assert not same([[1], [1]], [[1]])                       # duplicates count
    assert same([[1], [1], [2]], [[2], [1], [1]])            # order does not
    assert same([[5]], [[5.0]]) and same([[0.0]], [[-0.0]])  # int vs float equal
    assert same([[62666.666666]], [[62666.67]])              # rounded to 2 dp
    assert same([[None, 1]], [[None, 1]]) and not same([[None]], [[0]]) and not same([[None]], [[""]])
    assert not same([["a", 1]], [[1, "a"]])                  # columns by position
    assert not same([[1, 2]], [[1]])                         # column count
    assert same([[1, "x"]], [[1.0, "x"]])


def test_order_insensitive_unless_required():
    a, b = [[1, "x"], [2, "y"]], [[2, "y"], [1, "x"]]
    assert sql_judge.same_rows(a, b) and not sql_judge.same_rows(a, b, ordered=True)
    assert not sql_judge.same_rows([[1, 2]], [[1]])


def test_column_names_are_ignored_but_order_is_not():
    assert sql_judge.grade(E, "SELECT a.author_name AS x, COUNT(*) AS y FROM Authors a JOIN Books b ON a.author_id = b.author_id "
                              "GROUP BY a.author_id HAVING COUNT(*) >= 2")["pct"] == 100.0
    assert sql_judge.grade(E, "SELECT COUNT(*) AS n, a.author_name FROM Authors a JOIN Books b ON a.author_id = b.author_id "
                              "GROUP BY a.author_id HAVING COUNT(*) >= 2")["pct"] < 100.0


def test_browser_payload_has_sample_only_and_no_answers():
    text = json.dumps(bank.browser_problem(E))
    assert "hidden_datasets" not in text and "reference_query" not in text and "model_answer" not in text
    card = json.dumps(bank.llm_card(E))
    assert E["reference_query"] not in card and "key_points" not in card


def test_sql_stage_pool_works_now_that_datasets_exist():
    assert [e["id"] for e in bank.pool(BANK, stage="sql", max_level=3, voice_only=False)] == ["SQL-001"]
