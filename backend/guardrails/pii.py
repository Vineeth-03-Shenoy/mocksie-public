"""Rule-based PII scrubbing. No NLP model, no network: every rule here is a regex or an interviewer-typed term.

Two layers, applied to the resume AND to live transcripts before anything reaches OpenAI:

1. PATTERNS: email, phone (Indian formats incl. +91 and bare 10-digit), URLs and profile handles,
   Aadhaar, PAN, date of birth, PIN codes, roll numbers / USNs.
2. Redactor: the candidate's name (typed by the interviewer from the resume, required) plus an optional
   "Also hide" list (college, city, other names). Each term and each part of a term is redacted,
   case-insensitive and whole-word, including initials such as the "N." in "Lakshmi N.".

Typed tokens keep the text readable for the model: [NAME] [EMAIL] [PHONE] [LINK] [ID] [REDACTED].

Known limit (accepted for the pilot, see SECURITY.md): names the interviewer did NOT type (friends, mentors,
a manager in a project story) are not recognised and can still reach OpenAI.
"""
from __future__ import annotations

import re
from typing import Iterable

_SITES = (r"linkedin|github|gitlab|bitbucket|leetcode|hackerrank|hackerearth|codechef|codeforces|kaggle|medium|"
          r"twitter|instagram|facebook|behance|dribbble|stackoverflow|geeksforgeeks|naukri|internshala")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"

# kind -> typed token
TOKENS = {
    "EMAIL": "[EMAIL]", "PHONE": "[PHONE]", "URL": "[LINK]", "HANDLE": "[LINK]",
    "AADHAAR": "[ID]", "PAN": "[ID]", "ROLL_NO": "[ID]", "USN": "[ID]",
    "DOB": "[REDACTED]", "PIN": "[REDACTED]", "NAME": "[NAME]", "TERM": "[REDACTED]",
}

# Order matters: more specific patterns first so a 12-digit Aadhaar is not reported as a phone.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("URL", re.compile(
        rf"(?i)\b(?:https?://\S+|www\.\S+|(?:[a-z0-9-]+\.)*(?:{_SITES})\.(?:com|net|in|org)/\S*|(?:[a-z0-9-]+\.)?x\.com/\S+"
        r"|[a-z0-9-]+\.(?:github\.io|vercel\.app|netlify\.app|herokuapp\.com|web\.app|pages\.dev)\S*)")),
    # "LinkedIn: janedoe", "GitHub - janedoe", "Portfolio: janedoe.dev"
    ("HANDLE", re.compile(rf"(?i)\b(?:{_SITES}|portfolio|website)(?:[ \t]+(?:id|profile|username|handle|url|link))?[ \t]*[:\-][ \t]*[\w.\-/]+")),
    ("AADHAAR", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PAN", re.compile(r"(?i)\b[A-Z]{5}\d{4}[A-Z]\b")),
    # Indian mobiles: 10 digits starting 6-9, optional +91 / 91 / 0 prefix, spaces or dashes inside
    ("PHONE", re.compile(r"(?<![\w.])(?:\(?(?:\+|00)?91\)?[ .-]?|0)?[6-9]\d{4}[ .-]?\d{5}(?!\w)")),
    ("PHONE", re.compile(r"(?<![\w.])(?:\(?(?:\+|00)?91\)?[ .-]?|0)?[6-9]\d{2}[ .-]\d{3}[ .-]\d{4}(?!\w)")),
    ("PHONE", re.compile(r"(?<![\w.])0\d{2,4}[ -]\d{6,8}(?!\w)")),  # landline with STD code
    ("PHONE", re.compile(r"(?<![\w.])(?:\+\d{1,3}[ -]?)?(?:\(?\d[ -]?){9,11}\d(?!\w)")),
    ("DOB", re.compile(rf"(?i)\b\d{{1,2}}(?:st|nd|rd|th)?[ \t,-]+{_MONTH}[ \t,-]+(?:19|20)\d{{2}}\b")),
    ("DOB", re.compile(rf"(?i)\b{_MONTH}[ \t]+\d{{1,2}}(?:st|nd|rd|th)?,?[ \t]+(?:19|20)\d{{2}}\b")),
    ("DOB", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b")),
    ("DOB", re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}\b")),
    ("PIN", re.compile(r"(?i)\b(?:pin(?:[ -]?code)?|postal[ -]?code|zip(?:[ -]?code)?)\b[ \t]*[:\-#.]?[ \t]*(?P<v>[1-9]\d{2} ?\d{3})\b")),
    # "Roll No: 21CS045" -- the token must contain a digit, so "roll number suggests" / "Rollback" never match.
    ("ROLL_NO", re.compile(r"(?i)\broll[ \t]*(?:no\.?|number)[ \t]*[:\-]?[ \t]*(?=[A-Z0-9/-]*\d)[A-Z0-9/-]{4,}")),
    ("USN", re.compile(r"\b\d[A-Z]{2}\d{2}[A-Z]{2,3}\d{3}\b")),  # e.g. 1AB21CS045 (assumed college format)
    ("HANDLE", re.compile(r"(?<![\w@])@(?!(?:Override|Deprecated|FunctionalInterface|SuppressWarnings|SafeVarargs|Test)\b)[A-Za-z0-9_]{3,}")),
]

# Only on resumes: "Bengaluru - 560001", "Hyderabad, 500 081". A bare 6-digit number in speech ("500000 users") is left alone.
RESUME_ONLY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PIN", re.compile(r"(?<=[A-Za-z])[ \t]*[-–,][ \t]*(?P<v>[1-9]\d{2} ?\d{3})\b(?![ \t]*\d)")),
]

