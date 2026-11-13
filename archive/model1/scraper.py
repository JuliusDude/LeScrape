"""
eCourts Case Status Scraper
============================
Scrapes case status from https://services.ecourts.gov.in
Based on network analysis of the actual request flow.

Flow:
  1. Init session (get cookies)
  2. Fetch CAPTCHA (POST getCaptcha → GET securimage PNG)
  3. Solve CAPTCHA (OCR or 2captcha)
  4. Cascade dropdowns: fillDistrict → fillcomplex → fillCourtEstablishment → fillCaseType
  5. POST set_data (persist selections server-side)
  6. Refresh CAPTCHA
  7. POST submit_case_type → parse results
"""

import time
import random
import logging
import re
from io import BytesIO
from typing import Optional

import requests
from bs4 import BeautifulSoup
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from captcha_solver import solve_captcha_ocr, solve_captcha_2captcha
from parsers import parse_case_results, parse_dropdown_options

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ecourts")

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL  = "https://services.ecourts.gov.in/ecourtindia_v6/"
HOME_URL  = BASE_URL  # first hit to get session cookies
IMG_BASE  = "https://services.ecourts.gov.in"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://services.ecourts.gov.in",
    "Referer": "https://services.ecourts.gov.in/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

FORM_HEADERS = {
    **BASE_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Dest": "empty",
}


# ── Session Manager ────────────────────────────────────────────────────────────
class ECourtSession:
    """
    Manages a single requests.Session with eCourts cookie lifecycle.
    Automatically re-initialises when session expires.
    """

    def __init__(self, captcha_mode: str = "ocr", api_key_2captcha: str = ""):
        """
        captcha_mode: "ocr" (free, ~70-85% accuracy) or "2captcha" (paid, ~99%)
        api_key_2captcha: required when captcha_mode="2captcha"
        """
        self.captcha_mode = captcha_mode
        self.api_key_2captcha = api_key_2captcha
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self._initialized = False

    def init(self):
        """Hit the homepage to get SERVICES_SESSID + JSESSION cookies."""
        log.info("Initialising session (fetching cookies)...")
        resp = self.session.get(
            HOME_URL,
            params={"p": "casestatus/index", "app_token": ""},
            timeout=45,
        )
        resp.raise_for_status()
        cookies = dict(self.session.cookies)
        if "SERVICES_SESSID" not in cookies:
            raise RuntimeError("Failed to get SERVICES_SESSID cookie. Site may be down.")
        log.info(f"Session cookies: {list(cookies.keys())}")
        self._initialized = True
        return self

    def _ensure_init(self):
        if not self._initialized:
            self.init()

    def post(self, endpoint: str, data: dict, retries: int = 3) -> requests.Response:
        """POST to a ?p=<endpoint> with form data."""
        self._ensure_init()
        url = BASE_URL
        params = {"p": endpoint}
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.post(
                    url,
                    params=params,
                    data=data,
                    headers=FORM_HEADERS,
                    timeout=45,
                )
                if resp.status_code == 200:
                    return resp
                log.warning(f"POST {endpoint} returned {resp.status_code} (attempt {attempt})")
            except requests.RequestException as e:
                log.warning(f"POST {endpoint} error: {e} (attempt {attempt})")
            if attempt < retries:
                time.sleep(2 ** attempt + random.uniform(0, 1))
        raise RuntimeError(f"POST {endpoint} failed after {retries} attempts")

    def get_image(self, path: str) -> bytes:
        """GET an image resource."""
        self._ensure_init()
        url = IMG_BASE + path if path.startswith("/") else path
        resp = self.session.get(
            url,
            headers={**BASE_HEADERS, "Accept": "image/avif,image/webp,image/png,*/*", "Sec-Fetch-Dest": "image"},
            timeout=45,
        )
        resp.raise_for_status()
        return resp.content

    # ── CAPTCHA ────────────────────────────────────────────────────────────────
    def fetch_captcha(self) -> tuple[bytes, str]:
        """
        Returns (image_bytes, captcha_image_url).
        Two-step:
          1. POST getCaptcha → HTML with <img src="...securimage_show.php?hash">
          2. GET that URL → PNG bytes
        """
        log.info("Fetching CAPTCHA...")
        resp = self.post("casestatus/getCaptcha", data={"app_token": ""})
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the securimage img tag
        img_tag = soup.find("img", src=re.compile(r"securimage_show\.php"))
        if not img_tag:
            # Fallback: find any img
            img_tag = soup.find("img")
        if not img_tag:
            log.debug(f"getCaptcha response: {resp.text[:300]}")
            raise RuntimeError("Could not find CAPTCHA image tag in getCaptcha response")

        img_src = img_tag.get("src", "")
        # Make absolute if relative
        if img_src.startswith("/"):
            img_url = IMG_BASE + img_src
        elif img_src.startswith("http"):
            img_url = img_src
        else:
            img_url = IMG_BASE + "/ecourtindia_v6/" + img_src

        log.info(f"CAPTCHA image URL: {img_url}")
        img_bytes = self.get_image(img_url)
        return img_bytes, img_url

    def solve_captcha(self) -> str:
        """Fetch + solve CAPTCHA. Returns the text solution."""
        img_bytes, img_url = self.fetch_captcha()

        if self.captcha_mode == "2captcha":
            if not self.api_key_2captcha:
                raise ValueError("2captcha API key required when captcha_mode='2captcha'")
            solution = solve_captcha_2captcha(img_bytes, self.api_key_2captcha)
        else:
            solution = solve_captcha_ocr(img_bytes)

        log.info(f"CAPTCHA solved: '{solution}'")
        return solution


