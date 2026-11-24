# Backend image. Build context is ./backend (see infra/docker-compose.yml).
# Both stages use the same pinned base image; uv is pinned by digest. Dependencies install from
# uv.lock with `--frozen` (fails if the lock is stale instead of silently re-resolving).

# ---- build stage: uv lives here only ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
COPY --from=ghcr.io/astral-sh/uv@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/backend
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime stage: no package manager, no build tools, unprivileged user ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Apply Debian security updates published after the pinned base image was built.
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY . /app/backend

RUN useradd --system --uid 10001 --no-create-home mocksie \
 && mkdir -p /app/data && chown mocksie /app/data
USER mocksie

EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
