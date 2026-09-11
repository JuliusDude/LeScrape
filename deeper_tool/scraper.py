"""
deeper_tool.scraper
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
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from deeper_tool.db import DeeperDatabase
from deeper_tool.exporter import export_to_json, export_to_csv_flat, export_to_multisheet_excel
from deeper_tool.models import DeeperCaseDetail
from deeper_tool.parser import parse_deeper_case_detail
from ecourts.core.captcha import LocalOcrSolver
from ecourts.parser.search import parse_case_results

log = logging.getLogger("deeper_tool.scraper")

DEFAULT_UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36"
BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"


class DeeperScraper:
    """Production scraper engine for deep eCourts case extraction."""

    def __init__(
        self,
        db_path: str = "deeper_tool/deeper_ecourts.db",
        cookie_file: str = "deeper_tool/cookies_session.txt",
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

        os.makedirs(os.path.dirname(self.cookie_file) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    def _wait_pacing(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.pacing:
            time.sleep(self.pacing - elapsed)

    def _curl(self, url: str, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, is_binary: bool = False) -> Any:
        self._wait_pacing()
        cmd = ["curl.exe", "-s", "-b", self.cookie_file, "-c", self.cookie_file]
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

        res = subprocess.run(cmd, capture_output=True)
        self._last_call = time.time()
        if is_binary:
            return res.stdout
        return res.stdout.decode("utf-8", errors="ignore")

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

        m_comp = re.search(r'src=["\']([^"\']*components\.js\?v=[^"\']*)', cs_html)
        comp_url = m_comp.group(1) if m_comp else "/ecourtindia_v6/js/components.js"
        if comp_url.startswith("/"):
            comp_full = f"https://services.ecourts.gov.in{comp_url}"
        else:
            comp_full = comp_url

        js_text = self._curl(comp_full, headers={"Referer": f"{BASE_URL}?p=casestatus/index"})

        m_del = re.search(r'var\s+delimeter\s*=\s*["\']([^"\']+)["\']', js_text)
        self.delimeter = m_del.group(1) if m_del else "uiryhjtry57"

        m_hdr = re.search(r'headers\s*:\s*\{[^}]*"delimeter"\s*:\s*delimeter\s*,\s*["\']([^"\']+)["\']\s*:\s*delimeter', js_text)
        self.custom_header = m_hdr.group(1) if m_hdr else None

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

        out = self._curl(f"{BASE_URL}?p=home/viewHistory", data=payload, headers=headers)
        try:
            j = json.loads(out)
            if isinstance(j, dict) and j.get("app_token"):
                self.app_token = j["app_token"]
            return j.get("data_list", "")
        except Exception as e:
            log.error("Failed to parse viewHistory: %s", e)
            return ""

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

        for attempt in range(1, max_retries + 1):
            sol = self.solve_captcha()
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
            res = self._post_api("casestatus/submit_case_type", payload)
            case_data = res.get("case_data", "")
            if "Invalid Captcha" in case_data or "Enter Captcha" in case_data:
                log.warning("Server rejected CAPTCHA '%s', retrying...", sol)
                continue

            parsed = parse_case_results(case_data)
            log.info("Search query succeeded: %d cases retrieved", len(parsed))
            return parsed

        log.error("Failed to retrieve cases after %d attempts", max_retries)
        return []

    def scrape_and_save_case(self, view_params: Dict[str, Any], meta: Optional[Dict[str, str]] = None) -> DeeperCaseDetail:
        """Fetch, parse, and persist a single case with all deep relational fields."""
        html = self.fetch_view_history(view_params)
        if not html:
            raise ValueError("Failed to retrieve viewHistory HTML")

        case = parse_deeper_case_detail(html)
        if meta:
            if meta.get("court_name") and not case.court_name:
                case.court_name = meta["court_name"]
            case.state_code = meta.get("state_code", "")
            case.dist_code = meta.get("dist_code", "")
            case.court_complex_code = meta.get("court_complex_code", "")
            case.est_code = meta.get("est_code", "")

        self.db.save_case(case)
        return case

    def batch_extract(
        self,
        cases: List[Dict[str, Any]],
        limit: int = 20,
        meta: Optional[Dict[str, str]] = None,
    ) -> List[DeeperCaseDetail]:
        """Extract and persist a batch of cases respecting the pacing guardrail."""
        target_cases = cases[:limit]
        extracted: List[DeeperCaseDetail] = []
        total = len(target_cases)

        print(f"\n[+] Beginning batch extraction for {total} cases...")
        for idx, c in enumerate(target_cases, start=1):
            vp = c.get("view_params")
            case_no = c.get("case_number", f"Case #{idx}")
            cnr_val = c.get("cnr_number") or (vp.get("cino") if vp else "Unknown")
            print(f"    [{idx:2d}/{total}] Fetching {case_no} ({cnr_val})... ", end="", flush=True)

            if not vp:
                print("SKIPPED (no view_params)")
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

        print(f"\n[+] Batch complete! Successfully extracted {len(extracted)}/{total} cases.")
        return extracted

