"""Mocksie API. Run with one command from the repo root:  python -m backend   (see backend/__main__.py)

Routers are added per milestone step; every /api/admin/* route needs the interviewer session cookie.
The candidate token (session routes) must never reach results endpoints.
"""
from __future__ import annotations

import time
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import auth
from backend.api.interviews import build_router
from backend.api.voice import build_voice_router
from backend.config import Settings
from backend.content import bank as qbank
from backend.guardrails.pii import Redactor
from backend.judge.client import PistonClient
from backend.llm.client import LLMClient, PIIGuard
from backend.llm.usage import UsageTracker
from backend.api.results import build_results_router
from backend.scoring.service import ScoringService
from backend.store import Store

COOKIE = "mocksie_session"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
MIN_SECRET = 32


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def create_app(settings: Settings | None = None, *, transport: Callable[[dict], dict] | None = None,
               clock: Callable[[], float] = time.time, judge: PistonClient | None = None,
               background_scoring: bool = True, voice_flux: Callable | None = None, voice_aura: Callable | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    if len(s.secret_key) < MIN_SECRET:
        raise RuntimeError(f"SECRET_KEY must be set to at least {MIN_SECRET} random characters (see .env.example)")
    app = FastAPI(title="Mocksie", docs_url=None, redoc_url=None, openapi_url=None)  # no public API docs
    app.state.settings = s
    app.state.throttle = auth.LoginThrottle()

    app.add_middleware(CORSMiddleware, allow_origins=list(s.frontend_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def guards(request: Request, call_next):
        # CSRF: the cookie is SameSite=Strict, and a browser-sent unsafe request must come from our own frontend.
        origin = request.headers.get("origin")
        if request.method in UNSAFE and origin is not None and origin not in s.frontend_origins:
            return Response('{"detail":"origin not allowed"}', status_code=403, media_type="application/json")
        resp = await call_next(request)
        resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
        return resp

    def require_admin(request: Request) -> dict:
        data = auth.read_session(s.secret_key, request.cookies.get(COOKIE))
        if not data:
            raise HTTPException(status_code=401, detail="login required")
        return data

    app.state.require_admin = require_admin

    # interviews: one LLM client per interview so each has its own name/term tripwire; token usage is shared per day
    store = Store(s.database_path)
    usage = UsageTracker(s.database_path, {"small": s.pool_small, "judge": s.pool_judge})
    app.state.store, app.state.usage = store, usage

    def llm_factory(redactor: Redactor) -> LLMClient:
        guard = PIIGuard()
        guard.attach(redactor)
        return LLMClient(s, usage, guard, transport)

    bank = qbank.load()
    scoring = ScoringService(store, llm_factory, bank, clock, background=background_scoring)
    app.state.scoring = scoring
    admin_router, session_router, ops = build_router(store=store, llm_factory=llm_factory, require_admin=require_admin, bank=bank,
                                                clock=clock, max_pages=s.max_resume_pages, max_bytes=s.max_resume_bytes,
                                                judge=judge or PistonClient(s.judge_url), on_finished=scoring.kick)
    app.include_router(admin_router)
    app.include_router(build_results_router(store=store, service=scoring, require_admin=require_admin, clock=clock))
    app.include_router(session_router)
    app.include_router(build_voice_router(settings=s, store=store, ops=ops, bank=bank, flux_factory=voice_flux, aura_factory=voice_aura))

    @app.get("/health")
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        who = request.client.host if request.client else "?"
        if app.state.throttle.blocked(who):
            raise HTTPException(status_code=429, detail="too many attempts; wait 15 minutes")
        if not auth.verify_password(body.password, s.admin_password_hash):
            app.state.throttle.fail(who)
            raise HTTPException(status_code=401, detail="wrong password")
        app.state.throttle.ok(who)
        response.set_cookie(COOKIE, auth.make_session(s.secret_key, s.session_ttl_s), max_age=s.session_ttl_s,
                            httponly=True, samesite="strict", secure=s.cookie_secure, path="/")
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(_: dict = Depends(require_admin)):
        return {"role": "interviewer"}

    return app


def _default_app() -> FastAPI:  # `uvicorn backend.api.main:app` (Docker CMD)
    return create_app()


def __getattr__(name: str):  # build the app lazily so importing this module never needs a configured .env
    if name == "app":
        return _default_app()
    raise AttributeError(name)
