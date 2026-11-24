"""Voice (Deepgram Flux + Aura-2 over raw websockets): settings, keyterms, the two clients, and the session logic with fake sockets."""
import asyncio
import json

import pytest

from backend.voice import keyterms
from backend.voice import settings as vs
from backend.voice.deepgram import (AuraStream, FluxStream, Problem, TurnEvent, flux_url, parse_flux, split_for_speech)
from backend.voice.session import NUDGE_LINE, VoiceSession


# ---------------------------------------------------------------- settings
def test_voice_settings_defaults_match_the_design():
    s = vs.VoiceSettings()
    assert (s.eot_threshold, s.eager_eot_threshold, s.eot_timeout_ms, s.barge_in, s.no_answer_s, s.answer_cap_s) == (0.7, None, 6000, False, 20, 180)


@pytest.mark.parametrize("bad", [
    {"eot_threshold": 0.4}, {"eot_threshold": 1.1}, {"eot_timeout_ms": 100}, {"eot_timeout_ms": 70000}, {"eot_timeout_ms": 5000.5},
    {"eager_eot_threshold": 0.2}, {"eager_eot_threshold": 0.95}, {"eot_threshold": 0.6, "eager_eot_threshold": 0.8},
    {"barge_in": "yes"}, {"no_answer_s": 1}, {"answer_cap_s": 5}, {"voice": "aura-2-nobody-en"}, {"surprise": 1}, {"eot_threshold": True}])
def test_voice_settings_reject_values_outside_the_deepgram_ranges(bad):
    with pytest.raises(vs.VoiceSettingsError):
        vs.parse(bad)


def test_voice_settings_accept_the_edges_and_round_trip_through_the_store(tmp_path):
    from backend.store import Store
    s = vs.parse({"eot_threshold": 0.5, "eager_eot_threshold": 0.3, "eot_timeout_ms": 500, "barge_in": True})
    assert s.barge_in and s.eager_eot_threshold == 0.3
    store = Store(str(tmp_path / "s.db"))
    assert vs.load(store) == vs.VoiceSettings()
    vs.save(store, {"eot_threshold": 0.9, "barge_in": True})
    assert vs.load(store).eot_threshold == 0.9 and vs.load(store).barge_in
    store.set_setting("voice", "{not json")
    assert vs.load(store) == vs.VoiceSettings()          # a bad stored value never stops an interview


# ---------------------------------------------------------------- keyterms
def test_keyterms_come_from_the_bank_and_follow_the_flux_rules():
    terms = keyterms.build([{"question": "Explain ACID and the HashMap in C++ vs PostgreSQL", "key_points": [{"point": "TCP uses a B+ tree?"}],
                             "topic": "Databases", "type": "concept"}])
    assert {"ACID", "HashMap", "C++", "PostgreSQL", "TCP"} <= set(terms) and "Databases" not in terms
    assert all(len(t) <= keyterms.MAX_TERM_CHARS and not any(c in t for c in ",;\n") for t in terms)
    assert "IT" not in keyterms.terms_in("IT is OK and I am A person")
    assert keyterms.terms_in("SELECT ALL rows WHERE x IS NULL ORDER BY y; GROUP and JOIN") == ["SELECT", "JOIN"]   # ordinary words in capitals are not boosted


def test_keyterms_current_question_first_deduplicated_and_capped():
    bank = [{"question": f"Define TERM{i}X and ACID", "key_points": [], "topic": "", "type": "concept"} for i in range(300)]
    current = {"question": "What is MVCC?", "key_points": [], "topic": "", "type": "concept"}
    out = keyterms.build(bank, current)
    assert out[0] == "MVCC" and len(out) <= keyterms.MAX_TERMS and len({t.lower() for t in out}) == len(out)


def test_the_sample_question_bank_gives_keyterms():
    from backend.content import bank as qbank
    terms = keyterms.build(qbank.load())
    assert "SQL" in terms and len(terms) <= keyterms.MAX_TERMS


