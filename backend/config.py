"""Settings from environment / .env. Model IDs are never hard-coded elsewhere."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (does not override variables already set)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip()  # allow trailing comments
        os.environ.setdefault(k.strip(), v)


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    interviewer_model: str
    fast_model: str
    judge_model: str
    store: bool
    pool_small: int
    pool_judge: int
    judge_over_budget: str  # "queue" | "pay"
    judge_queue_threshold: float
    database_path: str
    admin_password_hash: str = ""
    secret_key: str = ""
    session_ttl_s: int = 8 * 3600
    cookie_secure: bool = False  # True behind HTTPS; localhost dev uses plain http
    frontend_origins: tuple = ("http://localhost:3000", "http://127.0.0.1:3000")
    max_resume_pages: int = 2
    max_resume_bytes: int = 2_000_000
    judge_url: str = "http://localhost:2000"
    deepgram_api_key: str = ""   # server side only: never sent to the browser

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ.get
        db = e("DATABASE_URL", "sqlite:///./mocksie.db").replace("sqlite:///", "", 1)
        return cls(
            openai_api_key=e("OPENAI_API_KEY", ""),
            interviewer_model=e("INTERVIEWER_MODEL", "gpt-5.4-mini"),
            fast_model=e("FAST_MODEL", "gpt-5.4-nano"),
            judge_model=e("JUDGE_MODEL", "gpt-5.4"),
            store=e("OPENAI_STORE", "false").lower() == "true",
            pool_small=int(e("DAILY_POOL_SMALL", "2500000")),
            pool_judge=int(e("DAILY_POOL_JUDGE", "250000")),
            judge_over_budget=e("JUDGE_OVER_BUDGET", "queue").lower(),
            judge_queue_threshold=float(e("JUDGE_QUEUE_THRESHOLD", "0.9")),
            database_path=str((ROOT / db).resolve()) if not Path(db).is_absolute() else db,
            admin_password_hash=e("ADMIN_PASSWORD_HASH", ""),
            secret_key=e("SECRET_KEY", ""),
            session_ttl_s=int(e("SESSION_TTL_HOURS", "8")) * 3600,
            cookie_secure=e("COOKIE_SECURE", "false").lower() == "true",
            frontend_origins=tuple(o.strip() for o in e("FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()),
            max_resume_pages=int(e("MAX_RESUME_PAGES", "2")),
            max_resume_bytes=int(e("MAX_RESUME_BYTES", "2000000")),
            judge_url=e("JUDGE_URL", "http://localhost:2000"),
            deepgram_api_key=e("DEEPGRAM_API_KEY", ""),
        )
