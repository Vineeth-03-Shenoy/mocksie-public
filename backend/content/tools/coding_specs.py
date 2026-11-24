"""Judge-ready definitions for the SAMPLE coding problems (architecture 4.2, 4.7).

Each spec pins the contract down so the deterministic judge has one right answer. Tests are
{"stdin": str, "expected": str, "weight": int, "edge": bool}; the expected output is produced by the reference
function below, never typed by hand. Comparison rule for the judge: strip trailing whitespace on each line and
trailing blank lines. These two problems were written for the public sample bank; the production bank is private.
"""
from __future__ import annotations

from collections import Counter

from starters import c_starter, java_starter

# ---------- reference solutions (Python) ----------

def ref_sum_of_evens(nums): return sum(x for x in nums if x % 2 == 0)


def ref_longest_run(nums):
    best = run = 0
    for i, x in enumerate(nums):
        run = run + 1 if i and nums[i - 1] == x else 1
        best = max(best, run)
    return best


# ---------- input / output encoding ----------

def enc_in(kind, args):
    if kind == "str": return f"{args[0]}\n"
    if kind == "two_str": return f"{args[0]}\n{args[1]}\n"
    if kind == "int": return f"{args[0]}\n"
    if kind == "ints": return f"{len(args[0])}\n{' '.join(map(str, args[0]))}\n"
    if kind == "ints_int":
        return f"{len(args[0])}\n{' '.join(map(str, args[0]))}\n{args[1]}\n"
    if kind == "two_ints":
        a, b = args
        return f"{len(a)}\n{' '.join(map(str, a))}\n{len(b)}\n{' '.join(map(str, b))}\n"
    raise ValueError(kind)

def enc_out(kind, r):
    if kind == "int": return f"{r}\n"
    if kind == "opt_int": return "NONE\n" if r is None else f"{r}\n"
    if kind == "bool": return "TRUE\n" if r else "FALSE\n"
    if kind == "str": return f"{r}\n"
    if kind == "opt_char": return "NONE\n" if r is None else f"{r}\n"
    if kind == "ints": return f"{' '.join(map(str, r))}\n"
    if kind == "pair_or_none": return "NONE\n" if r is None else f"{r[0]} {r[1]}\n"
    if kind == "freq": return "".join(f"{k} {v}\n" for k, v in r)
    raise ValueError(kind)

IO_TEXT = {
    "str": "Line 1: the string (may be empty).",
    "two_str": "Line 1: first string. Line 2: second string (either may be empty).",
    "int": "Line 1: the integer n.",
    "ints": "Line 1: count k. Line 2: k space-separated integers (empty line if k = 0).",
    "ints_int": "Line 1: count k. Line 2: k space-separated integers (empty line if k = 0). Line 3: an integer.",
    "two_ints": "Line 1: count k, line 2: k integers (first list). Line 3: count m, line 4: m integers (second list).",
}
OUT_TEXT = {
    "int": "Print the integer.",
    "opt_int": "Print the integer, or NONE if there is no answer.",
    "bool": "Print TRUE or FALSE.",
    "str": "Print the string.",
    "opt_char": "Print the character, or NONE if there is none.",
    "ints": "Print the integers separated by spaces (an empty line if the list is empty).",
    "pair_or_none": "Print the two indices i and j (i < j) separated by a space, or NONE.",
    "freq": "For each distinct value in ascending order print: value count (one per line). Print nothing for an empty list.",
}

# ---------- the specs ----------
# sample = shown to the candidate; hidden = judge only. Each entry is (args, edge?)
S = lambda *a: a  # noqa: E731

SPECS = {
    "DEMO-C1": dict(
        slug="sum_of_evens", difficulty=1, fn=ref_sum_of_evens, inp="ints", out="int",
        contract="Return the sum of the even numbers in the list. Negative numbers count, zero is even, and an empty list (or one with no even numbers) gives 0.",
        sample=[S([1, 2, 3, 4]), S([7, 9])],
        hidden=[(S([]), True), (S([0]), True), (S([-2, -4, 5]), True), (S([2]), False), (S([10, 20, 30]), False),
                (S([1, 3, 5, 7]), True), (S([-1, 2, -3, 4, -5, 6]), False), (S(list(range(-500, 500))), False)],
    ),
    "DEMO-C2": dict(
        slug="longest_run", difficulty=2, fn=ref_longest_run, inp="ints", out="int",
        contract="A run is a stretch of equal values next to each other. Return the length of the longest run in the list (0 for an empty list).",
        sample=[S([1, 1, 2, 2, 2, 3]), S([5, 6, 5])],
        hidden=[(S([]), True), (S([4]), True), (S([7, 7, 7, 7]), True), (S([1, 2, 3, 4]), False), (S([2, 2, 3, 3, 3, 2, 2]), False),
                (S([9, 9, 1, 9, 9, 9]), True), (S([-1, -1, -1, 0, 0]), False), (S([1] * 1500 + [2] * 700 + [1] * 1501), True)],
    ),
}

