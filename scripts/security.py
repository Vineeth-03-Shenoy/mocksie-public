#!/usr/bin/env python3
"""Security gate. `make security` and the GitHub workflow both run exactly this file.

    python scripts/security.py [check ...]        (no argument = every check)

checks:  locks  osv  pip-audit  npm-audit  trivy-fs  secrets  trivy-image

Scanner tools run from container images pinned by digest, so local and CI use identical versions.
A missing tool is a FAILURE, never a silent skip. Exit code 0 only if every selected check passes.
Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pinned by digest (resolved when this was written; update deliberately, not floating).
IMAGES = {
    "trivy": "ghcr.io/aquasecurity/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb",       # v0.76.2
    "osv": "ghcr.io/google/osv-scanner@sha256:a70ffffc2bfd33482953e5d6b64c6fcdb30239717a07b0f0d3ab2859ce3e4cde",          # v2.7.1
    "gitleaks": "ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f",      # v8.30.1
}
PIP_AUDIT = "pip-audit==2.10.1"
BLOCKING = {"CRITICAL", "HIGH"}

LOCKFILES = ["backend/uv.lock", "frontend/package-lock.json"]


class Fail(Exception):
    pass


# ---------------------------------------------------------------- helpers
def docker() -> str:
    found = shutil.which("docker")
    if not found:
        local = os.environ.get("LOCALAPPDATA")
        cand = Path(local) / "Programs/DockerDesktop/resources/bin/docker.exe" if local else None
        found = str(cand) if cand and cand.exists() else None
    if not found:
        raise Fail("Docker is required to run the pinned scanners (install Docker Desktop and start it)")
    return found


def env_with_docker() -> dict:
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    env["PATH"] = str(Path(docker()).parent) + os.pathsep + env.get("PATH", "")  # credential helper lives next to docker
    return env


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace", **kw)


def tool(image: str, args: list[str], mounts: list[tuple[str, str, str]] = (), cache: bool = False, env: list[str] = ()) -> subprocess.CompletedProcess:
    cmd = [docker(), "run", "--rm"]
    for host, cont, mode in mounts:
        cmd += ["-v", f"{host}:{cont}:{mode}"]
    if cache:
        cmd += ["-v", "mocksie-trivy-cache:/root/.cache/"]
    for e in env:
        cmd += ["-e", e]
    return run(cmd + [image] + args, env=env_with_docker())


def show(proc: subprocess.CompletedProcess, tail: int = 40) -> None:
    text = (proc.stdout or "") + (proc.stderr or "")
    print("\n".join(text.strip().splitlines()[-tail:]))


# ---------------------------------------------------------------- checks
def check_locks() -> None:
    """Every dependency pinned and the lock in sync: uv.lock (hashed) and package-lock.json (integrity)."""
    for lock in LOCKFILES:
        if not (ROOT / lock).exists():
            raise Fail(f"{lock} is missing: dependencies are not pinned")
    p = run(["uv", "lock", "--check"], cwd=ROOT / "backend")
    if p.returncode:
        show(p)
        raise Fail("backend/uv.lock is out of date with pyproject.toml (run: cd backend && uv lock)")
    npm = shutil.which("npm")
    if not npm:
        raise Fail("npm not found")
    p = run([npm, "ci", "--dry-run", "--ignore-scripts"], cwd=ROOT / "frontend")
    if p.returncode:
        show(p)
        raise Fail("frontend/package-lock.json is out of sync with package.json (run: cd frontend && npm install --package-lock-only)")
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    missing = [k for k, v in lock["packages"].items() if k and not v.get("link") and "integrity" not in v]
    if missing:
        raise Fail(f"{len(missing)} npm packages have no integrity hash, e.g. {missing[:3]}")
    print("  uv.lock current, package-lock.json current, every npm package has an integrity hash")


def severities(osv_json: dict) -> tuple[list[tuple], list[tuple]]:
    """Return (blocking, other) findings as (ecosystem, package, version, ids, severity)."""
    blocking, other = [], []
    for res in osv_json.get("results", []):
        for pkg in res.get("packages", []):
            vulns = {v["id"]: v for v in pkg.get("vulnerabilities", [])}
            for g in pkg.get("groups", []):
                try:
                    score = float(g.get("max_severity") or 0)
                except ValueError:
                    score = 0.0
                if score >= 9:
                    sev = "CRITICAL"
                elif score >= 7:
                    sev = "HIGH"
                elif score >= 4:
                    sev = "MEDIUM"
                elif score > 0:
                    sev = "LOW"
                else:  # no CVSS score: fall back to the advisory's own label
                    labels = {str((vulns.get(i, {}).get("database_specific") or {}).get("severity", "")).upper() for i in g.get("ids", [])}
                    sev = next((s for s in ("CRITICAL", "HIGH") if s in labels), "UNKNOWN")
                p = pkg["package"]
                row = (p["ecosystem"], p["name"], p["version"], ",".join(g.get("ids", [])), sev)
                (blocking if sev in BLOCKING else other).append(row)
    return blocking, other


def check_osv() -> None:
    p = tool(IMAGES["osv"], ["scan", "source", "--format", "json", *[a for l in LOCKFILES for a in ("-L", f"/src/{l}")]],
             mounts=[(str(ROOT), "/src", "ro")])
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        show(p)
        raise Fail("osv-scanner produced no JSON (is Docker running and online?)")
    blocking, other = severities(data)
    scanned = sum(len(r.get("packages", [])) for r in data.get("results", []))
    print(f"  scanned both lockfiles; {len(blocking)} HIGH/CRITICAL, {len(other)} lower or unscored")
    for row in other:
        print("    note:", *row)
    if blocking:
        for row in blocking:
            print("    BLOCKING:", *row)
        raise Fail(f"osv-scanner found {len(blocking)} HIGH/CRITICAL vulnerabilities")


def check_pip_audit() -> None:
    with tempfile.TemporaryDirectory() as d:
        req = Path(d) / "requirements.txt"
        p = run(["uv", "export", "--frozen", "--no-emit-project", "--format", "requirements-txt", "-o", str(req)], cwd=ROOT / "backend")
        if p.returncode:
            show(p)
            raise Fail("could not export the backend lock")
        # `python -m pip_audit` (not the pip-audit.exe shim, which Windows Application Control can block)
        p = run(["uv", "run", "--no-project", "--with", PIP_AUDIT, "python", "-I", "-m", "pip_audit", "-r", str(req),
                 "--require-hashes", "--disable-pip", "--progress-spinner", "off"])
        show(p, 20)
        if p.returncode:
            raise Fail("pip-audit found vulnerable backend dependencies (or could not run)")


def check_npm_audit() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise Fail("npm not found")
    p = run([npm, "audit", "--audit-level=high"], cwd=ROOT / "frontend")
    show(p, 12)
    if p.returncode:
        raise Fail("npm audit found HIGH/CRITICAL vulnerabilities")


def trivy_fs_args() -> list[str]:
    return ["fs", "--scanners", "vuln,secret", "--severity", "HIGH,CRITICAL", "--exit-code", "1", "--no-progress", "--timeout", "30m",
            "--skip-files", "/src/.env",
            "--skip-dirs", "**/node_modules", "--skip-dirs", "**/.venv", "--skip-dirs", "/src/.git",
            "--skip-dirs", "**/.next", "--skip-dirs", "**/__pycache__", "--skip-dirs", "**/.pytest_cache", "/src"]


def check_trivy_fs() -> None:
    p = tool(IMAGES["trivy"], trivy_fs_args(), mounts=[(str(ROOT), "/src", "ro")], cache=True)
    show(p, 25)
    if p.returncode:
        raise Fail("trivy fs found HIGH/CRITICAL vulnerabilities or secrets")


def check_secrets() -> None:
    p = run([sys.executable, str(ROOT / "scripts/secret_scan.py"), "--tree", "--history"])
    show(p, 25)
    if p.returncode:
        raise Fail("gitleaks found secrets")


def check_trivy_image() -> None:
    """Both images we build (backend, Piston on Debian 12) must have no fixable HIGH/CRITICAL findings or secrets."""
    failures = []
    scan = ["image", "--scanners", "vuln,secret", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--no-progress"]
    with tempfile.TemporaryDirectory() as d:
        for name, context in (("backend", ROOT / "backend"), ("piston", ROOT / "infra" / "piston")):
            tag = f"mocksie-{name}:scan"
            p = run([docker(), "build", "-q", "-t", tag, str(context)], env=env_with_docker())
            if p.returncode:
                show(p)
                raise Fail(f"{name} image failed to build")
            p = run([docker(), "save", "-o", str(Path(d) / f"{name}.tar"), tag], env=env_with_docker())
            if p.returncode:
                show(p)
                raise Fail(f"could not export the {name} image")
            p = tool(IMAGES["trivy"], scan + ["--exit-code", "1", "--input", f"/scan/{name}.tar"], mounts=[(d, "/scan", "ro")], cache=True)
            print(f"  {name} image:")
            show(p, 20)
            if p.returncode:
                failures.append(f"{name} image has fixable HIGH/CRITICAL vulnerabilities or secrets")
    if failures:
        raise Fail("; ".join(failures))


CHECKS = {
    "locks": check_locks, "osv": check_osv, "pip-audit": check_pip_audit, "npm-audit": check_npm_audit,
    "trivy-fs": check_trivy_fs, "secrets": check_secrets, "trivy-image": check_trivy_image,
}


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):   # tool output has box-drawing characters; a Windows console/redirect defaults to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    wanted = argv or list(CHECKS)
    unknown = [w for w in wanted if w not in CHECKS]
    if unknown:
        print(f"unknown check(s): {unknown}; choose from {list(CHECKS)}")
        return 2
    results = {}
    for name in wanted:
        print(f"\n== {name}")
        try:
            CHECKS[name]()
            results[name] = "PASS"
        except Fail as exc:
            print(f"  FAIL: {exc}")
            results[name] = "FAIL"
        except Exception as exc:  # noqa: BLE001 - a crashing check must still fail the gate
            print(f"  FAIL (error): {exc}")
            results[name] = "FAIL"
    print("\n== summary")
    for name, r in results.items():
        print(f"  {r}  {name}")
    return 0 if all(r == "PASS" for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
