"""Voice settings, editable by the interviewer in Settings and stored in the settings table (JSON under the key "voice").

Ranges come from the Deepgram Flux docs: eot_threshold 0.5 to 1.0, eager_eot_threshold 0.3 to 0.9 (and not above
eot_threshold), eot_timeout_ms 500 to 60000. Eager end-of-turn is OFF by default: it raises LLM calls by 50-70%.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

KEY = "voice"
VOICES = ("aura-2-thalia-en", "aura-2-andromeda-en", "aura-2-helena-en", "aura-2-apollo-en", "aura-2-arcas-en", "aura-2-orion-en")


@dataclass(frozen=True)
class VoiceSettings:
    eot_threshold: float = 0.7           # confidence needed to end the candidate's turn
    eager_eot_threshold: float | None = None   # None = off
    eot_timeout_ms: int = 6000           # silence that ends a turn anyway; with 6 s this is the "mid-answer pause" of design 4.11
    barge_in: bool = False               # off: on one laptop the speakers echo into the mic
    no_answer_s: int = 20                # silence after the question finished playing before a nudge / a miss
    answer_cap_s: int = 180              # longest single answer before the turn is ended for them
    voice: str = "aura-2-thalia-en"

    def to_dict(self) -> dict:
        return asdict(self)


class VoiceSettingsError(ValueError):
    pass


def parse(raw: dict) -> VoiceSettings:
    """Validate a dict (unknown keys are rejected); missing keys take the defaults."""
    base = VoiceSettings().to_dict()
    extra = set(raw) - set(base)
    if extra:
        raise VoiceSettingsError(f"unknown voice setting(s): {', '.join(sorted(extra))}")
    v = {**base, **raw}

    def number(name: str, lo: float, hi: float, integer: bool = False):
        x = v[name]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or (integer and int(x) != x):
            raise VoiceSettingsError(f"{name} must be a {'whole ' if integer else ''}number")
        if not lo <= x <= hi:
            raise VoiceSettingsError(f"{name} must be between {lo} and {hi}")
        return int(x) if integer else float(x)

    out = {"eot_threshold": number("eot_threshold", 0.5, 1.0), "eot_timeout_ms": number("eot_timeout_ms", 500, 60000, True),
           "no_answer_s": number("no_answer_s", 5, 120, True), "answer_cap_s": number("answer_cap_s", 30, 600, True)}
    if v["eager_eot_threshold"] is None:
        out["eager_eot_threshold"] = None
    else:
        out["eager_eot_threshold"] = number("eager_eot_threshold", 0.3, 0.9)
        if out["eager_eot_threshold"] > out["eot_threshold"]:
            raise VoiceSettingsError("eager_eot_threshold must not be above eot_threshold")
    if not isinstance(v["barge_in"], bool):
        raise VoiceSettingsError("barge_in must be true or false")
    out["barge_in"] = v["barge_in"]
    if v["voice"] not in VOICES:
        raise VoiceSettingsError("voice must be one of: " + ", ".join(VOICES))
    out["voice"] = v["voice"]
    return VoiceSettings(**out)


def load(store) -> VoiceSettings:
    try:
        return parse(json.loads(store.get_setting(KEY, "{}") or "{}"))
    except (ValueError, TypeError):   # a bad stored value must never stop an interview: fall back to the defaults
        return VoiceSettings()


def save(store, raw: dict) -> VoiceSettings:
    settings = parse(raw)
    store.set_setting(KEY, json.dumps(settings.to_dict()))
    return settings
