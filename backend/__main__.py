"""One command to run the API:  python -m backend  (from the repo root; reads .env)."""
import os

import uvicorn

from backend.api.main import create_app
from backend.config import Settings

if __name__ == "__main__":
    uvicorn.run(create_app(Settings.from_env()), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