# Words that make no sense to redact on their own when they are only PART of an "Also hide" phrase.
STOP_PARTS = {"of", "the", "and", "for", "in", "at", "college", "university", "institute", "institution", "engineering",
              "technology", "technological", "school", "sciences", "science", "department", "dr", "mr", "mrs", "ms",
              "prof", "sri", "shri", "smt", "city", "state", "road", "street", "nagar", "colony"}

_WORD = r"[^\W\d_]+(?:['’-][^\W\d_]+)*"
_SENTINEL = re.compile("[]")


def _span(m: re.Match) -> tuple[int, int]:
    return m.span("v") if "v" in m.re.groupindex else m.span()


def _pattern_spans(text: str, resume: bool = False) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    pats = PATTERNS + (RESUME_ONLY_PATTERNS if resume else [])
    for kind, pat in pats:
        for m in pat.finditer(text):
            s, e = _span(m)
            if any(s < b and a < e for a, b, _ in spans):
                continue  # already claimed by a more specific pattern
            spans.append((s, e, kind))
    return spans


def find_pii(text: str, resume: bool = False) -> list[tuple[str, str]]:
    """Return (kind, matched_text) for every pattern hit (names are the Redactor's job)."""
    return [(k, text[s:e]) for s, e, k in sorted(_pattern_spans(text, resume))]


def _replace(text: str, spans: list[tuple[int, int, str]]) -> str:
    for s, e, kind in sorted(spans, reverse=True):
        text = text[:s] + TOKENS[kind] + text[e:]
    return text


def scrub(text: str, resume: bool = False) -> str:
    """Patterns only (no names). Use a Redactor for the full job."""
    return _replace(text, _pattern_spans(text, resume))


def _phrase_regex(words: list[str]) -> re.Pattern:
    parts = [re.escape(w) + (r"\.?" if len(w) == 1 else "") for w in words]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _split_words(value: str) -> list[str]:
    return re.findall(_WORD, value)


