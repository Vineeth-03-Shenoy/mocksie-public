"""Verifies the sample coding content: the generated Python starters, with the reference body dropped in,
must pass every sample and hidden test through real stdin/stdout."""
import inspect
import subprocess
import sys

import pytest

sys.path.insert(0, "backend/content/tools")
import coding_specs as cs  # noqa: E402


def normalise(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def solution_source(spec) -> str:
    starter = cs.python_starter(spec)
    ref_src = inspect.getsource(spec["fn"]).replace(f"def {spec['fn'].__name__}(", "def _ref(", 1)
    params = cs.PY_PARAMS[spec["inp"]]
    return ref_src + "\n" + starter.replace("    pass\n", f"    return _ref({params})\n", 1)


@pytest.mark.parametrize("book_id", sorted(cs.SPECS))
def test_starter_with_reference_passes_all_tests(book_id, tmp_path):
    spec = cs.SPECS[book_id]
    path = tmp_path / "sol.py"
    path.write_text(solution_source(spec), encoding="utf-8")
    samples, hidden = cs.build_tests(spec)
    assert samples and hidden
    for t in samples + hidden:
        run = subprocess.run([sys.executable, "-I", str(path)], input=t["stdin"], capture_output=True, text=True, timeout=20)
        assert run.returncode == 0, run.stderr
        assert normalise(run.stdout) == normalise(t["expected"]), (book_id, t["stdin"][:80])


@pytest.mark.parametrize("book_id", sorted(cs.SPECS))
def test_untouched_starter_fails_tests(book_id):
    """Submitting the starter unchanged must not look like a pass (it is treated as a skip upstream)."""
    spec = cs.SPECS[book_id]
    _, hidden = cs.build_tests(spec)
    test = next(t for t in hidden if t["expected"].strip() != "None")
    run = subprocess.run([sys.executable, "-I", "-c", cs.python_starter(spec)], input=test["stdin"], capture_output=True, text=True)
    assert normalise(run.stdout) != normalise(test["expected"])


def test_reference_functions_on_hand_worked_examples():
    assert cs.ref_sum_of_evens([1, 2, 3, 4]) == 6 and cs.ref_sum_of_evens([7, 9]) == 0 and cs.ref_sum_of_evens([]) == 0
    assert cs.ref_sum_of_evens([-2, -4, 5]) == -6 and cs.ref_sum_of_evens([0]) == 0
    assert cs.ref_longest_run([1, 1, 2, 2, 2, 3]) == 3 and cs.ref_longest_run([5, 6, 5]) == 1
    assert cs.ref_longest_run([]) == 0 and cs.ref_longest_run([4]) == 1 and cs.ref_longest_run([9, 9, 1, 9, 9, 9]) == 3


def test_every_spec_has_edge_cases_and_a_difficulty():
    assert set(cs.SPECS) == {"DEMO-C1", "DEMO-C2"}
    for spec in cs.SPECS.values():
        assert 1 <= spec["difficulty"] <= 3 and any(edge for _, edge in spec["hidden"])
