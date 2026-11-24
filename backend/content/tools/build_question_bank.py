"""Build the SAMPLE question bank: 6 concept questions, 2 coding problems and 1 SQL question, all written for this
public repository. The production bank (hundreds of questions with hidden tests) is private and is not part of this repo.

    python backend/content/tools/build_question_bank.py

Everything is deterministic: the coding tests come from the reference functions in coding_specs.py and the SQL
expected rows from running the reference query (sql_specs.py). Nothing here is written by an LLM at run time.
The output, backend/content/question_bank.json, is committed so the app runs without this step.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coding_specs  # noqa: E402
import sql_specs  # noqa: E402

OUT = HERE.parent / "question_bank.json"


def concept(id_, book_id, topic, stage, difficulty, question, answer, points, follow_ups, red_flags, jd_tags, deeper=""):
    return {
        "id": id_, "book_id": book_id, "type": "concept", "topic": topic, "stage": stage, "difficulty": difficulty,
        "difficulty_source": "authored", "question": question, "model_answer": answer, "go_deeper": deeper,
        "key_points": [{"point": p, "weight": w} for p, w in points], "key_points_source": "authored",
        "follow_ups": follow_ups, "red_flags": red_flags, "jd_tags": jd_tags, "voice_suitable": True,
        "snippet": None, "optional": False, "excluded": False, "review": [],
    }


CONCEPTS = [
    concept(
        "OPEN-001", "DEMO-01", "Opening", "intro", 1,
        "To start, please introduce yourself: what you study, one project you are proud of, and the kind of work you hope to do.",
        "A short, structured introduction: current course and year, one concrete project with the candidate's own contribution, "
        "and a clear and honest direction such as the type of role or technology they want to grow in.",
        [("Names the course, year or background briefly", 2), ("Describes one concrete project and what they themselves did", 4),
         ("States a clear, realistic direction or goal", 3), ("Stays organised and to the point", 2)],
        ["Which part of that project was hardest for you?", "What would you like to learn in your first year at work?"],
        ["Only recites the resume with no detail", "Cannot name a goal"], ["hr"]),
    concept(
        "WARM-001", "DEMO-02", "Projects", "warmup", 1,
        "Pick one project from your resume and walk me through the part you built yourself.",
        "The candidate picks one project, explains the problem it solved, the pieces they personally built, one decision they made and why, "
        "and what they would change now.",
        [("Explains the problem the project solved", 3), ("Separates their own work from the team's", 4),
         ("Explains one design decision and the reason for it", 4), ("Reflects on what they would improve", 2)],
        ["What went wrong while you built it, and how did you find the cause?", "How did you test that it worked?"],
        ["Cannot explain their own code", "Claims all of a team project"], ["project"]),
    concept(
        "BEH-001", "DEMO-03", "Behavioural", "behavioural", 1,
        "Tell me about a time when a plan of yours went wrong. What did you change afterwards?",
        "A real, specific example: what the plan was, what went wrong, how the candidate noticed, what they changed, and what they do differently now.",
        [("Gives one specific, real example", 4), ("Owns their part of what went wrong", 4),
         ("Describes a concrete change they made", 4), ("Shows what they learned for next time", 3)],
        ["How soon did you realise it was going wrong?", "Who did you ask for help?"],
        ["Blames others entirely", "Says nothing has ever gone wrong"], ["behavioural"]),
    concept(
        "CS-001", "DEMO-04", "Data structures", "tech", 1,
        "What is a hash map, and why is looking up a key usually fast?",
        "A hash map stores key-value pairs. A hash function turns the key into a position in an array, so a lookup jumps straight there instead of "
        "scanning. It is fast on average, constant time, because keys spread out. Two keys can land in the same place (a collision), which is handled by "
        "keeping a small list per slot or probing for another slot. With many collisions the lookup slows down.",
        [("A hash function turns the key into an array position", 5), ("Lookup jumps to the position, so it is constant time on average", 4),
         ("Collisions happen when two keys map to the same slot", 3), ("Collisions are handled by chaining or probing", 3),
         ("Many collisions make lookups slower", 2)],
        ["What happens if two keys give the same hash?", "Why must the hash of a key not change while it is in the map?"],
        ["Thinks a hash map keeps keys sorted"], ["dsa", "fundamentals"],
        deeper="Listen for average versus worst case, and for the idea that a good hash function spreads keys evenly."),
    concept(
        "CS-002", "DEMO-05", "Web and systems", "tech", 2,
        "A web page has become slow to load. In what order would you look for the cause, and what does each check tell you?",
        "A sensible order: first check whether it is slow for everyone or one user, then the browser network panel to see which request is slow, "
        "then the server side for slow queries or heavy work, then the database (missing indexes, large result sets), then caching, then recent changes. "
        "Measure before changing anything and change one thing at a time.",
        [("Starts by narrowing down where the time goes, not by guessing", 5), ("Uses the browser network panel or similar to find the slow request", 4),
         ("Looks at server work and database queries (for example a missing index)", 4), ("Mentions caching or recent changes as candidates", 2),
         ("Measures first and changes one thing at a time", 3)],
        ["How would you tell a slow query from a slow network?", "What would you try first if the page is only slow at peak hours?"],
        ["Rewrites code before measuring anything"], ["web", "fundamentals"]),
    concept(
        "OPEN-010", "DEMO-06", "Closing", "candidate_questions", 1,
        "Before we wrap up, what would you like to ask me about the role or the hiring process?",
        "One or two thoughtful questions about the work, the team, learning and growth, or how the process continues, rather than questions already "
        "answered in the job description.",
        [("Asks at least one question about the role, team or learning", 5), ("The question shows preparation or curiosity", 3),
         ("Avoids opening with pay or leave", 2)],
        [], ["Has no question at all and no interest"], ["hr"]),
]

CODING = [
    {"id": "CODE-001", "book_id": "DEMO-C1", "type": "coding", "topic": "Coding practice", "stage": "coding", "difficulty": 1,
     "question": "Coding A — Sum of evens",
     "model_answer": "Walk through the list once, add a number to a running total only when it is divisible by two (remember zero and negative numbers), "
                     "and return the total. One pass, constant extra memory.",
     "key_points": [{"point": "A single pass over the list", "weight": 3}, {"point": "Tests evenness with the remainder after dividing by two", "weight": 4},
                    {"point": "Handles negatives, zero and an empty list", "weight": 3}],
     "follow_ups": ["What is the time and space cost?", "How does your code treat a negative odd number?"],
     "red_flags": ["Cannot explain why the empty list returns 0"]},
    {"id": "CODE-002", "book_id": "DEMO-C2", "type": "coding", "topic": "Coding practice", "stage": "coding", "difficulty": 2,
     "question": "Coding B — Longest run",
     "model_answer": "Walk the list once while keeping the length of the current run: if this value equals the previous one the run grows, otherwise it restarts at 1. "
                     "Keep the largest run seen. Return 0 for an empty list. One pass, constant extra memory.",
     "key_points": [{"point": "Compares each value with the one before it", "weight": 4}, {"point": "Resets the run length to 1 when the value changes", "weight": 4},
                    {"point": "Tracks the best run seen so far", "weight": 3}, {"point": "Handles the empty list", "weight": 2}],
     "follow_ups": ["What is the time and space cost?", "How would you also return where the longest run starts?"],
     "red_flags": ["Counts equal values anywhere in the list instead of next to each other"]},
]

SQL = [
    {"id": "SQL-001", "book_id": "DEMO-S1", "type": "sql", "topic": "SQL", "stage": "sql", "difficulty": 2,
     "question": "SQL 1 — Authors with at least two books",
     "model_answer": "Join Authors to Books on author_id, group by the author (by id, so authors with the same name stay apart), count the books, "
                     "and keep groups with a count of at least two using HAVING.",
     "key_points": [{"point": "Joins the two tables on author_id", "weight": 3}, {"point": "Groups by the author id", "weight": 4},
                    {"point": "Filters the groups with HAVING, not WHERE", "weight": 4}],
     "follow_ups": ["What changes if authors with no books must also be listed?"],
     "red_flags": ["Uses WHERE COUNT(*) >= 2"]},
]


def with_defaults(entry: dict) -> dict:
    return {"difficulty_source": "authored", "key_points_source": "authored", "go_deeper": "", "jd_tags": [], "voice_suitable": False,
            "snippet": None, "optional": False, "excluded": False, "review": [], **entry}


def build() -> list[dict]:
    bank = CONCEPTS + [with_defaults(e) for e in CODING + SQL]
    coding_specs.apply(bank)
    sql_specs.apply(bank)
    return bank


def main() -> None:
    bank = build()
    OUT.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(bank)} entries to {OUT}")


if __name__ == "__main__":
    main()
