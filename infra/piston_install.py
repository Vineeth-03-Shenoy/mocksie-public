"""One-time: install the language runtimes we use into a running Piston (python, java, gcc for C).

    python infra/piston_install.py [http://localhost:2000]

Idempotent. Piston downloads each package from its own package repository, so this needs
internet once; afterwards the runtimes live in the piston_packages volume.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:2000").rstrip("/")
WANTED = ["python", "java", "gcc"]  # gcc provides the `c` language


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if body else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def version_key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in v.replace("-", ".").split("."))


def main():
    try:
        packages = call("/api/v2/packages")
    except (urllib.error.URLError, ConnectionError) as exc:
        sys.exit(f"Piston is not reachable at {BASE}: {exc}\nStart it: docker compose -f infra/docker-compose.yml up -d piston")
    for lang in WANTED:
        candidates = [p for p in packages if p["language"] == lang]
        if not candidates:
            print(f"{lang}: not offered by this Piston")
            continue
        if any(p.get("installed") for p in candidates):
            print(f"{lang}: already installed")
            continue
        best = max(candidates, key=lambda p: version_key(p["language_version"]))
        print(f"{lang}: installing {best['language_version']} ...", flush=True)
        try:
            print("   ", call("/api/v2/packages", {"language": lang, "version": best["language_version"]}))
        except urllib.error.HTTPError as exc:
            print("    failed:", exc.read()[:200])
    print("installed runtimes:", sorted({(r["language"], r["version"]) for r in call("/api/v2/runtimes")}))


if __name__ == "__main__":
    main()
