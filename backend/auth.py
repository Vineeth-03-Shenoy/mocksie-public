"""Single interviewer account: scrypt password hash (stdlib), signed expiring session cookie, login throttle.

No new dependencies. Generate the hash with:  python -m backend.auth hash   (prompts, prints ADMIN_PASSWORD_HASH=...)
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
import time
from collections import defaultdict, deque

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. A missing or malformed hash never verifies (the account is then locked)."""
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, _unb64(want))
    except (ValueError, TypeError):
        return False


def make_session(secret_key: str, ttl_s: int, now: float | None = None) -> str:
    payload = _b64(json.dumps({"exp": int((now or time.time()) + ttl_s), "role": "interviewer"}).encode())
    sig = _b64(hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(secret_key: str, token: str | None, now: float | None = None) -> dict | None:
    if not token or token.count(".") != 1:
        return None
    payload, sig = token.split(".")
    good = _b64(hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("role") != "interviewer" or data.get("exp", 0) < (now or time.time()):
        return None
    return data


class LoginThrottle:
    """At most `limit` failed logins per `window_s` per client; a success clears the count."""

    def __init__(self, limit: int = 5, window_s: int = 900) -> None:
        self.limit, self.window_s = limit, window_s
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, who: str, now: float) -> deque:
        q = self._fails[who]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, who: str, now: float | None = None) -> bool:
        return len(self._trim(who, now or time.time())) >= self.limit

    def fail(self, who: str, now: float | None = None) -> None:
        self._trim(who, now or time.time()).append(now or time.time())

    def ok(self, who: str) -> None:
        self._fails.pop(who, None)


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        pw = getpass.getpass("Interviewer password: ")
        if len(pw) < 10:
            sys.exit("use at least 10 characters")
        if getpass.getpass("Again: ") != pw:
            sys.exit("passwords differ")
        print("ADMIN_PASSWORD_HASH=" + hash_password(pw))
    else:
        sys.exit("usage: python -m backend.auth hash")
