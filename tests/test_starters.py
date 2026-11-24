"""Compile and run the Java and C starters (with the reference body dropped in) against every test.

Needs a C compiler and a JDK. Where they are missing the tests SKIP, they never fake a pass:
  - C   : gcc / cc / clang on PATH, or the `ziglang` pip package (uv run --with ziglang ...)
  - Java: javac on PATH, or MOCKSIE_JAVA_HOME pointing at a JDK
When Piston is up, backend/judge/verify_bank.py does the same check through the real judge.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, "backend/content/tools")
import coding_specs as cs  # noqa: E402
import starters  # noqa: E402


def c_compiler():
    for name in ("gcc", "cc", "clang"):
        if shutil.which(name):
            return [shutil.which(name)]
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        return None


def java_tools():
    home = os.environ.get("MOCKSIE_JAVA_HOME")
    if home and os.path.exists(os.path.join(home, "bin", "javac.exe" if os.name == "nt" else "javac")):
        ext = ".exe" if os.name == "nt" else ""
        return os.path.join(home, "bin", "javac" + ext), os.path.join(home, "bin", "java" + ext)
    if shutil.which("javac") and shutil.which("java"):
        return shutil.which("javac"), shutil.which("java")
    return None


CC, JAVA = c_compiler(), java_tools()


def norm(text):
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def run_all(cmd, spec, tmp_path):
    samples, hidden = cs.build_tests(spec)
    for t in samples + hidden:
        try:
            run = subprocess.run(cmd, input=t["stdin"], capture_output=True, text=True, timeout=30)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 4551:  # Windows Application Control blocks this fresh .exe
                pytest.skip("compiled, but Windows Application Control blocked running it; verify via Piston")
            raise
        assert run.returncode == 0, run.stderr[:500]
        assert norm(run.stdout) == norm(t["expected"]), (spec["slug"], t["stdin"][:60], run.stdout[:60])


@pytest.mark.skipif(CC is None, reason="no C compiler (pip: ziglang)")
@pytest.mark.parametrize("book_id", sorted(cs.SPECS))
def test_c_reference_passes_every_test(book_id, tmp_path):
    spec = cs.SPECS[book_id]
    src, exe = tmp_path / "sol.c", tmp_path / ("sol.exe" if os.name == "nt" else "sol")
    src.write_text(starters.c_starter(spec, body=starters.REF_C[spec["slug"]]), encoding="utf-8")
    build = subprocess.run(CC + ["-O2", "-Wall", "-o", str(exe), str(src)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[:800]
    run_all([str(exe)], spec, tmp_path)


@pytest.mark.skipif(JAVA is None, reason="no JDK (set MOCKSIE_JAVA_HOME)")
@pytest.mark.parametrize("book_id", sorted(cs.SPECS))
def test_java_reference_passes_every_test(book_id, tmp_path):
    spec = cs.SPECS[book_id]
    (tmp_path / "Main.java").write_text(starters.java_starter(spec, body=starters.REF_JAVA[spec["slug"]]), encoding="utf-8")
    build = subprocess.run([JAVA[0], "-d", str(tmp_path), str(tmp_path / "Main.java")], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[:800]
    run_all([JAVA[1], "-cp", str(tmp_path), "Main"], spec, tmp_path)


@pytest.mark.skipif(CC is None, reason="no C compiler")
def test_untouched_c_starter_compiles_but_does_not_pass(tmp_path):
    spec = cs.SPECS["DEMO-C1"]
    src, exe = tmp_path / "s.c", tmp_path / ("s.exe" if os.name == "nt" else "s")
    src.write_text(starters.c_starter(spec), encoding="utf-8")
    assert subprocess.run(CC + ["-o", str(exe), str(src)], capture_output=True).returncode == 0
    _, hidden = cs.build_tests(spec)
    test = next(t for t in hidden if t["expected"].strip() != "0")       # the stub returns 0
    out = subprocess.run([str(exe)], input=test["stdin"], capture_output=True, text=True).stdout
    assert norm(out) != norm(test["expected"])


@pytest.mark.skipif(JAVA is None, reason="no JDK")
def test_untouched_java_starter_compiles(tmp_path):
    (tmp_path / "Main.java").write_text(starters.java_starter(cs.SPECS["DEMO-C1"]), encoding="utf-8")
    assert subprocess.run([JAVA[0], "-d", str(tmp_path), str(tmp_path / "Main.java")], capture_output=True).returncode == 0


def test_every_problem_has_java_and_c_starters_and_references():
    assert {s["slug"] for s in cs.SPECS.values()} == set(starters.REF_JAVA) == set(starters.REF_C)
    for spec in cs.SPECS.values():
        assert "class Main" in starters.java_starter(spec) and "int main" in starters.c_starter(spec)