# ── Dropdown Helpers ───────────────────────────────────────────────────────────
class ECourtDropdowns:
    """Resolves the cascading dropdown chain."""

    def __init__(self, session: ECourtSession):
        self.s = session

    def get_districts(self, state_code: str) -> list[dict]:
        """Returns list of {code, name} for districts in a state."""
        resp = self.s.post("casestatus/fillDistrict", data={"state_code": state_code, "app_token": ""})
        return parse_dropdown_options(resp.text, value_attr="district_code")

    def get_complexes(self, state_code: str, district_code: str) -> list[dict]:
        """Returns court complexes for a district."""
        resp = self.s.post("casestatus/fillcomplex", data={
            "state_code": state_code,
            "district_code": district_code,
            "app_token": "",
        })
        return parse_dropdown_options(resp.text, value_attr="complex_code")

    def get_establishments(self, state_code: str, district_code: str, complex_code: str) -> list[dict]:
        """Returns individual courts (establishments) in a complex."""
        resp = self.s.post("casestatus/fillCourtEstablishment", data={
            "state_code": state_code,
            "district_code": district_code,
            "complex_code": complex_code,
            "app_token": "",
        })
        return parse_dropdown_options(resp.text, value_attr="est_code")

    def get_case_types(self, state_code: str, district_code: str,
                       complex_code: str, est_code: str) -> list[dict]:
        """Returns case types for an establishment."""
        resp = self.s.post("casestatus/fillCaseType", data={
            "state_code": state_code,
            "district_code": district_code,
            "complex_code": complex_code,
            "est_code": est_code,
            "app_token": "",
        })
        return parse_dropdown_options(resp.text, value_attr="case_type_code")

    def set_data(self, state_code: str, district_code: str,
                 complex_code: str, est_code: str, case_type: str):
        """
        CRITICAL: Persists dropdown selections to the server session.
        Must be called before submit_case_type or results will be wrong/empty.
        """
        log.info("Setting session data (set_data)...")
        self.s.post("casestatus/set_data", data={
            "state_code": state_code,
            "district_code": district_code,
            "complex_code": complex_code,
            "est_code": est_code,
            "case_type": case_type,
            "app_token": "",
        })


