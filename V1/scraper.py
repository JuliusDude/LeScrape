"""
V1.scraper
===================
Live eCourts Scraper Engine equipped with dynamic anti-scraping session handshake,
CAPTCHA auto-solving, and deep record extraction for Execution cases.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from V1.db import DeeperDatabase
from V1.exporter import export_to_json, export_to_csv_flat, export_to_multisheet_excel
from V1.models import DeeperCaseDetail
from V1.parser import parse_deeper_case_detail
from V1.captcha import LocalOcrSolver
from V1.search import WrongCaptchaError, parse_case_results

log = logging.getLogger("V1.scraper")

DEFAULT_UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36"
BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"


TRANSIENT_CURL_CODES = frozenset({6, 7, 28, 35, 52, 55, 56})


class ScraperHTTPError(Exception):
    """Transport/HTTP failure from a curl call."""

    def __init__(self, message: str, url: str = "", http_status: Optional[int] = None,
                 returncode: Optional[int] = None, transient: bool = False) -> None:
        super().__init__(message)
        self.url = url
        self.http_status = http_status
        self.returncode = returncode
        self.transient = transient


class CnrMismatchError(ValueError):
    """Parsed CNR differs from the CNR that was requested."""


class DeeperScraper:
    """Production scraper engine for deep eCourts case extraction."""

    def __init__(
        self,
        db_path: str = "V1/deeper_ecourts.db",
        cookie_file: str = "V1/cookies_session.txt",
        user_agent: str = DEFAULT_UA,
        pacing: float = 2.0,
    ) -> None:
        self.db = DeeperDatabase(db_path)
        self.cookie_file = cookie_file
        self.user_agent = user_agent
        self.pacing = max(1.0, float(pacing))
        self.app_token = ""
        self.delimeter = ""
        self.custom_header = ""
        self.solver = LocalOcrSolver()
        self._last_call = 0.0
        self.run_id = uuid.uuid4().hex
        self.last_http_status: Optional[int] = None
        self.last_raw_body: str = ""
        self.last_raw_response_id: Optional[int] = None
        self.last_run_summary: Dict[str, Any] = {}

        os.makedirs(os.path.dirname(self.cookie_file) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    def _wait_pacing(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.pacing:
            time.sleep(self.pacing - elapsed)

    def _curl(self, url: str, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, is_binary: bool = False) -> Any:
        self._wait_pacing()
        cmd = ["curl.exe", "-s", "-b", self.cookie_file, "-c", self.cookie_file,
               "--connect-timeout", "15", "--max-time", "60", "-w", "\\n%{http_code}"]
        h = {
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            h.update(headers)
        for k, v in h.items():
            cmd.extend(["-H", f"{k}: {v}"])
        if data is not None:
            cmd.extend(["-X", "POST"])
            cmd.extend(["-d", urllib.parse.urlencode(data)])
        cmd.append(url)

        try:
            res = subprocess.run(cmd, capture_output=True, timeout=75)
        except subprocess.TimeoutExpired as ex:
            self._last_call = time.time()
            raise ScraperHTTPError(f"timeout calling {url}", url=url, transient=True) from ex
        self._last_call = time.time()
        if res.returncode != 0:
            raise ScraperHTTPError(
                f"curl exit code {res.returncode} for {url}", url=url,
                returncode=res.returncode, transient=res.returncode in TRANSIENT_CURL_CODES,
            )
        body, sep, code = res.stdout.rpartition(b"\n")
        if not sep:
            body, code = res.stdout, b""
        try:
            status: Optional[int] = int(code.strip())
        except ValueError:
            status = None
            body = res.stdout
        if status is not None and 300 <= status < 400:
            # curl runs without -L: redirect bodies were always returned; keep that.
            log.warning("HTTP %s redirect for %s (not followed); returning body", status, url)
        elif status is not None and status >= 400:
            raise ScraperHTTPError(
                f"HTTP {status} for {url}", url=url, http_status=status,
                transient=(status == 429 or status >= 500),
            )
        self.last_http_status = status
        if is_binary:
            return body
        text = body.decode("utf-8", errors="ignore")
        self.last_raw_body = text
        return text

    def init_session(self) -> None:
        """Perform dynamic session handshake:
        1. GET casestatus/index -> capture cookies, initial app_token, and versioned components.js URL
        2. GET components.js?v=... -> extract server-assigned dynamic delimeter and custom header
        """
        log.info("Initializing session handshake with eCourts portal...")
        self._curl(BASE_URL, headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"})

        cs_html = self._curl(
            f"{BASE_URL}?p=casestatus/index",
            headers={"Referer": BASE_URL, "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"}
        )

        m_token = re.search(r'id=["\']app_token["\'][^>]*value=["\']([^"\']+)', cs_html)
        self.app_token = m_token.group(1) if m_token else ""
        if not m_token:
            log.warning("init_session: app_token not found in casestatus/index; using empty token")

        m_comp = re.search(r'src=["\']([^"\']*components\.js\?v=[^"\']*)', cs_html)
        comp_url = m_comp.group(1) if m_comp else "/ecourtindia_v6/js/components.js"
        if not m_comp:
            log.warning("init_session: components.js URL not found; using default path")
        if comp_url.startswith("/"):
            comp_full = f"https://services.ecourts.gov.in{comp_url}"
        else:
            comp_full = comp_url

        js_text = self._curl(comp_full, headers={"Referer": f"{BASE_URL}?p=casestatus/index"})

        m_del = re.search(r'var\s+delimeter\s*=\s*["\']([^"\']+)["\']', js_text)
        self.delimeter = m_del.group(1) if m_del else "uiryhjtry57"
        if not m_del:
            log.warning("init_session: delimeter not found in components.js; using fallback value")

        m_hdr = re.search(r'headers\s*:\s*\{[^}]*"delimeter"\s*:\s*delimeter\s*,\s*["\']([^"\']+)["\']\s*:\s*delimeter', js_text)
        self.custom_header = m_hdr.group(1) if m_hdr else None
        if not m_hdr:
            log.warning("init_session: custom header not found in components.js; none will be sent")

        log.info("Session established: app_token=%s..., delimeter=%s, custom_header=%s", self.app_token[:14], self.delimeter, self.custom_header)

    def _post_api(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://services.ecourts.gov.in",
            "Referer": f"{BASE_URL}?p=casestatus/index",
            "X-Requested-With": "XMLHttpRequest",
            "delimeter": self.delimeter,
        }
        if self.custom_header:
            headers[self.custom_header] = self.delimeter

        payload = {"ajax_req": "true", "app_token": self.app_token}
        payload.update(data)

        out = self._curl(f"{BASE_URL}?p={endpoint}", data=payload, headers=headers)
        try:
            j = json.loads(out)
            if isinstance(j, dict) and j.get("app_token"):
                self.app_token = j["app_token"]
            return j
        except Exception as e:
            log.error("Failed to parse %s JSON: %s -> %s", endpoint, e, out[:150])
            self.db.record_error(self.run_id, endpoint, None, "UnexpectedResponse", out[:300])
            return {}

    def solve_captcha(self) -> str:
        """Fetch and solve CAPTCHA using LocalOcrSolver."""
        cap_resp = self._post_api("casestatus/getCaptcha", {})
        div_cap = cap_resp.get("div_captcha", "")
        soup = BeautifulSoup(div_cap, "html.parser")
        img_tag = soup.find("img")
        if not img_tag or not img_tag.get("src"):
            return ""
        src = img_tag["src"].replace("\\/", "/").replace("\\", "")
        if src.startswith("/"):
            src = f"https://services.ecourts.gov.in{src}"

        img_bytes = self._curl(src, headers={"Referer": f"{BASE_URL}?p=casestatus/index"}, is_binary=True)
        sol = self.solver.solve(img_bytes)
        return sol

    def set_establishment(self, complex_code: str, state_code: str, dist_code: str, est_code: str) -> None:
        """Set active court complex and establishment."""
        self._post_api("casestatus/set_data", {
            "complex_code": f"{complex_code}@{est_code}@Y",
            "selected_state_code": state_code,
            "selected_dist_code": dist_code,
            "selected_est_code": est_code,
        })

    def fetch_view_history(self, view_params: Dict[str, Any]) -> str:
        """Fetch viewHistory HTML fragment for a given case."""
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://services.ecourts.gov.in",
            "Referer": f"{BASE_URL}?p=casestatus/index",
            "X-Requested-With": "XMLHttpRequest",
            "delimeter": self.delimeter,
        }
        if self.custom_header:
            headers[self.custom_header] = self.delimeter

        payload = dict(view_params)
        payload["ajax_req"] = "true"
        payload["app_token"] = self.app_token

        cnr = view_params.get("cino")
        out = None
        for attempt in range(1, 4):
            try:
                out = self._curl(f"{BASE_URL}?p=home/viewHistory", data=payload, headers=headers)
                break
            except ScraperHTTPError as ex:
                if ex.transient and attempt < 3:
                    log.warning("viewHistory attempt %d failed (%s); retrying", attempt, ex)
                    time.sleep(2 * attempt)
                    continue
                log.error("viewHistory failed: %s", ex)
                self.db.record_error(self.run_id, "home/viewHistory", cnr, "ScraperHTTPError", str(ex),
                                     http_status=ex.http_status)
                return ""
        raw_id = self.db.save_raw_response("home/viewHistory", payload, out, self.last_http_status, cnr_number=cnr)
        self.last_raw_response_id = raw_id
        try:
            j = json.loads(out)
            if isinstance(j, dict) and j.get("app_token"):
                self.app_token = j["app_token"]
            html = j.get("data_list")
        except Exception as e:
            log.error("Failed to parse viewHistory: %s", e)
            self.db.record_error(self.run_id, "home/viewHistory", cnr, "ParseError", str(e)[:300],
                                 http_status=self.last_http_status, raw_response_id=raw_id)
            return ""
        if html is None:
            self.db.record_error(self.run_id, "home/viewHistory", cnr, "MissingDataList",
                                 out[:300], http_status=self.last_http_status, raw_response_id=raw_id)
            return ""
        return html

    def search_cases(
        self,
        case_type: str,
        search_year: str,
        case_status: str = "Disposed",
        complex_code: str = "1030135",
        state_code: str = "3",
        dist_code: str = "20",
        est_code: str = "3",
        max_retries: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search case status endpoint and return parsed cases."""
        self.set_establishment(complex_code=complex_code, state_code=state_code, dist_code=dist_code, est_code=est_code)

        run_row = self.db.create_search_run(
            run_id=self.run_id, state_code=state_code, dist_code=dist_code,
            court_complex_code=complex_code, est_code=est_code, case_type=case_type,
            search_year=search_year, case_status=case_status,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        attempts = 0

        def _finish(outcome: str, rows: int = 0, raw_id: Optional[int] = None) -> None:
            self.db.finish_search_run(
                run_row, finished_at=datetime.now(timezone.utc).isoformat(), attempts=attempts,
                row_count=rows, outcome=outcome, raw_response_id=raw_id,
            )

        for attempt in range(1, max_retries + 1):
            attempts = attempt
            try:
                sol = self.solve_captcha()
            except ScraperHTTPError as ex:
                self.db.record_error(self.run_id, "casestatus/getCaptcha", None, "ScraperHTTPError", str(ex), http_status=ex.http_status)
                continue
            if not sol:
                log.warning("CAPTCHA solve attempt %d returned empty, retrying...", attempt)
                continue

            log.info("Attempt %d/%d: solved CAPTCHA '%s', submitting search query...", attempt, max_retries, sol)
            payload = {
                "case_type_1": case_type,
                "search_year": search_year,
                "case_status": case_status,
                "ct_captcha_code": sol,
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": complex_code,
                "est_code": est_code,
            }
            self.last_raw_body = ""
            try:
                res = self._post_api("casestatus/submit_case_type", payload)
            except ScraperHTTPError as ex:
                self.db.record_error(self.run_id, "casestatus/submit_case_type", None, "ScraperHTTPError", str(ex), http_status=ex.http_status)
                continue
            raw_id = self.db.save_raw_response(
                "casestatus/submit_case_type", payload, self.last_raw_body, self.last_http_status
            )
            case_data = str(res.get("case_data", "") or "")
            errormsg = str(res.get("errormsg", "") or "")
            low = case_data.lower()
            if "captcha" in errormsg.lower() or "invalid captcha" in low or "enter captcha" in low:
                log.warning("Server rejected CAPTCHA '%s', retrying...", sol)
                self.db.record_error(self.run_id, "casestatus/submit_case_type", None, "CaptchaRejected",
                                     errormsg or case_data[:300], http_status=self.last_http_status, raw_response_id=raw_id)
                continue

            try:
                parsed = parse_case_results(res)
            except WrongCaptchaError as ex:
                self.db.record_error(self.run_id, "casestatus/submit_case_type", None, "CaptchaRejected",
                                     str(ex), http_status=self.last_http_status, raw_response_id=raw_id)
                continue
            if errormsg and not case_data:
                self.db.record_error(self.run_id, "casestatus/submit_case_type", None, "SearchError",
                                     errormsg, http_status=self.last_http_status, raw_response_id=raw_id)
                _finish("error", 0, raw_id)
                return []

            self.db.add_search_results(run_row, parsed)
            _finish("ok" if parsed else "empty", len(parsed), raw_id)
            log.info("Search query succeeded: %d cases retrieved", len(parsed))
            return parsed

        log.error("Failed to retrieve cases after %d attempts", max_retries)
        _finish("captcha_failed")
        return []

    def scrape_and_save_case(self, view_params: Dict[str, Any], meta: Optional[Dict[str, str]] = None) -> DeeperCaseDetail:
        """Fetch, parse, and persist a single case with all deep relational fields."""
        html = self.fetch_view_history(view_params)
        if not html:
            raise ValueError("Failed to retrieve viewHistory HTML")

        case = parse_deeper_case_detail(html)
        cino = str(view_params.get("cino") or "")
        if cino and case.cnr_number != cino:
            msg = f"parsed {case.cnr_number} != requested {cino}"
            self.db.record_error(self.run_id, "home/viewHistory", cino, "CnrMismatch", msg,
                                 raw_response_id=self.last_raw_response_id)
            raise CnrMismatchError(msg)
        meta = meta or {}
        if meta.get("court_name") and not case.court_name:
            case.court_name = meta["court_name"]
        for key in ("state_code", "dist_code", "court_complex_code", "est_code"):
            setattr(case, key, meta.get(key) or str(view_params.get(key) or ""))
        case.view_params_json = json.dumps(view_params, sort_keys=True)
        case.source_response_id = self.last_raw_response_id

        self.db.save_case(case)
        return case

    def batch_extract(
        self,
        cases: List[Dict[str, Any]],
        limit: int = 20,
        meta: Optional[Dict[str, str]] = None,
        skip_existing: bool = False,
    ) -> List[DeeperCaseDetail]:
        """Extract and persist a batch of cases respecting the pacing guardrail."""
        self.run_id = uuid.uuid4().hex
        target_cases = cases[:limit]
        extracted: List[DeeperCaseDetail] = []
        total = len(target_cases)
        failed = 0
        skipped = 0
        errors_by_class: Dict[str, int] = {}

        print(f"\n[+] Beginning batch extraction for {total} cases...")
        for idx, c in enumerate(target_cases, start=1):
            vp = c.get("view_params")
            case_no = c.get("case_number", f"Case #{idx}")
            cnr_val = c.get("cnr_number") or (vp.get("cino") if vp else "Unknown")
            print(f"    [{idx:2d}/{total}] Fetching {case_no} ({cnr_val})... ", end="", flush=True)

            if not vp:
                print("SKIPPED (no view_params)")
                skipped += 1
                self.db.record_error(self.run_id, "home/viewHistory", cnr_val if cnr_val != "Unknown" else None,
                                     "NoViewParams", "no view_params")
                continue

            cino = vp.get("cino")
            if skip_existing and cino and self.db.get_case(cino) is not None and self.db.has_raw_response(cino):
                print("SKIPPED (already extracted)")
                skipped += 1
                continue

            try:
                t0 = time.time()
                case = self.scrape_and_save_case(vp, meta=meta)
                elapsed = time.time() - t0
                extracted.append(case)
                h_cnt = len(case.hearings)
                p_cnt = len(case.processes)
                m_cnt = len(case.main_matters)
                o_cnt = len(case.orders)
                print(f"OK ({elapsed:.2f}s) [Stage: {case.nature_of_disposal or case.case_status} | Hearings: {h_cnt} | Proc: {p_cnt} | Main: {m_cnt} | Orders: {o_cnt}]")
            except Exception as ex:
                print(f"ERROR: {ex}")
                failed += 1
                cls = type(ex).__name__
                errors_by_class[cls] = errors_by_class.get(cls, 0) + 1
                if not isinstance(ex, CnrMismatchError):  # mismatch already recorded
                    self.db.record_error(self.run_id, "home/viewHistory", cnr_val, cls, str(ex))

        self.last_run_summary = {
            "run_id": self.run_id, "total": total, "ok": len(extracted),
            "failed": failed, "skipped": skipped, "errors_by_class": errors_by_class,
        }
        # "Main Filing No." is a session-scoped site artefact: the same value is rendered for
        # every case in a session. Keep the parsed values verbatim, but flag the run.
        by_filing: Dict[str, set] = {}
        for case in extracted:
            for mm in case.main_matters:
                if mm.main_filing_number and mm.main_cnr_number:
                    by_filing.setdefault(mm.main_filing_number, set()).add(mm.main_cnr_number)
        for filing_no, main_cnrs in by_filing.items():
            if len(main_cnrs) >= 2:
                log.warning(
                    "main_filing_number %r is shared by %d different main CNRs in one run; "
                    "it looks like a session-scoped site artefact, not real data",
                    filing_no, len(main_cnrs))
                self.last_run_summary["suspect_main_filing_number"] = filing_no
                break
        print(f"\n[+] Batch complete! Successfully extracted {len(extracted)}/{total} cases.")
        print(f"[+] Run summary: {json.dumps(self.last_run_summary)}")
        return extracted
