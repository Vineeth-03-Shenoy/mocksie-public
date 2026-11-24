"""Resume -> a small structured profile. Schema-only: the model can only fill the fields below, with length caps.

Pipeline (nothing raw ever leaves this process):
  PDF bytes -> pdf_sanitize (hidden text removed, page limit) -> resume_sections.drop_sections
  -> Redactor (typed name, "Also hide", patterns) -> injection.neutralize -> wrapped as untrusted data
  -> gpt-5.4-nano (strict JSON schema) -> output checked again (PII, injection, caps) -> CandidateProfile.
The raw text is deliberately NOT registered with PIIGuard.register_raw: the redacted text legitimately shares long
runs with it. The Redactor tripwire (typed names/terms) and the pattern tripwire in LLMClient guard the call instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.guardrails import injection
from backend.guardrails.pdf_sanitize import extract_visible_text
from backend.guardrails.pii import Redactor, find_pii
from backend.guardrails.resume_sections import drop_sections
from backend.llm.client import LLMClient

MAX_SKILLS, MAX_PROJECTS, MAX_ITEMS = 25, 5, 8
S = lambda n: {"type": "string", "maxLength": n}  # noqa: E731
STRING_LIST = lambda n, k: {"type": "array", "maxItems": k, "items": S(n)}  # noqa: E731

PROFILE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["degree", "branch", "expected_graduation_year", "skills", "programming_languages", "projects",
                 "internships", "certifications", "mentions_sql", "topics"],
    "properties": {
        "degree": {"type": ["string", "null"], "maxLength": 40},
        "branch": {"type": ["string", "null"], "maxLength": 60},
        "expected_graduation_year": {"type": ["integer", "null"], "minimum": 2000, "maximum": 2100},
        "skills": STRING_LIST(40, MAX_SKILLS),
        "programming_languages": STRING_LIST(30, 12),
        "projects": {"type": "array", "maxItems": MAX_PROJECTS, "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "summary", "technologies"],
            "properties": {"title": S(80), "summary": S(240), "technologies": STRING_LIST(30, 10)}}},
        "internships": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False, "required": ["role", "summary"],
            "properties": {"role": S(60), "summary": S(240)}}},
        "certifications": STRING_LIST(80, MAX_ITEMS),
        "mentions_sql": {"type": "boolean"},
        "topics": STRING_LIST(40, 10),
    },
}

SYSTEM = (
    "You extract a structured profile from a student's resume for interview preparation.\n"
    + injection.UNTRUSTED_NOTICE + "\n"
    "Fill ONLY the fields of the JSON schema, using only what the resume text says. Use null or an empty list when something "
    "is absent; never guess. Placeholders like [NAME] or [LINK] are redactions: do not try to recover what they hid. "
    "Never include names of people, contact details, links, ID numbers or addresses in any field. "
    "Summaries are neutral, factual and under the length caps. Do not evaluate or rate the candidate."
)


@dataclass
class CandidateProfile:
    data: dict
    warnings: list[str] = field(default_factory=list)  # shown to the interviewer in the console
    dropped_sections: list[str] = field(default_factory=list)
    hidden_text: dict[str, int] = field(default_factory=dict)
    injection_rules: list[str] = field(default_factory=list)
    redacted_text: str = ""  # what was sent to the model; kept for the resume-quality score

    @property
    def mentions_sql(self) -> bool:
        return bool(self.data.get("mentions_sql"))


def _walk(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _clean(obj: Any, redactor: Redactor, warnings: list[str]) -> Any:
    """Output check: a string with contact details or injection phrasing is blanked, not trusted. Typed names are not
    checked here: the model only ever saw redacted text, and the send-side tripwire already guarantees that."""
    if isinstance(obj, str):
        if find_pii(obj) or injection.detect(obj):
            warnings.append("a field in the model's profile was removed (contained personal details or instruction-like text)")
            return ""
        return obj.strip()
    if isinstance(obj, dict):
        return {k: _clean(v, redactor, warnings) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [_clean(v, redactor, warnings) for v in obj]
        return [v for v in out if v not in ("", None)]
    return obj


def prepare_resume_text(raw_text: str, redactor: Redactor) -> tuple[str, list[str], list[str]]:
    """Raw text -> (text safe to send, dropped sections, injection rules removed)."""
    kept, dropped = drop_sections(raw_text)
    redacted = redactor.redact(kept, resume=True)
    clean, rules = injection.neutralize(redacted)
    return clean, dropped, rules


def extract_profile(pdf_bytes: bytes, redactor: Redactor, client: LLMClient, max_pages: int = 2, max_bytes: int = 2_000_000) -> CandidateProfile:
    ex = extract_visible_text(pdf_bytes, max_pages=max_pages, max_bytes=max_bytes)
    warnings: list[str] = []
    if ex.hidden_chars:
        warnings.append(f"hidden text removed from the PDF ({ex.hidden_chars} characters: {', '.join(sorted(ex.reasons))})")
    if ex.truncated:
        warnings.append("the resume text was very long and was cut")
    text, dropped, rules = prepare_resume_text(ex.text, redactor)
    if rules:
        warnings.append("instruction-like text was found in the resume and removed: " + ", ".join(rules))
    if dropped:
        warnings.append("sections not sent: " + ", ".join(dropped))
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": injection.wrap_untrusted("resume", text)}]
    data, _resp = client.complete_json("fast", messages, PROFILE_SCHEMA, name="candidate_profile")
    data = _clean(data, redactor, warnings)
    return CandidateProfile(data=data, warnings=warnings, dropped_sections=dropped, hidden_text=ex.reasons, injection_rules=rules, redacted_text=text)
