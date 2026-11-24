"""One voice session: the browser's websocket on one side, Deepgram Flux (listening) and Aura-2 (speaking) on the other.

Our engine stays in charge. Flux only reports when the candidate's turn has ended and what was said:

  mic audio -> Flux -> EndOfTurn(transcript) -> Engine.candidate_message (it redacts the text, calls gpt-5.4-mini, runs the
  output guard) -> Aura-2 -> audio frames -> browser

Design 4.11 on Flux:
  - no answer: a timer starts when the browser says the question finished playing, and is cancelled by StartOfTurn.
    First expiry = one gentle nudge from the engine, second = a miss.
  - mid-answer pause: Flux ends the turn by timeout (eot_timeout_ms, default 6 s). The first time in an answer we say a fixed
    "take your time" line and keep what was said; the next turn is joined to it before the engine sees the answer.
  - answer cap: 3 minutes after the answer began we send ForceEndTurn.  "I'm done" from the browser does the same.
  - barge-in (StartOfTurn while we speak) is a Settings toggle, OFF by default: speakers echo into the mic on a single laptop.
    While we speak with barge-in off, the mic audio is replaced by silence so Flux stays connected but hears nothing.
  - coding and SQL: both Deepgram sockets are closed while the task is open (nothing is billed) and reopened on `resume`.
Transcripts and spoken text are never logged here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Protocol

from backend.voice import keyterms
from backend.voice.deepgram import STT_RATE, TTS_RATE, AuraStream, FluxStream, Problem, TurnEvent
from backend.voice.settings import VoiceSettings

log = logging.getLogger("mocksie.voice")

NUDGE_LINE = "Take your time. I'm listening."
CLOSED_MESSAGE = "The voice connection was lost. You can keep going by typing."


class Link(Protocol):
    async def receive(self) -> dict: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def send_json(self, data: dict) -> None: ...


class Ops(Protocol):
    """What the voice session may ask of the interview: the same calls the typed chat uses."""
    def view(self, token: str) -> dict: ...
    def message(self, token: str, text: str) -> dict: ...
    def silence(self, token: str) -> dict: ...


class VoiceSession:
    def __init__(self, link: Link, token: str, ops: Ops, settings: VoiceSettings, *, api_key: str = "", bank: list[dict] | None = None,
                 flux_factory: Callable[[VoiceSettings], Any] | None = None, aura_factory: Callable[[VoiceSettings], Any] | None = None) -> None:
        self.link, self.token, self.ops, self.cfg = link, token, ops, settings
        self.bank = bank or []
        self._flux_factory = flux_factory or (lambda s: FluxStream(api_key, eot_threshold=s.eot_threshold, eager_eot_threshold=s.eager_eot_threshold,
                                                                  eot_timeout_ms=s.eot_timeout_ms))
        self._aura_factory = aura_factory or (lambda s: AuraStream(api_key, voice=s.voice))
        self.flux: Any = None
        self.aura: Any = None
        self.mode = "starting"           # starting | listening | thinking | speaking | paused | ended
        self.pending = ""                # what the candidate said so far in this answer (before the engine saw it)
        self.nudged = False              # the mid-answer pause nudge was already used for this answer
        self.in_turn = False
        self.spoken = 0                  # how many interviewer messages have been read out
        self.after_speech = "listen"     # what to do when the playback ends: listen | pause | end
        self.tts_flushed = False
        self._lock = asyncio.Lock()
        self._done = asyncio.Event()     # set when the session is over, so the receive loop stops waiting for the browser
        self._tasks: dict[str, asyncio.Task] = {}
        self._keyterms = keyterms.build(self.bank)
        self.events: list[str] = []      # kinds of what we sent the browser (handy in tests and for debugging; no text)

    # ------------------------------------------------------------------ plumbing
    async def _send(self, kind: str, **data: Any) -> None:
        self.events.append(kind)
        try:
            await self.link.send_json({"type": kind, **data})
        except Exception:  # noqa: BLE001 - the browser went away; run() ends on its own
            pass

    def _spawn(self, name: str, make: Callable[[], Awaitable]) -> None:
        self._cancel(name)
        self._tasks[name] = asyncio.create_task(self._guarded(name, make))

    async def _guarded(self, name: str, make: Callable[[], Awaitable]) -> None:
        try:
            await make()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("voice task %s failed", name)

    def _cancel(self, name: str) -> None:
        t = self._tasks.pop(name, None)
        if t and t is not asyncio.current_task():
            t.cancel()

    async def _call(self, fn: Callable, *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

    # ------------------------------------------------------------------ the main loop
    async def run(self) -> None:
        await self._send("config", barge_in=self.cfg.barge_in, stt_rate=STT_RATE, tts_rate=TTS_RATE, no_answer_s=self.cfg.no_answer_s)
        try:
            view = await self._call(self.ops.view, self.token)
            if view["status"] != "active":
                await self._send("ended")
                return
            msgs = [m for m in view["messages"] if m["role"] == "interviewer"]
            last_is_interviewer = bool(view["messages"]) and view["messages"][-1]["role"] == "interviewer"
            self.spoken = len(msgs) - 1 if last_is_interviewer else len(msgs)    # a reload re-reads the question that is waiting
            if view["task"]:
                self.mode = "paused"
                await self._send("paused", reason="task")
            else:
                await self._open_streams()
                await self._after_engine(view)
            stop = asyncio.create_task(self._done.wait())
            while self.mode != "ended":
                recv = asyncio.create_task(self.link.receive())
                await asyncio.wait({recv, stop}, return_when=asyncio.FIRST_COMPLETED)
                if not recv.done():
                    recv.cancel()
                    break
                msg = recv.result()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self._on_audio(msg["bytes"])
                elif msg.get("text"):
                    await self._on_control(msg["text"])
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        for name in list(self._tasks):
            self._cancel(name)
        await self._close_streams()
        self._end()

    def _end(self) -> None:
        self.mode = "ended"
        self._done.set()

    # ------------------------------------------------------------------ Deepgram sockets
    async def _open_streams(self) -> None:
        if self.flux is not None and self.aura is not None:
            return
        self.flux, self.aura = self._flux_factory(self.cfg), self._aura_factory(self.cfg)
        try:
            await self.flux.open()
            await self.aura.open()
        except Exception as exc:  # noqa: BLE001 - no key, no network, refused: the candidate carries on by typing
            log.warning("could not open the speech connections: %s", type(exc).__name__)
            await self._close_streams()
            await self._send("fallback", message=CLOSED_MESSAGE)
            self._end()
            return
        await self.flux.configure(keyterms=self._keyterms)
        flux = self.flux
        self._spawn("flux", lambda: self._read_flux(flux))
        aura = self.aura
        self._spawn("tts", lambda: self._read_tts(aura))
        self.mode = "listening"

    async def _close_streams(self) -> None:
        self._cancel("flux")
        self._cancel("tts")
        self._cancel("no_answer")
        self._cancel("cap")
        flux, aura, self.flux, self.aura = self.flux, self.aura, None, None
        for s in (flux, aura):
            if s is not None:
                await s.close()
        self.in_turn = False

    async def _read_flux(self, flux: Any) -> None:
        async for ev in flux.events():
            if flux is not self.flux:
                return
            if isinstance(ev, Problem):
                log.warning("flux %s %s", ev.kind, ev.code)
                if ev.kind == "Error":
                    await self._lost()
                    return
                continue
            await self._on_turn_event(ev)

    async def _read_tts(self, aura: Any) -> None:
        async for kind, payload in aura.audio():
            if aura is not self.aura:
                return
            if kind == "audio":
                if self.mode == "speaking":
                    await self.link.send_bytes(payload)
            elif kind == "flushed":
                self.tts_flushed = True
                await self._send("tts_end")
            elif kind == "problem" and payload.kind == "Error":
                await self._lost()
                return

    async def _lost(self) -> None:
        if self.mode in ("ended", "paused"):
            return
        await self._close_streams()
        await self._send("fallback", message=CLOSED_MESSAGE)
        self._end()

    # ------------------------------------------------------------------ browser -> us
    async def _on_audio(self, chunk: bytes) -> None:
        if self.flux is None or self.mode in ("paused", "ended", "starting"):
            return
        if self.mode == "speaking" and not self.cfg.barge_in:
            chunk = bytes(len(chunk))        # silence, so the echo of our own voice is never heard
        await self.flux.send_audio(chunk)

    async def _on_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "playback_ended":
            await self._on_playback_ended()
        elif kind == "done":
            await self._on_done()
        elif kind == "resume":
            await self._on_resume()
        elif kind == "stop":
            self._end()

    # ------------------------------------------------------------------ Flux -> turn logic
    async def _on_turn_event(self, ev: TurnEvent) -> None:
        if ev.event == "StartOfTurn":
            await self._on_start_of_turn()
        elif ev.event == "EndOfTurn":
            await self._on_end_of_turn(ev)
        # Update, EagerEndOfTurn and TurnResumed are not acted on: eager mode is off by default and we answer only at EndOfTurn.

    async def _on_start_of_turn(self) -> None:
        self.in_turn = True
        self._cancel("no_answer")
        if self.mode == "speaking":
            if not self.cfg.barge_in:
                return
            await self.stop_speaking()
        if "cap" not in self._tasks and not self.pending:
            self._spawn("cap", self._answer_cap)

    async def stop_speaking(self) -> None:
        """Barge-in: stop the playback, drop the audio that has not been generated yet, and listen."""
        if self.aura is not None:
            await self.aura.clear()
        await self._send("stop_playback")
        self.tts_flushed = True
        self.mode = "listening"
        self.after_speech = "listen"

    async def _on_end_of_turn(self, ev: TurnEvent) -> None:
        self.in_turn = False
        text = ev.transcript.strip()
        if not text or self.mode in ("paused", "ended"):
            return
        async with self._lock:
            self.pending = (self.pending + " " + text).strip()
            if ev.trigger == "timeout" and not self.nudged:   # a pause in the middle of an answer: one nudge, keep the words
                self.nudged = True
                await self._speak([NUDGE_LINE], after="listen")
                return
            await self._answer()

    async def _answer(self) -> None:
        text, self.pending, self.nudged = self.pending, "", False
        self._cancel("cap")
        self._cancel("no_answer")
        if not text:
            return
        self.mode = "thinking"
        await self._send("thinking")
        try:
            await self._call(self.ops.message, self.token, text)
        except Exception as exc:  # noqa: BLE001 - HTTPException from the engine routes, or a privacy stop
            await self._engine_error(exc)
            return
        await self._after_engine()

    async def _engine_error(self, exc: Exception) -> None:
        detail = getattr(exc, "detail", None) or "something went wrong; please try again"
        log.warning("engine step failed: %s", type(exc).__name__)
        await self._send("error", message=str(detail)[:200])
        self.mode = "listening"
        self._arm_no_answer()

    async def _after_engine(self, view: dict | None = None) -> None:
        """Read out whatever the interviewer has said that we have not spoken yet, then decide what comes next."""
        view = view or await self._call(self.ops.view, self.token)
        said = [m["text"] for m in view["messages"] if m["role"] == "interviewer"][self.spoken:]
        self.spoken += len(said)
        after = "end" if view["status"] != "active" else "pause" if view["task"] else "listen"
        if said:
            await self._speak(said, after=after)
        else:
            self.after_speech, self.mode = after, "thinking"
            await self._on_playback_ended()

    async def _speak(self, texts: list[str], *, after: str) -> None:
        if self.aura is None:
            await self._open_streams()
            if self.aura is None:
                return
        self.mode, self.after_speech, self.tts_flushed = "speaking", after, False
        await self._send("tts_start")
        await self.aura.speak(" ".join(texts))

    async def _on_playback_ended(self) -> None:
        """The browser finished playing what we sent (or there was nothing to play)."""
        if self.mode not in ("speaking", "thinking"):
            return
        if self.after_speech == "end":
            await self._send("ended")
            self._end()
        elif self.after_speech == "pause":
            await self._close_streams()
            self.mode = "paused"
            await self._send("paused", reason="task")
        else:
            self.mode = "listening"
            self._arm_no_answer()

    # ------------------------------------------------------------------ timers
    def _arm_no_answer(self) -> None:
        self._spawn("no_answer", self._no_answer)

    async def _no_answer(self) -> None:
        await asyncio.sleep(self.cfg.no_answer_s)
        if self.in_turn or self.mode != "listening":
            return
        async with self._lock:
            if self.pending:                     # they said something, paused, and never carried on: that is the answer
                await self._answer()
                return
            self.mode = "thinking"
            await self._send("thinking")
            try:
                await self._call(self.ops.silence, self.token)
            except Exception as exc:  # noqa: BLE001
                await self._engine_error(exc)
                return
            await self._after_engine()

    async def _answer_cap(self) -> None:
        await asyncio.sleep(self.cfg.answer_cap_s)
        self._tasks.pop("cap", None)
        if self.in_turn and self.flux is not None:
            await self.flux.force_end_turn()
        elif self.pending:
            async with self._lock:
                await self._answer()

    async def _on_done(self) -> None:
        """"I'm done": end the turn now."""
        if self.in_turn and self.flux is not None:
            await self.flux.force_end_turn()
        elif self.pending and self.mode == "listening":
            async with self._lock:
                await self._answer()

    async def _on_resume(self) -> None:
        """The coding / SQL panel closed: reopen the speech connections and read what the interviewer said next."""
        if self.mode != "paused":
            return
        view = await self._call(self.ops.view, self.token)
        if view["task"]:
            return
        if view["status"] != "active":
            await self._after_engine(view)
            return
        self.mode = "starting"
        await self._open_streams()
        if self.mode != "ended":
            await self._after_engine(view)
