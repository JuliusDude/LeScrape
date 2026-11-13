"""
eCourts Case Status Scraper
============================
Field names and flow CONFIRMED from HAR capture (services_ecourts_gov_in_2.har).

Confirmed flow:
  1. POST getCaptcha          -> JSON {"div_captcha": "<html with <img id=captcha_image src=...>"}
  2. GET  securimage_show.php -> CAPTCHA PNG bytes
  3. POST fillDistrict        -> JSON {"dist_list": "<option>...", "status":1}
  4. POST fillcomplex         -> JSON {"complex_list": "<option value=CCC@EST@Y>...", "status":1}
  5. POST fillCourtEstablishment -> JSON {"establishment_list": "<option>...", "status":1}
  6. POST set_data            -> persist complex/dist/state selection
  7. POST fillCaseType        -> JSON {"casetype_list": "<option value=CT^EST>...", ...}
  8. POST set_data            -> persist est_code selection too
  9. POST getCaptcha          -> fresh CAPTCHA right before submit
 10. POST submit_case_type    -> JSON {"case_data": "<html table or 'Record not found'>", "div_captcha": "..."}

IMPORTANT: No cookies were observed anywhere in the captured flow (no Cookie
header sent, no Set-Cookie received). `app_token` is sent as an empty string
on every request. This suggests this version of the site does NOT require
session/cookie management for these endpoints — every request is effectively
self-contained as long as the right form fields are present.

If you find this changes (e.g. requests start failing with empty `app_token`),
re-capture a HAR including the initial page load to check for a token.
"""

