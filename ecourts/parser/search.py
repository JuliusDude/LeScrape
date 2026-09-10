"""
ecourts.parser.search
=====================
Search form submission payload builder and search result table parser.
Extracts 9 positional parameters from viewHistory(...) onclick handlers,
extracts 16-character CNR numbers, and identifies CAPTCHA rejection responses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parser.search")


class WrongCaptchaError(Exception):
    """Raised when the eCourts server rejects the submitted CAPTCHA solution."""
    pass


# 9 Positional Arguments in eCourts viewHistory(...) onclick handler:
# 1: case_no (court internal sequence / filing number)
# 2: cino (16-character CNR number)
# 3: court_code (establishment / court hall number)
# 4: hideparty (flag for concealing party names)
# 5: search_flag (e.g. 'CScaseNumber')
# 6: state_code
# 7: dist_code
# 8: court_complex_code
# 9: search_by (e.g. 'CScaseType')
VIEWHISTORY_RE = re.compile(
    r"viewHistory\(\s*(\d+)\s*,\s*'([^']*)'\s*,\s*(\d+)\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,"
    r"\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']*)'\s*\)"
)


def build_search_payload(
    state_code: str,
    dist_code: str,
    court_complex_code: str,
    est_code: str,
    case_type_value: str,
    search_year: str,
    case_status: str = "Pending",
    captcha_code: str = "",
) -> Dict[str, str]:
    """Builds POST form data for casestatus/submit_case_type according to verified HAR format."""
    # Ensure court_complex_code is numeric prefix only
    if "@" in court_complex_code:
        court_complex_code = court_complex_code.split("@")[0]

    return {
        "case_type_1": case_type_value,
        "search_year": str(search_year),
        "case_status": case_status,
        "ct_captcha_code": captcha_code,
        "state_code": str(state_code),
        "dist_code": str(dist_code),
        "court_complex_code": str(court_complex_code),
        "est_code": str(est_code),
        "ajax_req": "true",
        "app_token": "",
    }


def parse_view_params(onclick_attr: str) -> Optional[Dict[str, str]]:
    """Parse a viewHistory(...) onclick string into a dictionary of arguments

    required for the home/viewHistory endpoint.
    """
    if not onclick_attr:
        return None

    m = VIEWHISTORY_RE.search(onclick_attr)
    if not m:
        return None

    return {
        "case_no": m.group(1),
        "cino": m.group(2),
        "court_code": m.group(3),
        "hideparty": m.group(4),
        "search_flag": m.group(5),
        "state_code": m.group(6),
        "dist_code": m.group(7),
        "court_complex_code": m.group(8),
        "search_by": m.group(9),
    }


def extract_cnr_from_onclick(onclick_attr: str) -> Optional[str]:
    """Extracts the 16-character CNR number from an onclick attribute string."""
    params = parse_view_params(onclick_attr)
    if params and params.get("cino"):
        return params["cino"]

    # Fallback to direct CNR regex search
    m = re.search(r"\b[A-Z]{4}\d{12}\b", onclick_attr)
    if m:
        return m.group(0)
    m16 = re.search(r"\b[A-Z0-9]{16}\b", onclick_attr)
    if m16:
        return m16.group(0)
    return None


def parse_case_results(response_data: Union[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse casestatus/submit_case_type response.

    Detects CAPTCHA rejection (raises WrongCaptchaError),
    empty search ('Record not found' -> []),
    and extracts all search result rows.
    """
    data: Dict[str, Any] = {}
    html_fragment = ""

    if isinstance(response_data, dict):
        data = response_data
        html_fragment = str(data.get("case_data", ""))
    elif isinstance(response_data, str):
        trimmed = response_data.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                html_fragment = str(data.get("case_data", ""))
            except json.JSONDecodeError:
                html_fragment = response_data
        else:
            html_fragment = response_data

    # Check for CAPTCHA error in JSON error messages
    errormsg = str(data.get("errormsg", ""))
    if "invalid captcha" in errormsg.lower():
        raise WrongCaptchaError(f"CAPTCHA rejected by server (errormsg): {errormsg}")

    if not html_fragment:
        if errormsg:
            log.warning("Server returned error message without case_data: %s", errormsg)
        return []

    # Check for CAPTCHA error in HTML text
    if "invalid captcha" in html_fragment.lower():
        raise WrongCaptchaError("CAPTCHA rejected by server (in case_data)")

    # Check for 'Record not found'
    if "record not found" in html_fragment.lower() or "no record" in html_fragment.lower():
        log.info("eCourts search returned: Record not found")
        return []

    # Normalize malformed closing </br> tags before parsing
    html_fragment = re.sub(r"</br\s*>", "<br/> ", html_fragment, flags=re.IGNORECASE)
    soup = BeautifulSoup(html_fragment, "html.parser")

    # The table has multiple IDs in live HTML (id='dispTable' id='titlehid').
    # Finding by CSS class 'table-fixed' is guaranteed safe across parser engines.
    table = soup.find("table", class_="table-fixed") or soup.find("table")
    if not table:
        log.debug("No result table found in HTML fragment")
        return []

    cases: List[Dict[str, Any]] = []
    current_court_name = ""

    for row in table.find_all("tr"):
        # Header row containing Court Name: <th colspan="3" id="td_court_name_...">PRL. JUDGE...</th>
        th = row.find("th")
        if th and th.get("colspan"):
            current_court_name = th.get_text(strip=True)
            continue

        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        sr_no = cells[0].get_text(strip=True)
        case_number = cells[1].get_text(strip=True)

        # Party cell typically has invalid markup: "PETITIONER<br>Vs</br>RESPONDENT"
        party_raw = cells[2].get_text(separator="|", strip=True)
        clean_party_text = party_raw.replace("|", " ")
        # Ensure separator space if 'Vs' was concatenated with respondent
        clean_party_text = re.sub(
            r"\b(vs\.?|versus|v/?s)(?=[A-Z])", r"\1 ", clean_party_text, flags=re.IGNORECASE
        )

        # Split only on isolated "vs", "vs.", "versus", "v/s" surrounded by whitespace
        parts = re.split(
            r"\s+(?:vs\.?|versus|v/?s)\s+", clean_party_text, flags=re.IGNORECASE, maxsplit=1
        )
        if len(parts) == 2:
            petitioner = parts[0].strip()
            respondent = parts[1].strip()
        else:
            petitioner = clean_party_text.strip()
            respondent = ""

        # View link
        view_params: Optional[Dict[str, str]] = None
        cnr_number = ""
        link = cells[3].find("a")
        if link:
            onclick = link.get("onClick") or link.get("onclick") or ""
            view_params = parse_view_params(onclick)
            cnr_number = extract_cnr_from_onclick(onclick) or ""

        cases.append({
            "sr_no": sr_no,
            "case_number": case_number,
            "petitioner": petitioner,
            "respondent": respondent,
            "court_name": current_court_name,
            "cnr_number": cnr_number,
            "view_params": view_params,
            "raw_cells": [c.get_text(strip=True) for c in cells],
        })

    log.debug("Successfully parsed %d case rows from search results", len(cases))
    return cases