# ── Main Scraper ───────────────────────────────────────────────────────────────
class ECourtScraper:
    """
    High-level scraper. Use search_by_case_type() to query cases.
    """

    def __init__(
        self,
        captcha_mode: str = "ocr",
        api_key_2captcha: str = "",
        delay: float = 3.0,
    ):
        """
        captcha_mode: "ocr" or "2captcha"
        delay: seconds between requests (be polite to the server)
        """
        self.session = ECourtSession(captcha_mode, api_key_2captcha)
        self.dropdowns = ECourtDropdowns(self.session)
        self.delay = delay

    def _sleep(self):
        jitter = random.uniform(0, 1)
        time.sleep(self.delay + jitter)

    def search_by_case_type(
        self,
        state_code: str,
        district_code: str,
        complex_code: str,
        est_code: str,
        case_type: str,
        case_number: str,
        year: str,
    ) -> list[dict]:
        """
        Search cases by case type + number + year.

        Returns list of case dicts:
          - cnr_number
          - case_type
          - filing_number
          - registration_number
          - petitioner
          - respondent
          - next_hearing
          - court
          - status
        """
        self.session.init()
        self._sleep()

        # Step 1: Cascade dropdowns (populate server-side state)
        log.info(f"Resolving dropdowns for state={state_code}, district={district_code}...")
        self.dropdowns.set_data(state_code, district_code, complex_code, est_code, case_type)
        self._sleep()

        # Step 2: Solve CAPTCHA
        captcha_text = self.session.solve_captcha()
        self._sleep()

        # Step 3: Submit search
        log.info(f"Submitting search: case_type={case_type}, number={case_number}, year={year}")
        resp = self.session.post("casestatus/submit_case_type", data={
            "state_code": state_code,
            "district_code": district_code,
            "complex_code": complex_code,
            "est_code": est_code,
            "case_type": case_type,
            "case_number": case_number,
            "rgyear": year,
            "captcha": captcha_text,
            "app_token": "",
        })

        cases = parse_case_results(resp.text)
        log.info(f"Found {len(cases)} case(s)")
        return cases

    def search_by_cnr(self, cnr_number: str) -> Optional[dict]:
        """
        Search by CNR number (most reliable — unique across all courts).
        CNR format: STATEDISTRICT123456789012
        """
        self.session.init()
        self._sleep()

        captcha_text = self.session.solve_captcha()
        self._sleep()

        log.info(f"Searching by CNR: {cnr_number}")
        resp = self.session.post("casestatus/getCaseStatus", data={
            "cino": cnr_number,
            "captcha": captcha_text,
            "app_token": "",
        })

        cases = parse_case_results(resp.text)
        return cases[0] if cases else None

    def search_by_party_name(
        self,
        state_code: str,
        district_code: str,
        complex_code: str,
        est_code: str,
        party_name: str,
        year: str = "",
    ) -> list[dict]:
        """Search by party/advocate name."""
        self.session.init()
        self._sleep()

        captcha_text = self.session.solve_captcha()
        self._sleep()

        log.info(f"Searching by party name: '{party_name}'")
        resp = self.session.post("casestatus/submit_party_name", data={
            "state_code": state_code,
            "district_code": district_code,
            "complex_code": complex_code,
            "est_code": est_code,
            "party_name": party_name,
            "year": year,
            "captcha": captcha_text,
            "app_token": "",
        })

        cases = parse_case_results(resp.text)
        log.info(f"Found {len(cases)} case(s)")
        return cases

    def list_districts(self, state_code: str) -> list[dict]:
        return self.dropdowns.get_districts(state_code)

    def list_complexes(self, state_code: str, district_code: str) -> list[dict]:
        return self.dropdowns.get_complexes(state_code, district_code)

    def list_establishments(self, state_code: str, district_code: str, complex_code: str) -> list[dict]:
        return self.dropdowns.get_establishments(state_code, district_code, complex_code)

    def list_case_types(self, state_code: str, district_code: str,
                        complex_code: str, est_code: str) -> list[dict]:
        return self.dropdowns.get_case_types(state_code, district_code, complex_code, est_code)
