"""Resume PDF -> visible text only. Hidden text is the classic way to smuggle instructions to an LLM.

Dropped before anything is read:
  - characters with low contrast against what is painted behind them (white-on-white, text matching a covering
    rectangle's colour); white text on a DARK header bar stays, because the contrast is high
  - fonts under 4 pt
  - characters outside the page box
  - zero-width / formatting / control characters and Unicode "tag" characters (invisible in every viewer)
Rejected outright: not a PDF, over the byte limit, over the page limit, encrypted or unreadable.
The count and reasons for removed characters are returned so the console can show "hidden text removed".
"""
from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass, field

MIN_FONT_PT = 4.0
MIN_CONTRAST = 0.12   # luminance difference (0..1) below which text counts as hidden
MAX_TEXT_CHARS = 20_000


class ResumeRejected(ValueError):
    """The upload cannot be used; the message is safe to show the interviewer."""


@dataclass
class Extract:
    text: str
    pages: int
    hidden_chars: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


def _luminance(color) -> float:
    """pdfminer gives None (default black), a number, or a tuple of 1 (gray), 3 (RGB) or 4 (CMYK) numbers."""
    if color is None:
        return 0.0
    if isinstance(color, (int, float)):
        return float(color)
    if not isinstance(color, (tuple, list)) or not all(isinstance(c, (int, float)) for c in color):
        return 0.0  # pattern / named colour space: treat as visible dark
    if len(color) == 1:
        return float(color[0])
    if len(color) == 3:
        r, g, b = color
        return 0.299 * r + 0.587 * g + 0.114 * b
    if len(color) == 4:
        c, m, y, k = color
        return 0.299 * (1 - c) * (1 - k) + 0.587 * (1 - m) * (1 - k) + 0.114 * (1 - y) * (1 - k)
    return 0.0


def _background(page, ch) -> float:
    """Luminance of the last-painted filled rectangle under the character's centre; white page otherwise."""
    cx, cy = (ch["x0"] + ch["x1"]) / 2, (ch["top"] + ch["bottom"]) / 2
    bg = 1.0
    for r in page.rects:
        if r.get("fill") and r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]:
            bg = _luminance(r.get("non_stroking_color"))
    return bg


def _strip_invisible(text: str) -> tuple[str, int]:
    out, n = [], 0
    for c in text:
        cat = unicodedata.category(c)
        if cat == "Cf" or (cat == "Cc" and c not in "\n\t") or cat == "Co" or 0xE0000 <= ord(c) <= 0xE007F:
            n += 1
            continue
        out.append(c)
    return "".join(out), n


def extract_visible_text(pdf_bytes: bytes, max_pages: int = 2, max_bytes: int = 2_000_000) -> Extract:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ResumeRejected("that file is not a PDF")
    if len(pdf_bytes) > max_bytes:
        raise ResumeRejected(f"the PDF is larger than {max_bytes // 1_000_000} MB")
    import pdfplumber  # imported here so the rest of the app never pays for it

    reasons: dict[str, int] = {}

    def hit(why: str, n: int = 1) -> None:
        reasons[why] = reasons.get(why, 0) + n

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > max_pages:
                raise ResumeRejected(f"the resume has {len(pdf.pages)} pages; the limit is {max_pages}")
            parts: list[str] = []
            for page in pdf.pages:
                width, height = float(page.width), float(page.height)
                keep: set[int] = set()
                for i, ch in enumerate(page.chars):
                    if ch["x1"] < 0 or ch["x0"] > width or ch["bottom"] < 0 or ch["top"] > height:
                        hit("off_page")
                    elif ch.get("size", 12) < MIN_FONT_PT:
                        hit("tiny_font")
                    elif ch["text"].strip() and abs(_luminance(ch.get("non_stroking_color")) - _background(page, ch)) < MIN_CONTRAST:
                        hit("low_contrast")
                    else:
                        keep.add(i)
                chars = page.chars
                kept_ids = {id(chars[i]) for i in keep}
                parts.append(page.filter(lambda o: o.get("object_type") != "char" or id(o) in kept_ids).extract_text() or "")
    except ResumeRejected:
        raise
    except Exception as exc:  # malformed, encrypted, truncated: never leak parser internals to the UI
        raise ResumeRejected("the PDF could not be read (damaged or password-protected)") from exc

    text, invisible = _strip_invisible("\n".join(parts))
    if invisible:
        hit("invisible_unicode", invisible)
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ResumeRejected("no readable text found (a scanned image PDF?)")
    return Extract(text=text, pages=len(parts), hidden_chars=sum(reasons.values()), reasons=reasons, truncated=truncated)