# ---------------------------------------------------------------- the Deepgram clients
class FakeSocket:
    """Stands in for a websocket: records what is sent, replays what the test queues."""
    def __init__(self):
        self.sent, self.closed, self._q = [], False, asyncio.Queue()

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True
        self._q.put_nowait(None)

    def push(self, item):
        self._q.put_nowait(item)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._q.get()
        if item is None:
            raise StopAsyncIteration
        return item


def fake_connect(sock):
    calls = []

    async def connect(url, headers):
        calls.append((url, headers))
        return sock
    connect.calls = calls
    return connect


def test_flux_url_and_auth_header():
    url = flux_url(eot_threshold=0.8, eager_eot_threshold=None, eot_timeout_ms=7000)
    assert url.startswith("wss://api.deepgram.com/v2/listen?") and "model=flux-general-en" in url and "encoding=linear16" in url
    assert "sample_rate=16000" in url and "eot_threshold=0.8" in url and "eot_timeout_ms=7000" in url and "mip_opt_out=true" in url
    assert "eager_eot_threshold" not in url
    assert "eager_eot_threshold=0.5" in flux_url(eot_threshold=0.8, eager_eot_threshold=0.5, eot_timeout_ms=7000)


def test_flux_stream_sends_audio_control_messages_and_parses_turn_events():
    async def go():
        sock = FakeSocket()
        connect = fake_connect(sock)
        flux = FluxStream("KEY123", connect=connect)
        await flux.open()
        assert connect.calls[0][1] == {"Authorization": "Token KEY123"}
        await flux.send_audio(b"\x00\x01" * 10)
        await flux.configure(keyterms=["ACID", "B+ tree"])
        await flux.force_end_turn()
        sock.push(json.dumps({"type": "Connected", "request_id": "r", "sequence_id": 0}))
        sock.push(json.dumps({"type": "TurnInfo", "event": "StartOfTurn", "turn_index": 0, "transcript": "hi", "end_of_turn_confidence": "0.1"}))
        sock.push(json.dumps({"type": "TurnInfo", "event": "EndOfTurn", "turn_index": 0, "transcript": "hi there", "end_of_turn_confidence": 0.9,
                              "trigger": "manual"}))
        sock.push(json.dumps({"type": "Warning", "code": "SLOW", "description": "x"}))
        sock.push(json.dumps({"type": "Error", "code": "INTERNAL_SERVER_ERROR", "description": "boom"}))
        got = []
        async for ev in flux.events():
            got.append(ev)
            if len(got) == 4:
                break
        await flux.close()
        return sock, got
    sock, got = asyncio.run(go())
    assert sock.sent[0] == b"\x00\x01" * 10
    assert json.loads(sock.sent[1]) == {"type": "Configure", "keyterms": ["ACID", "B+ tree"]}
    assert json.loads(sock.sent[2]) == {"type": "ForceEndTurn"}
    assert got[0] == TurnEvent("StartOfTurn", "hi", 0, 0.1, None) and got[1].event == "EndOfTurn" and got[1].trigger == "manual"
    assert got[2] == Problem("Warning", "SLOW", "x") and got[3].kind == "Error"
    assert json.loads(sock.sent[-1]) == {"type": "CloseStream"} and sock.closed


def test_parse_flux_ignores_noise():
    assert parse_flux(b"binary") is None and parse_flux("not json") is None and parse_flux(json.dumps({"type": "Connected"})) is None
    assert parse_flux(json.dumps({"type": "ConfigureSuccess"})) is None


def test_missing_key_is_a_clear_error_not_a_silent_failure():
    from backend.voice.deepgram import DeepgramError
    with pytest.raises(DeepgramError):
        asyncio.run(FluxStream("").open())


