"""CAPTCHA solving: pluggable solvers + a generic fetch/solve/submit retry loop."""
from __future__ import annotations

import collections
import io
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ratelimit import BlockedAbort

log = logging.getLogger("V2.captcha")

ALLOWLIST = "abcdefghijklmnopqrstuvwxyz0123456789"


class CaptchaFailed(Exception):
    pass


class CaptchaSolver:
    name = "base"

    def solve(self, image_bytes: bytes) -> str:  # pragma: no cover - interface
        raise NotImplementedError


def sane(text: str, length: int = 6) -> bool:
    """Charset/length sanity check (securimage codes here are 6 lowercase alphanumerics)."""
    return bool(text) and len(text) == length and re.fullmatch(r"[a-z0-9]+", text) is not None


_reader = None
_reader_lock = threading.Lock()


def get_reader():
    """Process-wide singleton easyocr.Reader (model load takes seconds; reader is reused)."""
    global _reader
    with _reader_lock:
        if _reader is None:
            import easyocr
            _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return _reader


def preprocess_variants(image_bytes: bytes) -> List["Any"]:
    """Numpy images to OCR, best first. Benchmarked on 13 hand-labelled live captchas
    (tests/fixtures/captcha_bench): Otsu threshold without blur + 2x cubic = 13/13,
    raw = 11/13, 3x autocontrast = 12/13."""
    import numpy as np
    from PIL import Image, ImageOps
    out = []
    try:
        import cv2
        g = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if g is not None:
            _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            out.append(cv2.resize(th, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    except Exception:  # cv2 optional
        pass
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    out.append(np.array(img))
    g2 = ImageOps.autocontrast(ImageOps.grayscale(img))
    out.append(np.array(g2.resize((g2.width * 3, g2.height * 3), Image.LANCZOS)))
    return out


class EasyOcrSolver(CaptchaSolver):
    name = "easyocr"

    def __init__(self, length: int = 6, reader=None):
        self.length = length
        self._reader = reader

    def _read(self, arr) -> str:
        reader = self._reader or get_reader()
        res = reader.readtext(arr, allowlist=ALLOWLIST, detail=0, paragraph=False)
        return re.sub(r"[^a-z0-9]", "", "".join(res).lower())

    def solve(self, image_bytes: bytes) -> str:
        """Try the raw image first (best on the live captcha); fall back to preprocessed variants and
        majority-vote among results of the expected length."""
        valid: List[str] = []
        for i, arr in enumerate(preprocess_variants(image_bytes)):
            t = self._read(arr)
            if sane(t, self.length):
                if i == 0:
                    return t          # best variant gives a well-formed code: fast path
                valid.append(t)
        if valid:
            return collections.Counter(valid).most_common(1)[0][0]
        return ""


class ManualSolver(CaptchaSolver):
    """Fallback: save the image and ask on the console."""
    name = "manual"

    def __init__(self, path: str = "captcha_manual.png", prompt: Callable[[str], str] = input):
        self.path, self._prompt = path, prompt

    def solve(self, image_bytes: bytes) -> str:
        with open(self.path, "wb") as f:
            f.write(image_bytes)
        return self._prompt(f"CAPTCHA saved to {os.path.abspath(self.path)} - type the text: ").strip().lower()


class CallableSolver(CaptchaSolver):
    name = "callable"

    def __init__(self, fn: Callable[[bytes], str]):
        self.fn = fn

    def solve(self, image_bytes: bytes) -> str:
        return self.fn(image_bytes)


def make_solver(kind: str, length: int = 6) -> CaptchaSolver:
    if kind == "manual":
        return ManualSolver()
    if kind == "easyocr":
        return EasyOcrSolver(length)
    raise ValueError(f"unknown solver {kind!r}")


def solve_with_retries(fetch_image: Callable[[], bytes], solver: CaptchaSolver,
                       submit: Callable[[str], Tuple[bool, Any]], attempts: int = 10, length: int = 6,
                       on_attempt: Optional[Callable[[int, str, str], None]] = None,
                       debug_dir: Optional[str] = None) -> Tuple[Any, int]:
    """Loop: fetch a fresh captcha -> solve -> sanity check -> submit(text) -> (accepted, result).
    Returns (result, attempts_used) or raises CaptchaFailed. ``fetch_image`` may raise to signal a
    transient fetch problem (counted as an attempt)."""
    last_err = ""
    for n in range(1, attempts + 1):
        try:
            img = fetch_image()
        except BlockedAbort:
            raise
        except Exception as e:  # noqa: BLE001 - fetch problems are retried
            last_err = f"fetch: {e}"
            log.warning("captcha attempt %d: image fetch failed: %s", n, e)
            if on_attempt:
                on_attempt(n, "", "fetch-error")
            continue
        text = solver.solve(img)
        if not sane(text, length):
            last_err = f"insane OCR result {text!r}"
            log.info("captcha attempt %d: rejected locally (%r)", n, text)
            if on_attempt:
                on_attempt(n, text, "insane")
            continue
        accepted, result = submit(text)
        if not accepted and debug_dir:
            try:
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, f"rejected_{n:02d}_{text}.png"), "wb") as f:
                    f.write(img)
            except OSError:
                pass
        if on_attempt:
            on_attempt(n, text, "accepted" if accepted else "rejected")
        if accepted:
            return result, n
        last_err = f"server rejected {text!r}"
    raise CaptchaFailed(f"CAPTCHA not solved in {attempts} attempts ({last_err})")