import time
import random
import logging
import re

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from captcha_solver import solve_captcha_ocr, solve_captcha_2captcha
from parsers import (
    parse_dropdown_options,
    parse_case_results,
    parse_case_detail,
    parse_captcha_response,
    complex_code_numeric,
    unwrap_json,
    WrongCaptchaError,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ecourts")

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"
IMG_BASE = "https://services.ecourts.gov.in"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://services.ecourts.gov.in",
    "Referer": "https://services.ecourts.gov.in/",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


# ── Low-level HTTP client ──────────────────────────────────────────────────────
class ECourtClient:
    """
    Thin wrapper around requests.Session for the eCourts AJAX endpoints.
    Every POST automatically includes ajax_req=true and app_token="" as
    confirmed by the HAR capture.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.app_token = ""  # observed empty in all captured requests

    def post(self, endpoint: str, data: dict, retries: int = 3) -> str:
        """POST to ?p=<endpoint>. Returns response text. Retries on network errors."""
        payload = {"ajax_req": "true", "app_token": self.app_token, **data}

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.post(
                    BASE_URL,
                    params={"p": endpoint},
                    data=payload,
                    timeout=20,
                )
                if resp.status_code == 200:
                    return resp.text
                log.warning(f"POST {endpoint} -> HTTP {resp.status_code} (attempt {attempt})")
            except requests.RequestException as e:
                log.warning(f"POST {endpoint} error: {e} (attempt {attempt})")

            if attempt < retries:
                time.sleep(2 ** attempt + random.uniform(0, 1))

        raise RuntimeError(f"POST {endpoint} failed after {retries} attempts")

    def get_image(self, relative_or_absolute_url: str) -> bytes:
        url = relative_or_absolute_url
        if url.startswith("/"):
            url = IMG_BASE + url
        resp = self.session.get(
            url,
            headers={**HEADERS, "Accept": "image/avif,image/webp,image/png,*/*", "Sec-Fetch-Dest": "image"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.content


# ── CAPTCHA handling ───────────────────────────────────────────────────────────
class CaptchaHandler:
    def __init__(self, client: ECourtClient, mode: str = "ocr", api_key: str = ""):
        self.client = client
        self.mode = mode
        self.api_key = api_key

    def fetch_and_solve(self) -> str:
        """POST getCaptcha -> download image -> solve -> return text."""
        resp_text = self.client.post("casestatus/getCaptcha", data={})
        img_src = parse_captcha_response(resp_text)
        log.info(f"CAPTCHA image: {img_src}")

        img_bytes = self.client.get_image(img_src)

        if self.mode == "2captcha":
            if not self.api_key:
                raise ValueError("2captcha mode requires an API key")
            solution = solve_captcha_2captcha(img_bytes, self.api_key)
        elif self.mode == "manual":
            from captcha_solver import solve_captcha_manual
            solution = solve_captcha_manual(img_bytes)
        else:
            solution = solve_captcha_ocr(img_bytes)

        log.info(f"CAPTCHA solved as: '{solution}'")
        return solution

    def solve_from_response(self, resp_text: str) -> str:
        """
        Some endpoints (e.g. submit_case_type on failure) return a *fresh*
        CAPTCHA in their own 'div_captcha' field. Use this to solve that one
        without an extra getCaptcha round-trip.
        """
        img_src = parse_captcha_response(resp_text)
        img_bytes = self.client.get_image(img_src)

        if self.mode == "2captcha":
            return solve_captcha_2captcha(img_bytes, self.api_key)
        elif self.mode == "manual":
            from captcha_solver import solve_captcha_manual
            return solve_captcha_manual(img_bytes)
        return solve_captcha_ocr(img_bytes)


# ── Dropdown cascade ────────────────────────────────────────────────────────────
class ECourtDropdowns:
    def __init__(self, client: ECourtClient):
        self.client = client

    def get_districts(self, state_code: str) -> list[dict]:
        resp = self.client.post("casestatus/fillDistrict", data={"state_code": state_code})
        return parse_dropdown_options(resp, "dist_list")

    def get_complexes(self, state_code: str, dist_code: str) -> list[dict]:
        """
        Returns [{"code": "1030135@3@Y", "name": "City Civil Court Complex, Bangalore"}, ...]
        `code` is the FULL value - use complex_code_numeric() to get the numeric part.
        """
        resp = self.client.post("casestatus/fillcomplex", data={
            "state_code": state_code,
            "dist_code": dist_code,
        })
        return parse_dropdown_options(resp, "complex_list")

    def get_establishments(self, state_code: str, dist_code: str, court_complex_code: str) -> list[dict]:
        """court_complex_code = numeric part only, e.g. '1030135' (not the full '1030135@3@Y')"""
        resp = self.client.post("casestatus/fillCourtEstablishment", data={
            "state_code": state_code,
            "dist_code": dist_code,
            "court_complex_code": court_complex_code,
        })
        return parse_dropdown_options(resp, "establishment_list")

    def get_case_types(self, state_code: str, dist_code: str,
                       court_complex_code: str, est_code: str) -> list[dict]:
        """
        Returns [{"code": "12^3", "name": "C.C. - CRIMINAL CASES"}, ...]
        `code` format is "{case_type_code}^{est_code}" - send as-is in case_type_1
        (requests will URL-encode the ^ automatically).
        """
        resp = self.client.post("casestatus/fillCaseType", data={
            "state_code": state_code,
            "dist_code": dist_code,
            "court_complex_code": court_complex_code,
            "est_code": est_code,
            "search_type": "c_type",
        })
        return parse_dropdown_options(resp, "casetype_list")

    def set_data(self, full_complex_code: str, state_code: str,
                  dist_code: str, est_code: str = "") -> dict:
        """
        Persists the current selection. Called twice in the observed flow:
          1. After selecting court complex (est_code="")
          2. After selecting establishment (est_code=<chosen est>)

        full_complex_code: the FULL value from get_complexes(), e.g. "1030135@3@Y"
        """
        resp = self.client.post("casestatus/set_data", data={
            "complex_code": full_complex_code,
            "selected_state_code": state_code,
            "selected_dist_code": dist_code,
            "selected_est_code": est_code,
        })
        return unwrap_json(resp)


# ── Main scraper ────────────────────────────────────────────────────────────────
class ECourtScraper:
    """
    High-level scraper. Typical usage:

        scraper = ECourtScraper(captcha_mode="ocr")

        # 1. Explore hierarchy to find codes
        districts = scraper.list_districts("3")               # Karnataka
        complexes = scraper.list_complexes("3", "20")          # Bengaluru
        ests      = scraper.list_establishments("3", "20", "1030135")
        types     = scraper.list_case_types("3", "20", "1030135", "3")

        # 2. Search
        results = scraper.search_by_case_type(
            state_code="3", dist_code="20",
            full_complex_code="1030135@3@Y", est_code="3",
            case_type_value="12^3",   # from list_case_types()
            search_year="2024",
            case_status="Pending",    # Pending / Disposed / Both
        )
    """

    def __init__(self, captcha_mode: str = "ocr", api_key_2captcha: str = "", delay: float = 3.0):
        self.client = ECourtClient()
        self.dropdowns = ECourtDropdowns(self.client)
        self.captcha = CaptchaHandler(self.client, mode=captcha_mode, api_key=api_key_2captcha)
        self.delay = delay

    def _sleep(self):
        time.sleep(self.delay + random.uniform(0, 1))

    # ── Exploration ──────────────────────────────────────────────────────────
    def list_districts(self, state_code: str) -> list[dict]:
        return self.dropdowns.get_districts(state_code)

    def list_complexes(self, state_code: str, dist_code: str) -> list[dict]:
        return self.dropdowns.get_complexes(state_code, dist_code)

    def list_establishments(self, state_code: str, dist_code: str, court_complex_code: str) -> list[dict]:
        """court_complex_code: numeric part only (use complex_code_numeric() on a complex's 'code')"""
        return self.dropdowns.get_establishments(state_code, dist_code, court_complex_code)

    def list_case_types(self, state_code: str, dist_code: str,
                         court_complex_code: str, est_code: str) -> list[dict]:
        return self.dropdowns.get_case_types(state_code, dist_code, court_complex_code, est_code)

    # ── Search ───────────────────────────────────────────────────────────────
    def search_by_case_type(
        self,
        state_code: str,
        dist_code: str,
        full_complex_code: str,   # e.g. "1030135@3@Y" - from list_complexes()
        est_code: str,
        case_type_value: str,     # e.g. "12^3" - from list_case_types()
        search_year: str,
        case_status: str = "Pending",  # "Pending" | "Disposed" | "Both"
        max_captcha_retries: int = 3,
    ) -> list[dict]:
        """
        Replays the full confirmed flow:
          fillcomplex/establishments/casetypes are assumed already resolved
          (caller passes the chosen codes). This method does:
            set_data (complex only) -> set_data (with est_code)
            -> getCaptcha -> solve -> submit_case_type
          Retries with a fresh CAPTCHA if the server rejects it.
        """
        court_complex_code = complex_code_numeric(full_complex_code)

        # Step 1: set_data with complex selected, est not yet chosen
        log.info("set_data (complex selection)...")
        self.dropdowns.set_data(full_complex_code, state_code, dist_code, est_code="")
        self._sleep()

        # Step 2: set_data again with establishment chosen
        log.info("set_data (establishment selection)...")
        self.dropdowns.set_data(full_complex_code, state_code, dist_code, est_code=est_code)
        self._sleep()

        for attempt in range(1, max_captcha_retries + 1):
            # Step 3: fresh CAPTCHA
            captcha_text = self.captcha.fetch_and_solve()
            self._sleep()

            # Step 4: submit
            log.info(f"Submitting search (attempt {attempt}): case_type={case_type_value}, "
                     f"year={search_year}, status={case_status}")
            resp_text = self.client.post("casestatus/submit_case_type", data={
                "case_type_1": case_type_value,
                "search_year": search_year,
                "case_status": case_status,
                "ct_captcha_code": captcha_text,
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": court_complex_code,
                "est_code": est_code,
            })

            try:
                cases = parse_case_results(resp_text)
                log.info(f"Found {len(cases)} case(s)")
                return cases
            except WrongCaptchaError:
                log.warning(f"CAPTCHA rejected (attempt {attempt}/{max_captcha_retries}), retrying...")
                self._sleep()
                continue

        raise WrongCaptchaError(f"CAPTCHA failed after {max_captcha_retries} attempts")

    # ── Case detail (the "View" button) ─────────────────────────────────────
    def get_case_detail(self, view_params: dict) -> dict:
        """
        Replicates clicking "View" on a result row.

        view_params comes from a case dict returned by search_by_case_type()
        (its "view_params" key) - extracted from the row's
        onClick="viewHistory(...)" attribute. CONFIRMED endpoint: home/viewHistory.

        Returns a dict with: court_name, case_type, filing_number, filing_date,
        registration_number, registration_date, cnr_number, first_hearing_date,
        next_hearing_date, case_stage, court_number_judge, petitioners,
        respondents, acts, case_history.
        """
        log.info(f"Fetching case detail for CNR {view_params.get('cino')}...")
        resp_text = self.client.post("home/viewHistory", data={
            "court_code": view_params["court_code"],
            "state_code": view_params["state_code"],
            "dist_code": view_params["dist_code"],
            "court_complex_code": view_params["court_complex_code"],
            "case_no": view_params["case_no"],
            "cino": view_params["cino"],
            "hideparty": view_params.get("hideparty", ""),
            "search_flag": view_params["search_flag"],
            "search_by": view_params["search_by"],
        })
        return parse_case_detail(resp_text)

    def search_with_details(
        self,
        state_code: str,
        dist_code: str,
        full_complex_code: str,
        est_code: str,
        case_type_value: str,
        search_year: str,
        case_status: str = "Pending",
        max_results: int = 10,
        max_captcha_retries: int = 3,
    ) -> list[dict]:
        """
        Convenience method: runs search_by_case_type(), then calls
        get_case_detail() for up to `max_results` of the returned rows
        and merges the detail dict into each case.

        NOTE: each detail fetch is an extra HTTP request (no CAPTCHA needed
        for home/viewHistory). Use max_results to avoid hammering the server
        when a search returns thousands of rows (e.g. 3478 in our test case).
        """
        cases = self.search_by_case_type(
            state_code, dist_code, full_complex_code, est_code,
            case_type_value, search_year, case_status, max_captcha_retries,
        )

        for case in cases[:max_results]:
            if not case.get("view_params"):
                continue
            self._sleep()
            try:
                detail = self.get_case_detail(case["view_params"])
                case["detail"] = detail
            except Exception as e:
                log.warning(f"Failed to fetch detail for {case.get('case_number')}: {e}")
                case["detail"] = None

        return cases
