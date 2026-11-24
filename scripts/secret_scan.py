#!/usr/bin/env python3
"""Secret scanning with gitleaks (pinned image, runs in Docker).

    python scripts/secret_scan.py --staged     what a commit would contain (the pre-commit hook)
    python scripts/secret_scan.py --tree       every non-ignored file in the working tree
    python scripts/secret_scan.py --history    all commits (CI checks out with full history)

Findings are redacted in the output. Exit code 1 = a secret was found (or the scan could not run).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITLEAKS = "ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"  # v8.30.1


def docker() -> str:
    found = shutil.which("docker")
    if not found and os.environ.get("LOCALAPPDATA"):
        cand = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
        found = str(cand) if cand.exists() else None
    if not found:
        sys.exit("secret scan: Docker is required (the scan is not skipped). Start Docker Desktop, or bypass once with: git commit --no-verify")
    return found


def git(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, **kw)


def gitleaks(args: list[str], mount: Path, extra_env: list[str] = ()) -> int:
    d = docker()
    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    env["PATH"] = str(Path(d).parent) + os.pathsep + env.get("PATH", "")
    cmd = [d, "run", "--rm", "-v", f"{mount}:/scan:ro"]
    for e in extra_env:
        cmd += ["-e", e]
    p = subprocess.run(cmd + [GITLEAKS] + args + ["--redact", "--no-banner", "--exit-code", "1", "-v"],
                       text=True, capture_output=True, env=env, encoding="utf-8", errors="replace")
    out = (p.stdout + p.stderr).strip()
    if p.returncode:
        print(out)
    return p.returncode


def scan_snapshot(files_cmd: list[str]) -> int:
    """Copy exactly the files in question to a temp dir and scan that (no git needed inside the container)."""
    listed = git(*files_cmd)
    if listed.returncode:
        sys.exit(listed.stderr)
    names = [n for n in listed.stdout.split("\0") if n]
    if not names:
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        for n in names:
            src = ROOT / n
            if src.is_file():
                dest = Path(tmp) / n
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        return gitleaks(["dir", "/scan"], Path(tmp))


def staged() -> int:
    """Scan the STAGED version of each file (what would be committed), not the working copy."""
    listed = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    names = [n for n in listed.stdout.split("\0") if n]
    if not names:
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        for n in names:
            blob = subprocess.run(["git", "show", f":{n}"], cwd=ROOT, capture_output=True)
            if blob.returncode == 0:
                dest = Path(tmp) / n
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob.stdout)
        return gitleaks(["dir", "/scan"], Path(tmp))


def tree() -> int:
    return scan_snapshot(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])


def history() -> int:
    if git("rev-parse", "--verify", "HEAD").returncode:
        return 0  # no commits yet
    return gitleaks(["git", "/scan"], ROOT, extra_env=["GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=safe.directory", "GIT_CONFIG_VALUE_0=/scan"])


def main(argv: list[str]) -> int:
    modes = {"--staged": staged, "--tree": tree, "--history": history}
    chosen = [m for m in argv if m in modes]
    if not chosen:
        print(__doc__)
        return 2
    rc = 0
    for m in chosen:
        r = modes[m]()
        print(f"secret scan {m[2:]}: {'FOUND SECRETS' if r else 'clean'}")
        rc = rc or r
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