class Redactor:
    """Interviewer-typed identity terms + the pattern layer. One instance per interview."""

    def __init__(self, candidate_name: str, also_hide: str | Iterable[str] = "") -> None:
        name = " ".join((candidate_name or "").split())
        words = _split_words(name)
        if not [w for w in words if len(w) > 1]:
            raise ValueError("the candidate's name is required (type it from the resume)")
        self.name = name
        self.initials = {w[0].upper() for w in words}
        self.extra = [t for t in (self._split_terms(also_hide)) if len(t) > 1]
        # (compiled regex, kind, literal text for the tripwire list)
        self._terms: list[tuple[re.Pattern, str]] = []
        seen: set[str] = set()

        def add(ws: list[str], kind: str) -> None:
            if len(ws) == 1 and len(ws[0]) < 2:
                return  # a lone initial is only redacted next to a name (see _absorb_initials)
            key = " ".join(w.lower() for w in ws)
            if key not in seen:
                seen.add(key)
                self._terms.append((_phrase_regex(ws), kind))

        add(words, "NAME")
        for w in words:
            add([w], "NAME")
            if re.search(r"['’-]", w):  # Anne-Marie -> Anne, Marie
                for piece in re.split(r"['’-]", w):
                    add([piece], "NAME")
        for term in self.extra:
            toks = term.split()
            phrase = r"(?<!\w)" + r"\s+".join(re.escape(t) for t in toks) + r"(?!\w)"
            if phrase not in seen:  # the whole phrase, exactly as typed ("9th Cross", "Lakeview Tech")
                seen.add(phrase)
                self._terms.append((re.compile(phrase, re.IGNORECASE), "TERM"))
            ws = _split_words(term)
            if len(toks) > 1:  # each part too (other people's names), except generic words
                for w in ws:
                    if len(w) >= 3 and w.lower() not in STOP_PARTS:
                        add([w], "TERM")
            elif ws:
                add(ws, "TERM")
        self._terms.sort(key=lambda t: -len(t[0].pattern))  # longest phrase first

    @staticmethod
    def _split_terms(value: str | Iterable[str]) -> list[str]:
        if isinstance(value, str):
            value = re.split(r"[,;\n]", value)
        return [" ".join(v.split()) for v in value if v and v.strip()]

    # -- detection ----------------------------------------------------------
    def _term_spans(self, text: str, blocked: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for rx, kind in self._terms:
            for m in rx.finditer(text):
                s, e = m.span()
                if any(s < b and a < e for a, b, _ in blocked + spans):
                    continue
                spans.append((s, e, kind))
        return self._absorb_initials(text, spans, blocked)

    _RIGHT = re.compile(r"[ \t]+([A-Z])(?:\.(?!\w)|(?!\w))")
    _LEFT = re.compile(r"(?<!\w)([A-Z])\.?[ \t]+$")

    def _absorb_initials(self, text, spans, blocked):
        """'Lakshmi N.' / 'N. Lakshmi' / 'S. R. Testname': an uppercase initial of a name part, next to a name, goes too."""
        out = []
        taken = [(a, b) for a, b, _ in blocked]
        for s, e, kind in sorted(spans):
            if kind == "NAME":
                while True:
                    m = self._RIGHT.match(text, e)
                    if not m or m.group(1) not in self.initials or any(m.start(1) < b and a < m.end() for a, b in taken):
                        break
                    e = m.end()
                while True:
                    base = max(0, s - 8)
                    m = self._LEFT.search(text[base:s])
                    if not m or m.group(1) not in self.initials:
                        break
                    new_s = base + m.start(1)
                    if any(new_s < b and a < s for a, b in taken):
                        break
                    s = new_s
            out.append((s, e, kind))
            taken.append((s, e))
        return out

    def _all_spans(self, text: str, resume: bool) -> list[tuple[int, int, str]]:
        pats = _pattern_spans(text, resume)
        spans = pats + self._term_spans(text, pats)
        spans.sort()
        merged: list[tuple[int, int, str]] = []
        for s, e, k in spans:  # [NAME] [NAME] -> one token
            if merged and merged[-1][2] == k and k in ("NAME", "TERM") and not text[merged[-1][1]:s].strip(" \t"):
                merged[-1] = (merged[-1][0], max(e, merged[-1][1]), k)
            else:
                merged.append((s, e, k))
        return merged

    # -- public API ---------------------------------------------------------
    def redact(self, text: str, resume: bool = False) -> str:
        text = _SENTINEL.sub("", text)
        return _replace(text, self._all_spans(text, resume))

    def find(self, text: str, resume: bool = False) -> list[tuple[str, str]]:
        return [(k, text[s:e]) for s, e, k in self._all_spans(text, resume)]

    def find_terms(self, text: str) -> list[tuple[str, str]]:
        """Only the typed name / hidden terms (what the LLM tripwire checks)."""
        return [(k, text[s:e]) for s, e, k in self._term_spans(text, []) if k in ("NAME", "TERM")]
