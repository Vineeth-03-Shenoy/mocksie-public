"""Direct Deepgram clients over raw websockets (Pipecat was replaced by this; see README):

  FluxStream  speech to text with Flux's own turn detection:  wss://api.deepgram.com/v2/listen   (flux-general-en)
  AuraStream  text to speech with Aura-2:                      wss://api.deepgram.com/v1/speak

The API key is only ever used here, on the server. Nothing in this module logs transcripts or the text being spoken.
`connect` can be replaced in tests with a fake socket factory.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import urlencode

FLUX_URL = "wss://api.deepgram.com/v2/listen"
AURA_URL = "wss://api.deepgram.com/v1/speak"
FLUX_MODEL = "flux-general-en"
STT_RATE = 16000            # browser -> server: 16 kHz, 16-bit little-endian mono
TTS_RATE = 24000            # Aura-2 linear16 default; the browser plays it back at this rate
MAX_SPEAK_CHARS = 2000      # per Speak message (Deepgram limit)

Connect = Callable[[str, dict], Awaitable[Any]]


class DeepgramError(Exception):
    pass


async def default_connect(url: str, headers: dict) -> Any:
    from websockets.asyncio.client import connect  # imported here so tests need no network stack
    return await connect(url, additional_headers=headers, open_timeout=10, max_size=2 ** 22)


def _headers(api_key: str) -> dict:
    if not api_key:
        raise DeepgramError("DEEPGRAM_API_KEY is not set")
    return {"Authorization": f"Token {api_key}"}


@dataclass(frozen=True)
class TurnEvent:
    event: str              # StartOfTurn | Update | EagerEndOfTurn | TurnResumed | EndOfTurn
    transcript: str
    turn_index: int
    confidence: float
    trigger: str | None     # on EndOfTurn: model | manual | timeout


@dataclass(frozen=True)
class Problem:
    kind: str               # Warning | Error | ConfigureFailure
    code: str
    description: str


def flux_url(*, eot_threshold: float, eager_eot_threshold: float | None, eot_timeout_ms: int, model: str = FLUX_MODEL) -> str:
    q: dict[str, Any] = {"model": model, "encoding": "linear16", "sample_rate": STT_RATE, "eot_threshold": eot_threshold,
                         "eot_timeout_ms": eot_timeout_ms, "mip_opt_out": "true"}     # opt out of Deepgram's model-improvement data use
    if eager_eot_threshold is not None:
        q["eager_eot_threshold"] = eager_eot_threshold
    return FLUX_URL + "?" + urlencode(q)


def parse_flux(raw: str | bytes) -> TurnEvent | Problem | None:
    """One server message -> a TurnEvent, a Problem, or None (Connected, ConfigureSuccess and unknown types are ignored)."""
    if isinstance(raw, bytes):
        return None
    try:
        m = json.loads(raw)
    except ValueError:
        return None
    t = m.get("type")
    if t == "TurnInfo" and m.get("event"):
        try:
            conf = float(m.get("end_of_turn_confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        return TurnEvent(str(m["event"]), str(m.get("transcript") or ""), int(m.get("turn_index") or 0), conf, m.get("trigger"))
    if t in ("Warning", "ConfigureFailure"):
        return Problem(t, str(m.get("code") or ""), str(m.get("description") or "")[:200])
    if t == "Error":
        return Problem("Error", str(m.get("code") or ""), str(m.get("description") or "")[:200])
    return None


class FluxStream:
    """One Flux connection. Audio in (16 kHz linear16, 80 ms chunks recommended), turn events out."""

    def __init__(self, api_key: str, *, eot_threshold: float = 0.7, eager_eot_threshold: float | None = None,
                 eot_timeout_ms: int = 6000, connect: Connect = default_connect) -> None:
        self._key, self._connect = api_key, connect
        self.url = flux_url(eot_threshold=eot_threshold, eager_eot_threshold=eager_eot_threshold, eot_timeout_ms=eot_timeout_ms)
        self._ws: Any = None

    @property
    def is_open(self) -> bool:
        return self._ws is not None

    async def open(self) -> None:
        self._ws = await self._connect(self.url, _headers(self._key))

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(chunk)

    async def force_end_turn(self) -> None:
        """End the current turn on the audio transcribed so far (Flux answers with EndOfTurn, trigger=manual)."""
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "ForceEndTurn"}))

    async def configure(self, *, keyterms: list[str] | None = None, thresholds: dict | None = None) -> None:
        msg: dict[str, Any] = {"type": "Configure"}
        if keyterms is not None:
            msg["keyterms"] = keyterms
        if thresholds:
            msg["thresholds"] = thresholds
        if self._ws is not None and len(msg) > 1:
            await self._ws.send(json.dumps(msg))

    async def events(self) -> AsyncIterator[TurnEvent | Problem]:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                ev = parse_flux(raw)
                if ev is not None:
                    yield ev
        except Exception as exc:  # noqa: BLE001 - a dropped socket ends the stream; the session decides what to do
            yield Problem("Error", type(exc).__name__, "the speech connection closed")

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:  # noqa: BLE001
                pass
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass


def split_for_speech(text: str, limit: int = MAX_SPEAK_CHARS) -> list[str]:
    """Break text at sentence ends so that no Speak message goes over Deepgram's character limit."""
    text = " ".join(text.split())
    parts, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if cur and len(cur) + 1 + len(sentence) > limit:
            parts.append(cur)
            cur = sentence
        else:
            cur = (cur + " " + sentence).strip()
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


class AuraStream:
    """One Aura-2 connection. text in (Speak + Flush), audio out (raw linear16 at TTS_RATE)."""

    def __init__(self, api_key: str, *, voice: str = "aura-2-thalia-en", sample_rate: int = TTS_RATE, connect: Connect = default_connect) -> None:
        self._key, self._connect = api_key, connect
        self.url = AURA_URL + "?" + urlencode({"model": voice, "encoding": "linear16", "sample_rate": sample_rate, "mip_opt_out": "true"})
        self._ws: Any = None

    @property
    def is_open(self) -> bool:
        return self._ws is not None

    async def open(self) -> None:
        self._ws = await self._connect(self.url, _headers(self._key))

    async def speak(self, text: str) -> None:
        """Queue the text and ask for the audio now. The audio arrives through audio(); Flushed marks the end."""
        if self._ws is None:
            return
        for part in split_for_speech(text):
            await self._ws.send(json.dumps({"type": "Speak", "text": part}))
        await self._ws.send(json.dumps({"type": "Flush"}))

    async def clear(self) -> None:
        """Barge-in: drop whatever has not been generated yet."""
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "Clear"}))

    async def audio(self) -> AsyncIterator[tuple[str, Any]]:
        """Yields ("audio", bytes), ("flushed", None), ("cleared", None) or ("problem", Problem)."""
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    yield "audio", bytes(raw)
                    continue
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                t = m.get("type")
                if t == "Flushed":
                    yield "flushed", None
                elif t == "Cleared":
                    yield "cleared", None
                elif t == "Warning":
                    yield "problem", Problem("Warning", str(m.get("code") or ""), str(m.get("description") or "")[:200])
        except Exception as exc:  # noqa: BLE001
            yield "problem", Problem("Error", type(exc).__name__, "the speech connection closed")

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "Close"}))
            except Exception:  # noqa: BLE001
                pass
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