def test_aura_stream_speaks_flushes_and_reads_audio():
    async def go():
        sock = FakeSocket()
        aura = AuraStream("KEY", voice="aura-2-thalia-en", connect=fake_connect(sock))
        await aura.open()
        await aura.speak("Hello there. How are you?")
        await aura.clear()
        sock.push(b"\x01\x02")
        sock.push(json.dumps({"type": "Metadata", "request_id": "r"}))
        sock.push(json.dumps({"type": "Flushed", "sequence_id": 1}))
        sock.push(json.dumps({"type": "Cleared", "sequence_id": 2}))
        sock.push(json.dumps({"type": "Warning", "code": "W", "description": "d"}))
        got = []
        async for item in aura.audio():
            got.append(item)
            if len(got) == 4:
                break
        await aura.close()
        return aura, sock, got
    aura, sock, got = asyncio.run(go())
    assert "model=aura-2-thalia-en" in aura.url and "encoding=linear16" in aura.url and "sample_rate=24000" in aura.url
    assert [json.loads(x)["type"] for x in sock.sent[:3]] == ["Speak", "Flush", "Clear"]
    assert json.loads(sock.sent[0])["text"] == "Hello there. How are you?"
    assert got == [("audio", b"\x01\x02"), ("flushed", None), ("cleared", None), ("problem", Problem("Warning", "W", "d"))]


def test_long_text_is_split_under_the_speak_limit_at_sentence_ends():
    assert split_for_speech("One. Two! Three?") == ["One. Two! Three?"]
    long = " ".join(["This is a sentence of moderate length."] * 200)
    parts = split_for_speech(long)
    assert all(len(p) <= 2000 for p in parts) and " ".join(parts) == long and len(parts) >= 4
    assert all(len(p) <= 60 for p in split_for_speech("a" * 150 + " " + "b" * 150, 60))


# ---------------------------------------------------------------- the session, with fake Deepgram sockets and a fake interview
class FakeOps:
    """The interview as the voice session sees it: a transcript, a status and an optional open task."""
    def __init__(self, opening="Welcome. Tell me about yourself."):
        self.messages = [{"role": "interviewer", "text": opening}]
        self.status, self.task = "active", None
        self.said, self.silent = [], 0
        self.replies = []                      # (text, task?, finished?) consumed by message()

    def view(self, token):
        return {"status": self.status, "state": "intro", "remaining_s": 2000, "messages": list(self.messages), "task": self.task}

    def message(self, token, text):
        self.said.append(text)
        self.messages.append({"role": "candidate", "text": text})
        reply, task, finished = self.replies.pop(0) if self.replies else ("Thanks. Next question please.", False, False)
        self.messages.append({"role": "interviewer", "text": reply})
        self.task = {"kind": "coding"} if task else None
        if finished:
            self.status = "finished"
        return {}

    def silence(self, token):
        self.silent += 1
        self.messages.append({"role": "interviewer", "text": "Take a moment, or ask me to repeat the question."})
        return {}


class FakeLink:
    def __init__(self):
        self.inbox, self.bytes_out, self.json_out = asyncio.Queue(), [], []

    async def receive(self):
        return await self.inbox.get()

    async def send_bytes(self, data):
        self.bytes_out.append(data)

    async def send_json(self, data):
        self.json_out.append(data)

    def kinds(self):
        return [m["type"] for m in self.json_out]

    def audio(self, chunk=b"\x05\x00" * 4):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": chunk})

    def control(self, kind):
        self.inbox.put_nowait({"type": "websocket.receive", "text": json.dumps({"type": kind})})

    def hang_up(self):
        self.inbox.put_nowait({"type": "websocket.disconnect"})


class FakeFlux:
    def __init__(self, fail_open=False):
        self.q, self.audio, self.configured, self.forced, self.closed, self.fail_open, self.opened = asyncio.Queue(), [], [], 0, False, fail_open, False

    async def open(self):
        if self.fail_open:
            raise OSError("refused")
        self.opened = True

    async def send_audio(self, b):
        self.audio.append(b)

    async def configure(self, *, keyterms=None, thresholds=None):
        self.configured.append(keyterms)

    async def force_end_turn(self):
        self.forced += 1

    async def events(self):
        while True:
            ev = await self.q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed = True
        self.q.put_nowait(None)

    def turn(self, event, text="", trigger=None):
        self.q.put_nowait(TurnEvent(event, text, 0, 0.9, trigger))


