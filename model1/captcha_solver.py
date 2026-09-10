"""
CAPTCHA Solver Module
======================
Two strategies:
  1. OCR  — free, uses EasyOCR / Tesseract. ~70-85% accuracy on Securimage.
  2. 2captcha — paid service (~$1-3/1000), ~99% accuracy.

Securimage (used by eCourts) generates 5-char alphanumeric text CAPTCHAs
with moderate noise. OCR works reasonably well after preprocessing.
"""

import logging
from io import BytesIO

from PIL import Image, ImageFilter, ImageEnhance, ImageOps

log = logging.getLogger("ecourts.captcha")


# ── Image Preprocessing ────────────────────────────────────────────────────────
def preprocess_captcha(img_bytes: bytes) -> Image.Image:
    """
    Clean up Securimage CAPTCHA for better OCR accuracy.
    Steps: grayscale → enhance contrast → threshold → slight sharpen
    """
    img = Image.open(BytesIO(img_bytes)).convert("RGB")

    # 1. Grayscale
    img = img.convert("L")

    # 2. Increase contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)

    # 3. Sharpen
    img = img.filter(ImageFilter.SHARPEN)

    # 4. Binary threshold (black text on white bg)
    img = img.point(lambda x: 0 if x < 140 else 255, "1")
    img = img.convert("L")

    # 5. Scale up for better OCR (2x)
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)

    return img


# ── OCR Solver (free) ──────────────────────────────────────────────────────────
def solve_captcha_ocr(img_bytes: bytes) -> str:
    """
    Solve CAPTCHA using EasyOCR (preferred) with Tesseract fallback.
    Returns cleaned alphanumeric string.
    """
    clean_img = preprocess_captcha(img_bytes)
    buf = BytesIO()
    clean_img.save(buf, format="PNG")
    buf.seek(0)
    clean_bytes = buf.read()

    text = ""

    # Strategy 1: EasyOCR
    try:
        import easyocr
        reader = easyocr.Reader(["en"], verbose=False)
        results = reader.readtext(clean_bytes, detail=0, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz")
        text = "".join(results).strip()
        log.info(f"EasyOCR result: '{text}'")
    except ImportError:
        log.warning("EasyOCR not installed, trying pytesseract...")
    except Exception as e:
        log.warning(f"EasyOCR failed: {e}")

    # Strategy 2: Tesseract fallback
    if not text:
        try:
            import pytesseract
            config = r"--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            text = pytesseract.image_to_string(clean_img, config=config).strip()
            log.info(f"Tesseract result: '{text}'")
        except ImportError:
            log.error("Neither EasyOCR nor pytesseract is installed!")
        except Exception as e:
            log.warning(f"Tesseract failed: {e}")

    # Clean: keep only alphanumeric, Securimage CAPTCHAs are 5 chars
    text = "".join(c for c in text if c.isalnum())
    return text


# ── 2Captcha Solver (paid, reliable) ──────────────────────────────────────────
def solve_captcha_2captcha(img_bytes: bytes, api_key: str) -> str:
    """
    Solve CAPTCHA using 2captcha.com human/AI solving service.
    Cost: ~$1-3 per 1000 solves. Response time: 5-15 seconds.

    Requires: pip install 2captcha-python
    Get API key at: https://2captcha.com
    """
    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        raise ImportError("Install with: pip install 2captcha-python")

    solver = TwoCaptcha(api_key)

    import base64
    img_b64 = base64.b64encode(img_bytes).decode()

    log.info("Sending CAPTCHA to 2captcha service (waiting 5-15s)...")
    result = solver.normal(img_b64, caseSensitive=0, minLen=4, maxLen=6)
    text = result.get("code", "").strip()
    log.info(f"2captcha result: '{text}'")
    return text


# ── Manual Solver (for testing) ───────────────────────────────────────────────
def solve_captcha_manual(img_bytes: bytes) -> str:
    """
    Save CAPTCHA to disk and prompt user to type it.
    Useful for testing the rest of the flow without a real solver.
    """
    import os
    path = "/tmp/ecourts_captcha.png"

    clean_img = preprocess_captcha(img_bytes)
    clean_img.save(path)

    print(f"\n[CAPTCHA] Image saved to: {path}")
    print("[CAPTCHA] Open the file and type the text below.")
    text = input("[CAPTCHA] Enter CAPTCHA text: ").strip()
    return text
