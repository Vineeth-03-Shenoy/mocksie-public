"""Step 1: the API starts, answers /health, and only the single interviewer account can log in."""
import time

import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.api.main import COOKIE, create_app
from backend.config import Settings

PASSWORD = "correct horse battery"
KEY = "k" * 48


def settings(tmp_path, **over):
    base = dict(openai_api_key="x", interviewer_model="m1", fast_model="m2", judge_model="m3", store=False, pool_small=10**6,
                pool_judge=10**6, judge_over_budget="queue", judge_queue_threshold=0.9, database_path=str(tmp_path / "t.db"),
                admin_password_hash=auth.hash_password(PASSWORD), secret_key=KEY)
    return Settings(**{**base, **over})


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(settings(tmp_path)), base_url="http://localhost")


def test_health_is_public_and_minimal(client):
    for path in ("/health", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_api_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_login_flow_sets_a_strict_httponly_cookie_and_unlocks_me(client):
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me").json() == {"role": "interviewer"}
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_or_empty_password_is_rejected(client):
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": ""}).status_code == 422
    assert client.get("/api/auth/me").status_code == 401


def test_no_password_hash_configured_means_nobody_can_log_in(tmp_path):
    c = TestClient(create_app(settings(tmp_path, admin_password_hash="")), base_url="http://localhost")
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401


def test_login_is_throttled_after_five_failures_even_for_the_right_password(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


def test_unsafe_requests_from_another_origin_are_refused(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "http://localhost:3000"})
    assert ok.status_code == 200


def test_security_headers_are_set(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store"


def test_the_app_refuses_to_start_with_a_weak_secret_key(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(settings(tmp_path, secret_key="short"))


# ---- the auth primitives
def test_password_hash_roundtrip_and_malformed_hashes_never_verify():
    h = auth.hash_password("pw-1234567890")
    assert h.startswith("scrypt$") and PASSWORD not in h
    assert auth.verify_password("pw-1234567890", h) and not auth.verify_password("other", h)
    for bad in ("", "plain", "scrypt$1$2", "md5$a$b$c$d$e", "scrypt$x$y$z$a$b"):
        assert not auth.verify_password("pw-1234567890", bad)
    assert auth.hash_password("same") != auth.hash_password("same")  # random salt


def test_session_tokens_are_signed_and_expire():
    t = auth.make_session(KEY, 60, now=1000)
    assert auth.read_session(KEY, t, now=1030)
    assert auth.read_session(KEY, t, now=1100) is None                    # expired
    assert auth.read_session("z" * 48, t, now=1030) is None               # other key
    assert auth.read_session(KEY, t[:-2] + "xx", now=1030) is None        # tampered
    assert auth.read_session(KEY, None) is None and auth.read_session(KEY, "a.b.c") is None


def test_throttle_window_expires():
    th = auth.LoginThrottle(limit=2, window_s=10)
    th.fail("ip", now=100), th.fail("ip", now=101)
    assert th.blocked("ip", now=102) and not th.blocked("ip", now=200)
    th.fail("ip2", now=time.time())
    th.ok("ip2")
    assert not th.blocked("ip2")
