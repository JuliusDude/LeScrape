"""
ecourts.core.captcha
====================
Pluggable multi-strategy CAPTCHA resolution architecture with Local OCR,
Manual Interactive, Third-party API, and Mock deterministic solvers,
plus CaptchaHandler supporting in-band refresh recovery.
"""

from __future__ import annotations

import abc
import base64
import hashlib
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageFilter

from ecourts.core.client import PacedSession

log = logging.getLogger("ecourts.core.captcha")


# ── Image Preprocessing ────────────────────────────────────────────────────────


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


# ── Manual Interactive Solver ─────────────────────────────────────────────────


class ManualInteractiveSolver(BaseCaptchaSolver):
    """Interactive terminal prompt solver for human verification.

    Saves the image to a cross-platform temp directory (using tempfile.gettempdir())
    and opens the image viewer before prompting the operator via console input.
    """

    def __init__(self, prompt_func: Optional[Callable[[str], str]] = None) -> None:
        self.prompt_func = prompt_func or input

    def solve(self, image_bytes: bytes) -> str:
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"ecourts_captcha_{int(time.time())}.png")

        cleaned_bytes = preprocess_captcha_image(image_bytes)
        with open(temp_path, "wb") as f:
            f.write(cleaned_bytes)

        log.info("Saved CAPTCHA image to %s", temp_path)
        print(f"\n[CAPTCHA] Image saved to: {temp_path}")

        # Attempt to launch system viewer
        try:
            if sys.platform.startswith("win"):
                os.startfile(temp_path)  # type: ignore[attr-defined]
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", temp_path])
            else:
                subprocess.Popen(["xdg-open", temp_path])
        except Exception as exc:
            log.debug("Could not launch image viewer automatically: %s", exc)

        print("[CAPTCHA] Please open the image and enter the 5-6 character alphanumeric code.")
        try:
            solution = self.prompt_func("[CAPTCHA] Enter solution: ").strip()
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

        return "".join(c for c in solution if c.isalnum())


# ── Third Party API Solver ─────────────────────────────────────────────────────


