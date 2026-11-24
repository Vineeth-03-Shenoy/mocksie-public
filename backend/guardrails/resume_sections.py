"""Drop whole resume sections that carry identity or family details, before any redaction or LLM call.

Dropped entirely when found: References / Referees, Address, Declaration, Personal Details / Information,
Father's / Mother's / Guardian's name, Date of Birth.

How a section ends (rule-based, no model):
- "Heading" line on its own (e.g. "PERSONAL DETAILS", "Declaration:"): dropped until the next section heading.
- "Label: value" line (e.g. "Address: 12 MG Road ...", "Father's Name: X", "DOB: 12/03/2004"): that line plus any
  continuation lines, up to a blank line, the next "Label:" line, or the next section heading.
- A section heading is a known resume heading (Education, Skills, Projects, ...), or any short ALL-CAPS line,
  or a short Title Case line ending in ":".
Bias: privacy over completeness. A resume section with an unusual heading that directly follows a dropped block
can be lost with it; the interviewer sees what was dropped in the console summary.
"""
from __future__ import annotations

import re

from backend.guardrails.pii import Redactor

_DROP = (r"references?|referees?|(?:permanent |current |present |residential |communication |correspondence |home |mailing )?address"
         r"|declaration|personal (?:details|information|info|profile|particulars|data)"
         r"|(?:father|mother|guardian)(?:['’]?s)?(?: name| details)?|parent(?:['’]?s)? name|date of birth|d\.?o\.?b\.?|birth ?date")
_DROP_LINE = re.compile(rf"(?i)^\s*(?P<h>{_DROP})\s*(?::|-|–|—)?\s*(?P<rest>.*?)\s*$")

_KNOWN_HEADINGS = (r"education|academics?|academic (?:details|profile|qualifications?|background)|skills?|technical skills?|"
                   r"key skills?|core competenc\w+|projects?|academic projects?|personal projects?|experience|work experience|"
                   r"professional experience|internships?|achievements?|accomplishments?|certifications?|courses?|coursework|"
                   r"training|objective|career objective|summary|professional summary|profile|about me|extra[- ]?curricular\w*|"
                   r"activities|hobbies|interests|languages?(?: known)?|awards?|honou?rs|publications?|positions? of responsibility|"
                   r"responsibilit\w+|strengths|volunteer\w*|leadership|open source|tools|technologies|links")
_HEADING = re.compile(rf"(?i)^\s*(?:{_KNOWN_HEADINGS})\s*:?\s*$")
_LABEL = re.compile(r"^\s*[A-Za-z][A-Za-z /.'’-]{0,28}\s*:\s*\S")


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _HEADING.match(s):
        return True
    m = _DROP_LINE.match(s)
    if m and not m.group("rest"):
        return True  # a drop-heading standing alone ("Declaration", "PERSONAL DETAILS")
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) >= 3 and len(s.split()) <= 4 and s == s.upper() and not re.search(r"\d|@", s):
        return True  # SHORT ALL CAPS LINE
    return bool(re.fullmatch(r"(?:[A-Z][A-Za-z&'’-]*\s?){1,4}:", s))  # Title Case:


def drop_sections(text: str) -> tuple[str, list[str]]:
    """Return (text without the sensitive sections, list of dropped heading/label names)."""
    out: list[str] = []
    dropped: list[str] = []
    mode = None  # None | "section" (until the next heading) | "label" (until a blank line, label or heading)
    for line in text.splitlines():
        m = _DROP_LINE.match(line)
        if m:
            alone = not m.group("rest")
            labelled = re.match(rf"(?i)^\s*(?:{_DROP})\s*[:\-–—]", line) is not None
            # A sentence that merely starts with such a word ("Address validation service built ...") is kept.
            if alone or labelled:
                dropped.append(" ".join(m.group("h").split()))
                if mode != "section":  # a label inside a dropped section does not end that section
                    mode = "section" if alone else "label"
                continue
        if mode == "section":
            if _is_heading(line) and not m:
                mode = None
                out.append(line)
            continue
        if mode == "label":
            if not line.strip() or _LABEL.match(line) or _is_heading(line):
                mode = None
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out), dropped


def redact_resume(text: str, redactor: Redactor) -> tuple[str, list[str]]:
    """Resume pipeline: drop sensitive sections, then redact names, hidden terms and patterns.

    Returns (clean text, dropped section names)."""
    kept, dropped = drop_sections(text)
    return redactor.redact(kept, resume=True), dropped
