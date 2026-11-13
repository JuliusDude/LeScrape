"""Save PDFs by content hash and extract their text layer (pymupdf)."""
from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Any, Dict, Optional

MIN_TEXT_CHARS_PER_PAGE = 5


def is_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-" or b"%PDF-" in content[:1024]


def extract_text(content: bytes) -> Dict[str, Any]:
    """-> {pages, text (pages joined by form feed), needs_ocr, error}"""
    try:
        import pymupdf
        doc = pymupdf.open(stream=content, filetype="pdf")
        try:
            pages = [p.get_text("text") for p in doc]
        finally:
            doc.close()
    except Exception as e:  # noqa: BLE001
        return {"pages": 0, "text": "", "needs_ocr": 1, "error": f"{type(e).__name__}: {e}"}
    text = "\f".join(pages)
    chars = sum(len(p.strip()) for p in pages)
    needs = 1 if (not pages or chars < MIN_TEXT_CHARS_PER_PAGE * len(pages)) else 0
    return {"pages": len(pages), "text": text, "needs_ocr": needs, "error": None}


def save_pdf(content: bytes, pdf_dir: Optional[str]) -> Dict[str, Any]:
    """Store ``content`` as <pdf_dir>/<sha[:2]>/<sha>.pdf (idempotent) and describe it."""
    sha = hashlib.sha256(content).hexdigest()
    info = extract_text(content)
    info.update({"sha256": sha, "size": len(content), "path": None})
    if pdf_dir:
        sub = os.path.join(pdf_dir, sha[:2])
        os.makedirs(sub, exist_ok=True)
        dest = os.path.join(sub, sha + ".pdf")
        if not os.path.exists(dest):
            fd, tmp = tempfile.mkstemp(dir=sub, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            try:
                os.replace(tmp, dest)
            except OSError:
                # another worker stored the identical file at the same moment (Windows refuses the replace)
                if not os.path.exists(dest):
                    raise
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        info["path"] = os.path.abspath(dest)
    return info