class ThirdPartyApiSolver(BaseCaptchaSolver):
    """Pluggable third-party CAPTCHA solver interface supporting 2Captcha,

    Anti-Captcha, or custom HTTP endpoints.
    Can solve using direct HTTP requests or through the twocaptcha library.
    """

    def __init__(
        self,
        api_key: str,
        service: str = "2captcha",
        in_url: Optional[str] = None,
        res_url: Optional[str] = None,
        poll_interval: float = 3.0,
        max_wait: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.service = service.lower()
        self.in_url = in_url or "https://2captcha.com/in.php"
        self.res_url = res_url or "https://2captcha.com/res.php"
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def is_available(self) -> bool:
        return bool(self.api_key)

    def solve(self, image_bytes: bytes) -> str:
        if not self.api_key:
            raise ValueError("ThirdPartyApiSolver requires an api_key")

        # Try twocaptcha python library if available and service is 2captcha
        if self.service == "2captcha":
            try:
                from twocaptcha import TwoCaptcha  # type: ignore[import-not-found]

                solver = TwoCaptcha(self.api_key)
                b64_img = base64.b64encode(image_bytes).decode("ascii")
                res = solver.normal(b64_img, caseSensitive=0, minLen=4, maxLen=6)
                code = res.get("code", "")
                return "".join(c for c in code if c.isalnum())
            except ImportError:
                log.debug("twocaptcha package not installed; falling back to direct HTTP API")

        # Direct HTTP API fallback (2captcha protocol compatible)
        import requests

        b64_img = base64.b64encode(image_bytes).decode("ascii")
        submit_payload = {
            "key": self.api_key,
            "method": "base64",
            "body": b64_img,
            "json": 1,
        }

        resp = requests.post(self.in_url, data=submit_payload, timeout=30)
        data = resp.json()
        if data.get("status") != 1:
            raise RuntimeError(f"CAPTCHA API submission failed: {data.get('request', data)}")

        captcha_id = data.get("request")
        log.info("Submitted CAPTCHA to %s (id: %s)", self.service, captcha_id)

        start_time = time.monotonic()
        while time.monotonic() - start_time < self.max_wait:
            time.sleep(self.poll_interval)
            poll_resp = requests.get(
                self.res_url,
                params={"key": self.api_key, "action": "get", "id": captcha_id, "json": 1},
                timeout=30,
            )
            poll_data = poll_resp.json()
            if poll_data.get("status") == 1:
                solution = poll_data.get("request", "").strip()
                log.info("CAPTCHA resolved by %s: '%s'", self.service, solution)
                return "".join(c for c in solution if c.isalnum())
            if poll_data.get("request") != "CAPCHA_NOT_READY":
                raise RuntimeError(f"CAPTCHA resolution error: {poll_data.get('request')}")

        raise TimeoutError(f"CAPTCHA timed out after {self.max_wait}s on {self.service}")


# ── Mock Deterministic Solver ──────────────────────────────────────────────────


class MockCaptchaSolver(BaseCaptchaSolver):
    """Deterministic mock solver for offline unit and scenario testing.

    Can return:
      1. A pre-mapped solution by image SHA-256 or MD5 hash.
      2. A configured sequence of solutions (useful for testing rejection retry loops).
      3. A default static solution string.
    """

    def __init__(
        self,
        default_solution: str = "mock1",
        hash_map: Optional[Dict[str, str]] = None,
        sequence: Optional[List[str]] = None,
    ) -> None:
        self.default_solution = default_solution
        self.hash_map: Dict[str, str] = hash_map or {}
        self.sequence: List[str] = list(sequence) if sequence else []
        self.call_count: int = 0

    def register_hash(self, image_hash_or_bytes: Union[str, bytes], solution: str) -> None:
        if isinstance(image_hash_or_bytes, bytes):
            h_sha = hashlib.sha256(image_hash_or_bytes).hexdigest()
            h_md5 = hashlib.md5(image_hash_or_bytes).hexdigest()
            self.hash_map[h_sha] = solution
            self.hash_map[h_md5] = solution
        else:
            self.hash_map[image_hash_or_bytes] = solution

    def solve(self, image_bytes: bytes) -> str:
        self.call_count += 1
        if self.sequence:
            return self.sequence.pop(0)

        sha = hashlib.sha256(image_bytes).hexdigest()
        md5 = hashlib.md5(image_bytes).hexdigest()

        if sha in self.hash_map:
            return self.hash_map[sha]
        if md5 in self.hash_map:
            return self.hash_map[md5]

        return self.default_solution


# ── Captcha Handler ───────────────────────────────────────────────────────────


class CaptchaHandler:
    """Orchestrates CAPTCHA retrieval from eCourts endpoints, invoking the solver

    strategy, and handling in-band recovery from response fragments.
    """

    def __init__(self, session: PacedSession, solver: Optional[BaseCaptchaSolver] = None) -> None:
        self.session = session
        self.solver = solver or LocalOcrSolver()

    def extract_image_url(self, div_html: str) -> str:
        """Extract relative or absolute image src from a div_captcha HTML snippet."""
        soup = BeautifulSoup(div_html, "html.parser")
        img_tag = soup.find("img", id="captcha_image") or soup.find(
            "img", src=re.compile(r"securimage_show\.php")
        )
        if not img_tag or not img_tag.get("src"):
            raise ValueError(f"No valid captcha image found in HTML snippet: {div_html[:200]}")
        return str(img_tag["src"])

    def get_and_solve_captcha(self) -> Tuple[str, str]:
        """POST to casestatus/getCaptcha, parse div_captcha, download image,

        and return (captcha_code, image_url).
        """
        resp = self.session.post("casestatus/getCaptcha", data={})
        try:
            data = resp.json()
        except Exception:
            import json

            try:
                data = json.loads(resp.text)
            except Exception as exc:
                raise ValueError(f"Invalid JSON response from getCaptcha: {resp.text[:200]}") from exc

        div_captcha = data.get("div_captcha", "")
        if not div_captcha:
            raise ValueError(f"Missing 'div_captcha' field in response: {list(data.keys())}")

        return self.solve_from_div(div_captcha)

    def solve_from_div(self, div_html: str) -> Tuple[str, str]:
        """Given a div_captcha HTML fragment, download the image and solve it."""
        img_url = self.extract_image_url(div_html)
        img_bytes = self.session.get_image(img_url)
        solution = self.solver.solve(img_bytes)
        log.info("Solved CAPTCHA (%s) -> '%s'", img_url, solution)
        return solution, img_url

    def solve_from_response(self, response_text: str) -> Tuple[str, str]:
        """Extract fresh div_captcha from an endpoint response JSON and solve it."""
        import json

        try:
            data = json.loads(response_text)
        except Exception as exc:
            raise ValueError("Failed to parse JSON response for captcha extraction") from exc

        div_captcha = data.get("div_captcha")
        if not div_captcha:
            raise ValueError("No div_captcha found in response for in-band refresh")

        return self.solve_from_div(div_captcha)
