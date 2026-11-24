"""Unit tests for the parts of scripts/security.py that do not need Docker: the severity gate and the pins."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("security", ROOT / "scripts" / "security.py")
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


def osv(*groups, pkg="postcss", eco="npm", ver="8.4.31"):
    vulns = [{"id": i, "database_specific": {"severity": sev}} for i, _, sev in groups]
    return {"results": [{"packages": [{"package": {"name": pkg, "version": ver, "ecosystem": eco}, "vulnerabilities": vulns,
                                       "groups": [{"ids": [i], "max_severity": score} for i, score, _ in groups]}]}]}


def test_high_and_critical_block_everything_else_does_not():
    blocking, other = security.severities(osv(("A", "7.5", "HIGH"), ("B", "9.8", "CRITICAL"), ("C", "6.3", "MODERATE"), ("D", "2.3", "LOW")))
    assert {r[3] for r in blocking} == {"A", "B"}
    assert {r[3] for r in other} == {"C", "D"}


def test_boundaries_are_7_and_9():
    blocking, other = security.severities(osv(("A", "6.9", "x"), ("B", "7.0", "x"), ("C", "9.0", "x")))
    assert {r[3] for r in blocking} == {"B", "C"} and {r[3] for r in other} == {"A"}
    assert {r[4] for r in blocking} == {"HIGH", "CRITICAL"}


def test_unscored_advisory_falls_back_to_its_own_label():
    blocking, other = security.severities(osv(("A", "", "HIGH"), ("B", "", "MODERATE"), ("C", "", "")))
    assert [r[3] for r in blocking] == ["A"]
    assert {r[3]: r[4] for r in other} == {"B": "UNKNOWN", "C": "UNKNOWN"}  # warned about, never silently dropped


def test_clean_scan_has_no_findings():
    assert security.severities({"results": []}) == ([], [])


def test_scanner_images_are_pinned_by_digest_never_floating():
    for name, image in security.IMAGES.items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), name
    assert "==" in security.PIP_AUDIT


def test_workflow_pins_every_action_to_a_commit_sha_and_is_read_only():
    wf = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", wf)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses
    assert re.search(r"permissions:\s*\n\s+contents: read", wf)
    assert "push:" in wf and "pull_request:" in wf


def test_dockerfile_pins_its_bases_and_does_not_run_as_root():
    df = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    stages = set(re.findall(r"^FROM .* AS (\w+)$", df, re.M))
    for line in df.splitlines():
        if line.startswith("FROM ") or "COPY --from=" in line:
            ref = re.search(r"(?:FROM|--from=)\s*(\S+)", line).group(1)
            assert "@sha256:" in ref or ref in stages, line  # every external image pinned by digest
    assert "--frozen" in df and re.search(r"^USER\s+(?!root)\S+", df, re.M)
    final = df.split("AS builder", 1)[1].split("\nFROM ", 1)[1]
    assert "uv" not in re.findall(r"COPY --from=\S+", final) and "/bin/uv" not in final  # no package manager at runtime


def test_gitignore_covers_secrets_keys_uploads_and_resumes():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for needed in (".env", "*.pem", "uploads/", "resumes/", "**/*resume*.pdf"):
        assert needed in gi, needed
    assert "!.env.example" in gi


def test_piston_dockerfile_pins_bases_and_sources_and_drops_npm():
    df = (ROOT / "infra" / "piston" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ARG NODE_IMAGE=node@sha256:[0-9a-f]{64}", df) and re.search(r"ARG BUILD_IMAGE=buildpack-deps@sha256:[0-9a-f]{64}", df)
    assert len(re.findall(r"git checkout [0-9a-f]{40}", df)) == 2  # isolate and piston sources pinned by commit
    assert "npm ci" in df and "npm install" not in df
    final = df.rsplit("\nFROM ", 1)[1]
    assert "rm -rf /usr/local/lib/node_modules" in final and "/usr/local/bin/npm" in final
    assert not any(l.startswith("FROM ") and "buster" in l for l in df.splitlines())  # not the EOL Debian 10 base


def test_piston_lock_resolves_only_from_registry_or_the_two_pinned_git_deps():
    import json
    pkgs = json.loads((ROOT / "infra" / "piston" / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    for path, meta in pkgs.items():
        if not path:
            continue
        res = meta.get("resolved", "")
        assert res.startswith("https://registry.npmjs.org/") or re.fullmatch(r"git\+ssh://git@github.com/HexF/[\w-]+\.git#[0-9a-f]{40}", res), (path, res)


def test_no_nlp_stack_in_the_backend_lock():
    lock = (ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
    pyproject = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    for banned in ("spacy", "presidio", "en_core_web", "en-core-web", "thinc"):
        assert banned not in lock and banned not in pyproject, banned