class FakeAura:
    def __init__(self):
        self.q, self.spoken, self.cleared, self.closed = asyncio.Queue(), [], 0, False

    async def open(self):
        pass

    async def speak(self, text):
        self.spoken.append(text)
        self.q.put_nowait(("audio", b"AUDIO"))
        self.q.put_nowait(("flushed", None))

    async def clear(self):
        self.cleared += 1

    async def audio(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            yield item

    async def close(self):
        self.closed = True
        self.q.put_nowait(None)


class Rig:
    """A VoiceSession wired to fakes. Use it inside `async with`-free asyncio.run coroutines."""
    def __init__(self, ops=None, **cfg):
        self.link, self.ops = FakeLink(), ops or FakeOps()
        self.fluxes, self.auras = [], []
        base = dict(no_answer_s=0.15, answer_cap_s=0.3)
        self.cfg = vs.VoiceSettings(**{**base, **cfg})
        self.session = VoiceSession(self.link, "tok", self.ops, self.cfg, bank=[{"question": "What is ACID?", "key_points": [], "topic": "", "type": "concept"}],
                                    flux_factory=self._flux, aura_factory=self._aura)

    def _flux(self, _):
        f = FakeFlux()
        self.fluxes.append(f)
        return f

    def _aura(self, _):
        a = FakeAura()
        self.auras.append(a)
        return a

    @property
    def flux(self):
        return self.fluxes[-1]

    @property
    def aura(self):
        return self.auras[-1]

    async def start(self):
        self.task = asyncio.create_task(self.session.run())
        await self.tick()

    async def tick(self, n=6):
        for _ in range(n):
            await asyncio.sleep(0.01)

    async def playback_done(self):
        self.link.control("playback_ended")
        await self.tick()

    async def finish(self):
        self.link.hang_up()
        await asyncio.wait_for(self.task, 2)


def run(coro):
    asyncio.run(asyncio.wait_for(coro, 10))


def test_on_connect_the_waiting_question_is_spoken_and_keyterms_are_sent():
    async def go():
        r = Rig()
        await r.start()
        assert r.aura.spoken == ["Welcome. Tell me about yourself."] and "tts_start" in r.link.kinds() and "tts_end" in r.link.kinds()
        assert r.link.bytes_out == [b"AUDIO"]
        assert r.flux.configured and "ACID" in r.flux.configured[0]
        cfg = r.link.json_out[0]
        assert cfg["type"] == "config" and cfg["barge_in"] is False and cfg["stt_rate"] == 16000 and cfg["tts_rate"] == 24000
        await r.finish()
        assert r.flux.closed and r.aura.closed
    run(go())


def test_an_end_of_turn_goes_to_the_engine_and_the_reply_is_spoken():
    async def go():
        r = Rig()
        r.ops.replies = [("Good. What about databases?", False, False)]
        await r.start()
        await r.playback_done()
        r.flux.turn("StartOfTurn", "I am a")
        r.flux.turn("EndOfTurn", "I am a final year student", trigger="model")
        await r.tick(10)
        assert r.ops.said == ["I am a final year student"]
        assert r.aura.spoken[-1] == "Good. What about databases?" and "thinking" in r.link.kinds()
        await r.finish()
    run(go())


def test_mic_audio_reaches_flux_but_is_silenced_while_we_speak_without_barge_in():
    async def go():
        r = Rig()
        await r.start()                    # greeting is playing: mode "speaking"
        r.link.audio(b"\x07\x00" * 5)
        await r.tick()
        assert r.flux.audio == [bytes(10)]
        await r.playback_done()
        r.link.audio(b"\x07\x00" * 5)
        await r.tick()
        assert r.flux.audio[-1] == b"\x07\x00" * 5
        await r.finish()
    run(go())


def test_with_barge_in_on_the_mic_is_never_silenced_and_speaking_stops_when_the_candidate_starts():
    async def go():
        r = Rig(barge_in=True)
        await r.start()
        r.link.audio(b"\x07\x00" * 5)
        await r.tick()
        assert r.flux.audio == [b"\x07\x00" * 5]
        r.flux.turn("StartOfTurn", "wait")
        await r.tick()
        assert r.aura.cleared == 1 and "stop_playback" in r.link.kinds()
        await r.finish()
    run(go())


def test_with_barge_in_off_a_start_of_turn_while_speaking_is_ignored():
    async def go():
        r = Rig()
        await r.start()
        r.flux.turn("StartOfTurn", "echo")
        await r.tick()
        assert r.aura.cleared == 0 and "stop_playback" not in r.link.kinds()
        await r.finish()
    run(go())


def test_no_answer_timer_starts_when_playback_ends_and_a_start_of_turn_cancels_it():
    async def go():
        r = Rig()
        await r.start()
        await asyncio.sleep(0.3)
        assert r.ops.silent == 0                   # not running while the question is still playing
        await r.playback_done()
        await asyncio.sleep(0.35)
        assert r.ops.silent == 1 and r.aura.spoken[-1].startswith("Take a moment")
        await r.playback_done()
        r.flux.turn("StartOfTurn", "um")
        await asyncio.sleep(0.35)
        assert r.ops.silent == 1                   # the candidate started talking: no second nudge
        await r.finish()
    run(go())


def test_a_mid_answer_pause_gets_one_nudge_and_the_two_parts_are_joined():
    async def go():
        r = Rig()
        await r.start()
        await r.playback_done()
        r.flux.turn("StartOfTurn", "a stack is")
        r.flux.turn("EndOfTurn", "a stack is last in", trigger="timeout")
        await r.tick(10)
        assert r.ops.said == [] and r.aura.spoken[-1] == NUDGE_LINE
        await r.playback_done()
        r.flux.turn("StartOfTurn", "first out")
        r.flux.turn("EndOfTurn", "first out", trigger="model")
        await r.tick(10)
        assert r.ops.said == ["a stack is last in first out"]
        await r.finish()
    run(go())


def test_the_nudge_is_used_once_per_answer_and_a_silent_candidate_after_it_still_gets_answered():
    async def go():
        r = Rig()
        await r.start()
        await r.playback_done()
        r.flux.turn("EndOfTurn", "half an answer", trigger="timeout")
        await r.tick(10)
        await r.playback_done()                    # the nudge finished; the candidate says nothing more
        await asyncio.sleep(0.35)
        assert r.ops.said == ["half an answer"]    # the no-answer timer submits what they said
        r.flux.turn("EndOfTurn", "another", trigger="timeout")   # next answer: a fresh nudge is allowed
        await r.tick(10)
        assert r.aura.spoken.count(NUDGE_LINE) >= 1
        await r.finish()
    run(go())


def test_a_three_minute_style_answer_cap_sends_force_end_turn():
    async def go():
        r = Rig()
        await r.start()
        await r.playback_done()
        r.flux.turn("StartOfTurn", "talking")
        await asyncio.sleep(0.5)
        assert r.flux.forced == 1
        await r.finish()
    run(go())


def test_im_done_forces_the_end_of_the_turn_or_submits_what_was_said():
    async def go():
        r = Rig()
        await r.start()
        await r.playback_done()
        r.flux.turn("StartOfTurn", "my answer")
        await r.tick()
        r.link.control("done")
        await r.tick()
        assert r.flux.forced == 1
        await r.finish()
        r2 = Rig()
        await r2.start()
        await r2.playback_done()
        r2.flux.turn("EndOfTurn", "partial", trigger="timeout")
        await r2.tick(10)
        await r2.playback_done()
        r2.link.control("done")
        await r2.tick(10)
        assert r2.ops.said == ["partial"]
        await r2.finish()
    run(go())


def test_starting_a_coding_task_closes_both_sockets_and_resume_reopens_them():
    async def go():
        r = Rig()
        r.ops.replies = [("Here is your coding problem. Take your time.", True, False)]
        await r.start()
        await r.playback_done()
        r.flux.turn("EndOfTurn", "ready", trigger="model")
        await r.tick(10)
        assert not r.flux.closed                        # still reading the hand-over aloud
        await r.playback_done()
        assert r.flux.closed and r.aura.closed and "paused" in r.link.kinds()
        r.link.audio()                                  # mic frames while paused go nowhere
        await r.tick()
        assert r.flux.audio == []
        r.ops.task = None                               # the candidate submitted
        r.ops.messages.append({"role": "interviewer", "text": "Welcome back. Walk me through the complexity."})
        r.link.control("resume")
        await r.tick(10)
        assert len(r.fluxes) == 2 and len(r.auras) == 2 and r.aura.spoken == ["Welcome back. Walk me through the complexity."]
        await r.finish()
    run(go())


def test_resume_while_the_task_is_still_open_does_nothing():
    async def go():
        r = Rig()
        r.ops.task = {"kind": "sql"}
        await r.start()
        assert r.session.mode == "paused" and r.fluxes == []
        r.link.control("resume")
        await r.tick()
        assert r.fluxes == []
        await r.finish()
    run(go())


def test_the_farewell_ends_the_session_after_it_is_spoken():
    async def go():
        r = Rig()
        r.ops.replies = [("Thank you, that is all. Goodbye.", False, True)]
        await r.start()
        await r.playback_done()
        r.flux.turn("EndOfTurn", "no questions", trigger="model")
        await r.tick(10)
        assert "ended" not in r.link.kinds()
        await r.playback_done()
        await asyncio.wait_for(r.task, 2)
        assert r.link.kinds()[-1] == "ended" and r.flux.closed
    run(go())


def test_a_finished_interview_just_says_ended():
    async def go():
        r = Rig()
        r.ops.status = "finished"
        await r.start()
        await asyncio.wait_for(r.task, 2)
        assert r.link.kinds() == ["config", "ended"] and r.fluxes == []
    run(go())


def test_if_deepgram_cannot_be_reached_the_page_is_told_to_fall_back_to_typing():
    async def go():
        r = Rig()
        r.session._flux_factory = lambda s: FakeFlux(fail_open=True)
        await r.start()
        await asyncio.wait_for(r.task, 2)
        assert "fallback" in r.link.kinds()
    run(go())


def test_a_fatal_flux_error_falls_back_to_typing():
    async def go():
        r = Rig()
        await r.start()
        r.flux.q.put_nowait(Problem("Error", "INTERNAL_SERVER_ERROR", "boom"))
        await r.tick(10)
        assert "fallback" in r.link.kinds()
        await asyncio.wait_for(r.task, 2)
    run(go())


def test_an_engine_error_is_reported_and_the_candidate_can_try_again():
    async def go():
        r = Rig(no_answer_s=30)      # long, so the no-answer timer cannot fire during the assertions

        def boom(token, text):
            raise RuntimeError("model down")
        r.ops.message = boom
        await r.start()
        await r.playback_done()
        r.flux.turn("EndOfTurn", "my answer", trigger="model")
        await r.tick(10)
        assert "error" in r.link.kinds() and r.session.mode == "listening"
        await r.finish()
    run(go())


def test_the_deepgram_key_never_reaches_the_browser():
    async def go():
        r = Rig()
        await r.start()
        r.flux.turn("EndOfTurn", "hello", trigger="model")
        await r.tick(10)
        blob = json.dumps(r.link.json_out) + repr(r.link.bytes_out)
        assert "Token" not in blob and "DEEPGRAM" not in blob.upper()
        await r.finish()
    run(go())


# ---------------------------------------------------------------- the websocket route
def make_route_env(tmp_path, **kw):
    from test_api_tasks import Env
    box = {"flux": [], "aura": []}

    def flux(_):
        f = FakeFlux()
        box["flux"].append(f)
        return f

    def aura(_):
        a = FakeAura()
        box["aura"].append(a)
        return a
    env = Env(tmp_path, voice_flux=flux, voice_aura=aura, **kw)
    return env, box


def test_route_rejects_other_origins_unknown_tokens_and_interviews_that_are_not_active(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    env, _ = make_route_env(tmp_path)
    for url, headers in ((f"/api/session/{env.tok}/voice", {"origin": "https://evil.example"}), (f"/api/session/{env.tok}/voice", {}),
                         ("/api/session/nope/voice", {"origin": "http://localhost:3000"})):
        with pytest.raises(WebSocketDisconnect):
            with env.cand.websocket_connect(url, headers=headers) as ws:
                ws.receive_json()
    (tmp_path / "c").mkdir()
    env3, _ = make_route_env(tmp_path / "c", start=False)                 # created but not started
    with pytest.raises(WebSocketDisconnect):
        with env3.cand.websocket_connect(f"/api/session/{env3.tok}/voice", headers={"origin": "http://localhost:3000"}) as ws:
            ws.receive_json()


def test_route_speaks_the_greeting_and_sends_the_answer_through_the_real_engine(tmp_path):
    env, box = make_route_env(tmp_path)
    with env.cand.websocket_connect(f"/api/session/{env.tok}/voice", headers={"origin": "http://localhost:3000"}) as ws:
        cfg = ws.receive_json()
        assert cfg["type"] == "config" and cfg["tts_rate"] == 24000
        assert ws.receive_json()["type"] == "tts_start"
        assert ws.receive_bytes() == b"AUDIO" and ws.receive_json()["type"] == "tts_end"
        ws.send_json({"type": "playback_ended"})
        box["flux"][0].turn("EndOfTurn", "Hello, I study computer science and like databases.", "model")
        got = []
        while "tts_end" not in got:
            m = ws.receive()
            if m.get("text"):
                got.append(json.loads(m["text"])["type"])
        assert "thinking" in got and box["aura"][0].spoken[-1]
        ws.send_json({"type": "stop"})
    transcript = [m["text"] for m in env.view()["messages"]]
    assert any("databases" in t for t in transcript)


def test_a_second_voice_connection_for_the_same_interview_is_refused(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    env, _ = make_route_env(tmp_path)
    hdr = {"origin": "http://localhost:3000"}
    with env.cand.websocket_connect(f"/api/session/{env.tok}/voice", headers=hdr) as first:
        first.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with env.cand.websocket_connect(f"/api/session/{env.tok}/voice", headers=hdr) as second:
                second.receive_json()
        first.send_json({"type": "stop"})


def test_without_a_deepgram_key_the_page_is_told_to_type(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.main import create_app
    from test_api_auth import settings
    from test_api_tasks import ORIGIN, PASSWORD
    from fake_llm import FakeLLM
    app = create_app(settings(tmp_path), transport=FakeLLM(), background_scoring=False)
    admin, cand = TestClient(app, base_url="http://localhost"), TestClient(app, base_url="http://localhost")
    admin.post("/api/auth/login", json={"password": PASSWORD})
    made = admin.post("/api/admin/interviews", json={"candidate_name": "Priya Rhea Testname"}, headers=ORIGIN).json()
    cand.post(f"/api/session/{made['token']}/start", headers=ORIGIN)
    with cand.websocket_connect(f"/api/session/{made['token']}/voice", headers={"origin": "http://localhost:3000"}) as ws:
        assert ws.receive_json()["type"] == "fallback"


# ---------------------------------------------------------------- settings over HTTP
def test_voice_settings_over_http_are_validated_and_leave_the_faq_alone(tmp_path):
    from test_api_tasks import ORIGIN, Env
    env = Env(tmp_path, start=False)
    put = lambda body: env.admin.put("/api/admin/settings", json=body, headers=ORIGIN)  # noqa: E731
    assert put({"company_faq": "We hire in June."}).status_code == 200
    r = put({"voice": {"eot_threshold": 0.85, "barge_in": True, "eot_timeout_ms": 8000}})
    assert r.status_code == 200 and r.json()["voice"]["eot_threshold"] == 0.85 and r.json()["voice"]["barge_in"] is True
    assert r.json()["company_faq"] == "We hire in June." and "aura-2-thalia-en" in r.json()["voices"]
    for bad in ({"eot_threshold": 0.2}, {"eot_timeout_ms": 10}, {"mystery": 1}, {"voice": "x"}):
        assert put({"voice": bad}).status_code == 422
    assert env.admin.get("/api/admin/settings").json()["voice"]["eot_threshold"] == 0.85
