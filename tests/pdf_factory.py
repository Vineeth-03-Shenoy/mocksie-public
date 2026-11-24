"""Build tiny real PDFs for tests (no PDF library needed): text at a position, size and colour, plus filled rectangles."""
from __future__ import annotations


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text(x: float, y: float, s: str, size: float = 12, color: str = "0 g") -> str:
    """y is measured from the bottom of the page (PDF coordinates). color: '0 g' black, '1 g' white, '1 0 0 rg' red ..."""
    return f"{color}\nBT /F1 {size} Tf {x} {y} Td ({_esc(s)}) Tj ET\n"


def rect(x: float, y: float, w: float, h: float, color: str) -> str:
    return f"{color}\n{x} {y} {w} {h} re f\n"


def make_pdf(pages: list[list[str]], width: int = 612, height: int = 792) -> bytes:
    """pages: one list of content-stream snippets per page."""
    objs: list[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    font_id = 3 + 2 * n_pages
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, ops in enumerate(pages):
        content = "".join(ops).encode("latin-1", "replace")
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents {content_id} 0 R "
                     f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
