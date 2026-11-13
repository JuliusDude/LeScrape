"""
V1.captcha
==========
CAPTCHA image preprocessing and the local OCR solver used by V1.

Copied verbatim from ``ecourts/core/captcha.py`` (``preprocess_captcha_image``,
``BaseCaptchaSolver``, ``LocalOcrSolver``) on 2026-11-13 so that V1 no longer imports
the ``ecourts`` package. Behaviour is unchanged; only the unused PacedSession import was dropped.
"""

from __future__ import annotations

import abc
import io
import logging
from typing import Any, Dict, Optional

from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger("V1.captcha")


def preprocess_captcha_image(image_bytes: bytes) -> bytes:
    """Preprocess Securimage CAPTCHA image using OpenCV / Pillow.

    Pipeline:
      1. Grayscale conversion
      2. Contrast enhancement
      3. Median blur (removes background pixel noise and thin lines)
      4. Binary thresholding (black text on white background)
      5. 2x resize with high-quality interpolation
    Returns cleaned image as PNG bytes.
    """
    # Attempt OpenCV pipeline first if available
    try:
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # Contrast stretching (normalize to full 0-255 range)
            norm = cv2.normalize(gray, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)
            # Median blur to eliminate salt-and-pepper noise
            blurred = cv2.medianBlur(norm, 3)
            # Binary thresholding (Otsu's thresholding after slight Gaussian blur)
            _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            # 2x resize
            resized = cv2.resize(thresh, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            success, enc = cv2.imencode(".png", resized)
            if success:
                return enc.tobytes()
    except Exception as exc:
        log.debug("OpenCV preprocessing not used (%s), falling back to PIL", exc)

    # PIL fallback pipeline
    try:
        img_pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        gray_pil = img_pil.convert("L")
        enhancer = ImageEnhance.Contrast(gray_pil)
        contrasted = enhancer.enhance(2.5)
        sharpened = contrasted.filter(ImageFilter.SHARPEN)
        # Threshold: cutoff around 140
        thresholded = sharpened.point(lambda x: 0 if x < 140 else 255, "1").convert("L")
        w, h = thresholded.size
        upscaled = thresholded.resize((w * 2, h * 2), Image.LANCZOS)
        buf = io.BytesIO()
        upscaled.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        log.warning("Pillow preprocessing failed: %s; using raw image bytes", exc)
        return image_bytes


# ── Base Solver ────────────────────────────────────────────────────────────────


class BaseCaptchaSolver(abc.ABC):
    """Abstract base class for all CAPTCHA solving strategies."""

    @abc.abstractmethod
    def solve(self, image_bytes: bytes) -> str:
        """Takes raw CAPTCHA image bytes and returns the solved alphanumeric string."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Returns True if the solver's prerequisites/libraries are met."""
        return True


# ── Local OCR Solver ──────────────────────────────────────────────────────────


class LocalOcrSolver(BaseCaptchaSolver):
    """Local OCR solver utilizing EasyOCR with OpenCV/Pillow image preprocessing.

    Caches the EasyOCR Reader instance as a singleton to avoid repeated
    2-5 second PyTorch weight loading overhead.
    Safely degrades if offline or model weights are missing.
    """

    _reader_instance: Optional[Any] = None
    _reader_init_attempted: bool = False

    def __init__(self, use_gpu: bool = False, model_dir: Optional[str] = None) -> None:
        self.use_gpu = use_gpu
        self.model_dir = model_dir

    @classmethod
    def get_reader(cls, use_gpu: bool = False, model_dir: Optional[str] = None) -> Optional[Any]:
        """Thread-safe singleton accessor for EasyOCR Reader."""
        if cls._reader_instance is None and not cls._reader_init_attempted:
            cls._reader_init_attempted = True
            try:
                import easyocr

                kwargs: Dict[str, Any] = {
                    "lang_list": ["en"],
                    "gpu": use_gpu,
                    "verbose": False,
                }
                if model_dir:
                    kwargs["model_storage_directory"] = model_dir
                cls._reader_instance = easyocr.Reader(**kwargs)
                log.info("EasyOCR Reader successfully initialized (singleton)")
            except Exception as exc:
                log.warning(
                    "EasyOCR reader could not be initialized (%s). Offline or missing weights.",
                    exc,
                )
                cls._reader_instance = None
        return cls._reader_instance

    def is_available(self) -> bool:
        reader = self.get_reader(self.use_gpu, self.model_dir)
        if reader is not None:
            return True
        try:
            import pytesseract
            return True
        except ImportError:
            return False

    def solve(self, image_bytes: bytes) -> str:
        cleaned_bytes = preprocess_captcha_image(image_bytes)
        text = ""

        # Primary: EasyOCR
        reader = self.get_reader(self.use_gpu, self.model_dir)
        if reader is not None:
            try:
                results = reader.readtext(
                    cleaned_bytes,
                    detail=0,
                    allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz",
                )
                text = "".join(results).strip()
                log.debug("EasyOCR extracted text: '%s'", text)
            except Exception as exc:
                log.warning("EasyOCR inference failed: %s", exc)

        # Fallback: pytesseract
        if not text:
            try:
                import pytesseract

                img = Image.open(io.BytesIO(cleaned_bytes))
                config = (
                    r"--oem 3 --psm 8 "
                    r"-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
                )
                text = pytesseract.image_to_string(img, config=config).strip()
                log.debug("pytesseract extracted text: '%s'", text)
            except Exception as exc:
                log.debug("pytesseract fallback unavailable: %s", exc)

        cleaned = "".join(c for c in text if c.isalnum())
        if not cleaned:
            log.warning("LocalOcrSolver produced empty solution")
        return cleaned
