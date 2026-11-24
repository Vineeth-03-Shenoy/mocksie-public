"""The candidate's voice websocket: /api/session/{token}/voice  (browser <-> our server <-> Deepgram).

Browser -> us: binary frames of 16 kHz 16-bit mono microphone audio, and JSON {"type": "playback_ended" | "done" | "resume" | "stop"}.
Us -> browser: binary frames of 24 kHz 16-bit mono speech, and JSON events (config, thinking, tts_start, tts_end, stop_playback,
paused, ended, error, fallback). The Deepgram key stays on the server. The interview token is the credential, as for the typed chat;
the Origin must be our own frontend (a page on another site must not be able to open this socket).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, WebSocket

from backend.config import Settings
from backend.store import Store
from backend.voice import settings as voice_settings
from backend.voice.session import VoiceSession

log = logging.getLogger("mocksie.voice")


class _SocketLink:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def receive(self) -> dict:
        return await self.ws.receive()

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)

    async def send_json(self, data: dict) -> None:
        await self.ws.send_json(data)


def build_voice_router(*, settings: Settings, store: Store, ops: Any, bank: list[dict],
                       flux_factory: Callable | None = None, aura_factory: Callable | None = None) -> APIRouter:
    router = APIRouter()
    active: set[str] = set()      # one voice connection per interview

    @router.websocket("/api/session/{token}/voice")
    async def voice(ws: WebSocket, token: str) -> None:
        if ws.headers.get("origin") not in settings.frontend_origins:
            await ws.close(code=1008)
            return
        rec = store.by_token(token)
        if not rec or rec["status"] != "active" or token in active:
            await ws.close(code=1008)
            return
        await ws.accept()
        if not settings.deepgram_api_key and flux_factory is None:
            await ws.send_json({"type": "fallback", "message": "Voice is not set up on the server. You can type your answers instead."})
            await ws.close()
            return
        active.add(token)
        try:
            session = VoiceSession(_SocketLink(ws), token, ops, voice_settings.load(store), api_key=settings.deepgram_api_key, bank=bank,
                                   flux_factory=flux_factory, aura_factory=aura_factory)
            await session.run()
        except Exception:  # noqa: BLE001 - never leave a socket hanging; the page falls back to typing
            log.exception("voice session crashed")
        finally:
            active.discard(token)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return router