# ---------- starter code (Python only until the Piston judge can compile-check Java and C) ----------
PY_PARSE = {
    "str": "    s = sys.stdin.readline().rstrip('\\n')\n",
    "two_str": "    a = sys.stdin.readline().rstrip('\\n')\n    b = sys.stdin.readline().rstrip('\\n')\n",
    "int": "    n = int(sys.stdin.readline())\n",
    "ints": "    k = int(sys.stdin.readline())\n    nums = list(map(int, sys.stdin.readline().split()))\n",
    "ints_int": "    k = int(sys.stdin.readline())\n    nums = list(map(int, sys.stdin.readline().split()))\n    x = int(sys.stdin.readline())\n",
    "two_ints": "    k = int(sys.stdin.readline())\n    a = list(map(int, sys.stdin.readline().split()))\n    m = int(sys.stdin.readline())\n    b = list(map(int, sys.stdin.readline().split()))\n",
}
PY_CALL_ARGS = {"str": "s", "two_str": "a, b", "int": "n", "ints": "nums", "ints_int": "nums, x", "two_ints": "a, b"}
PY_PRINT = {
    "int": "    print(result)\n",
    "opt_int": "    print('NONE' if result is None else result)\n",
    "bool": "    print('TRUE' if result else 'FALSE')\n",
    "str": "    print(result)\n",
    "opt_char": "    print('NONE' if result is None else result)\n",
    "ints": "    print(' '.join(map(str, result)))\n",
    "pair_or_none": "    print('NONE' if result is None else f'{result[0]} {result[1]}')\n",
    "freq": "    for value, count in result:\n        print(value, count)\n",
}
PY_PARAMS = {"str": "s", "two_str": "a, b", "int": "n", "ints": "nums", "ints_int": "nums, x", "two_ints": "a, b"}
PY_RETURN_HINT = {
    "opt_int": "an int, or None", "pair_or_none": "a tuple (i, j), or None", "freq": "a list of (value, count) tuples sorted by value",
    "opt_char": "a one-character string, or None", "ints": "a list of ints", "bool": "True or False", "int": "an int", "str": "a string",
}

def python_starter(spec) -> str:
    name, inp, out = spec["slug"], spec["inp"], spec["out"]
    if name == "reverse_list":
        return (
            "import sys\n\n\nclass Node:\n    def __init__(self, val, next=None):\n        self.val = val\n        self.next = next\n\n\n"
            "def reverse_list(head):\n    # Return the new head. Re-point the nodes; do not build a new list.\n    pass\n\n\n"
            "def main():\n    k = int(sys.stdin.readline())\n    vals = list(map(int, sys.stdin.readline().split()))\n"
            "    head = None\n    for v in reversed(vals):\n        head = Node(v, head)\n    node = reverse_list(head)\n"
            "    out = []\n    while node is not None:\n        out.append(str(node.val))\n        node = node.next\n"
            "    print(' '.join(out))\n\n\nmain()\n"
        )
    return (
        f"import sys\n\n\ndef {name}({PY_PARAMS[inp]}):\n    # Return {PY_RETURN_HINT[out]}.\n    pass\n\n\n"
        f"def main():\n{PY_PARSE[inp]}    result = {name}({PY_CALL_ARGS[inp]})\n{PY_PRINT[out]}\n\nmain()\n"
    )

def build_tests(spec):
    f, inp, out = spec["fn"], spec["inp"], spec["out"]
    def mk(args, edge, visible):
        return {"stdin": enc_in(inp, args), "expected": enc_out(out, f(*args)),
                "weight": 2 if edge else 1, "edge": bool(edge), "visible": visible}
    samples = [mk(a, False, True) for a in spec["sample"]]
    hidden = [mk(a, e, False) for a, e in spec["hidden"]]
    return samples, hidden

def signature(spec) -> str:
    p = PY_PARAMS[spec["inp"]] if spec["slug"] != "reverse_list" else "head"
    return f"{spec['slug']}({p})"

def apply(bank: list[dict]) -> None:
    """Fill the coding entries of the draft bank with judge-ready fields."""
    for e in bank:
        spec = SPECS.get(e["book_id"])
        if not spec:
            continue
        samples, hidden = build_tests(spec)
        e.update({
            "difficulty": spec["difficulty"],
            "difficulty_source": "authored",
            "slug": spec["slug"],
            "signature": signature(spec),
            "statement": spec["contract"],
            "io": {"input": IO_TEXT[spec["inp"]], "output": OUT_TEXT[spec["out"]], "input_kind": spec["inp"], "output_kind": spec["out"]},
            "starter_code": {"python": python_starter(spec), "java": java_starter(spec), "c": c_starter(spec)},
            "sample_tests": samples,
            "hidden_tests": hidden,
            "time_limit_ms": 2000,
            "memory_mb": 256,
        })
        e["review"] = [r for r in e["review"] if r != "java_c_starter_missing"]
