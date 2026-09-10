"""
Parsers for eCourts HTML/JSON responses
=========================================
CONFIRMED from HAR capture (services_ecourts_gov_in_2.har):

Every AJAX endpoint returns JSON with the HTML fragment nested inside
a named key, e.g.:
    {"dist_list": "<option value=2>BAGALKOT</option>...", "status": 1}
    {"complex_list": "<option value=1030135@3@Y >City Civil...</option>", "status": 1}
    {"establishment_list": "<option value=3>PRL...</option>", "status": 1}
    {"casetype_list": "<option value=3^3>AA - Arbitration...</option>", ...}
    {"case_data": "<div ...>Record not found</div>", "status": 1, "div_captcha": "..."}
    {"div_captcha": "<div ...><img src='/.../securimage_show.php?HASH' ...></div>"}

So parsing is two-step:
  1. json.loads(response.text) -> dict
  2. BeautifulSoup(dict[the_relevant_key], "html.parser") -> extract <option>/<table>/etc.
"""

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parsers")


# ── JSON Envelope Helper ───────────────────────────────────────────────────────
def unwrap_json(response_text: str) -> dict:
    """
    Parse the JSON envelope all eCourts AJAX endpoints return.
    Returns the parsed dict, or {} if response isn't valid JSON
    (some error pages return raw HTML instead).
    """
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        log.warning("Response was not valid JSON, returning empty dict")
        log.debug(f"Raw response: {response_text[:300]}")
        return {}


# ── Dropdown Parser ────────────────────────────────────────────────────────────
def parse_dropdown_options(response_text: str, list_key: str) -> list[dict]:
    """
    Parse a dropdown response, e.g.:
        fillDistrict        -> list_key="dist_list"
        fillcomplex         -> list_key="complex_list"
        fillCourtEstablishment -> list_key="establishment_list"
        fillCaseType        -> list_key="casetype_list"

    Returns: [{"code": "<option value>", "name": "<option text>"}, ...]

    NOTE on complex_list: values look like "1030135@3@Y"
      - 1030135 = court_complex_code (numeric, used in later requests)
      - 3       = establishment code(s) within this complex (comma-separated if multiple)
      - Y/N     = differ_mast_est flag (whether establishments differ)
    The FULL value (e.g. "1030135@3@Y") is needed for set_data's `complex_code` field.
    The numeric prefix (e.g. "1030135") is needed for `court_complex_code` in other calls.

    NOTE on casetype_list: values look like "12^3"
      - 12 = case type code
      - 3  = est_code
    The full value (URL-encoded, e.g. "12%5E3") is sent as `case_type_1`.
    """
    data = unwrap_json(response_text)
    html_fragment = data.get(list_key, "")

    if not html_fragment:
        log.warning(f"No '{list_key}' key in response. Keys present: {list(data.keys())}")
        return []

    soup = BeautifulSoup(html_fragment, "html.parser")
    options = soup.find_all("option")

    results = []
    for opt in options:
        val = opt.get("value", "").strip()
        name = opt.get_text(strip=True)
        if val and "select" not in name.lower():
            results.append({"code": val, "name": name})

    log.debug(f"Parsed {len(results)} options from '{list_key}'")
    return results


def complex_code_numeric(full_complex_code: str) -> str:
    """
    Extract the numeric court_complex_code from a full value like '1030135@3@Y'.
    Used for fillCourtEstablishment / fillCaseType's `court_complex_code` field.
    """
    return full_complex_code.split("@")[0]


# ── CAPTCHA Response Parser ─────────────────────────────────────────────────────
def parse_captcha_response(response_text: str) -> str:
    """
    Parse a getCaptcha (or submit_case_type/submit_*) response and extract
    the CAPTCHA image URL (relative path) from the 'div_captcha' key.

    Response shape:
        {"div_captcha": "<div...><img ... src='/ecourtindia_v6/vendor/securimage/securimage_show.php?HASH' .../>...</div>"}

    Returns the relative image src, e.g.:
        /ecourtindia_v6/vendor/securimage/securimage_show.php?015c0e68581bbfed870ba921f798d52f
    """
    data = unwrap_json(response_text)
    html_fragment = data.get("div_captcha", "")

    if not html_fragment:
        raise RuntimeError(f"No 'div_captcha' key found. Response keys: {list(data.keys())}")

    soup = BeautifulSoup(html_fragment, "html.parser")
    img_tag = soup.find("img", id="captcha_image") or soup.find("img", src=re.compile(r"securimage_show\.php"))

    if not img_tag:
        raise RuntimeError("Could not find captcha_image <img> tag in div_captcha")

    return img_tag.get("src", "")


# ── Case Results Parser ────────────────────────────────────────────────────────
def parse_case_results(response_text: str) -> list[dict]:
    """
    Parse the response from submit_case_type / submit_party_name / getCaseStatus.

    Response shape:
        {"case_data": "<html>...</html>", "status": 1, "div_captcha": "..."}

    The 'case_data' HTML is either:
      - "<div ... id='nodata' style='color:red'>Record not found</div>"  -> no results
      - A table of matching cases
      - "Invalid Captcha" type message -> raise WrongCaptchaError
    """
    data = unwrap_json(response_text)
    html_fragment = data.get("case_data", "")

    if not html_fragment:
        errormsg = data.get("errormsg", "")
        if "invalid captcha" in errormsg.lower():
            raise WrongCaptchaError(f"CAPTCHA rejected (errormsg): {errormsg}")
        if errormsg:
            log.warning(f"Server returned error message: {errormsg}")
        log.warning(f"No valid HTML in 'case_data'. Keys: {list(data.keys())}")
        return []

    soup = BeautifulSoup(html_fragment, "html.parser")
    text = soup.get_text()

    # No results
    if "record not found" in text.lower() or "no record" in text.lower():
        log.info("No cases found (Record not found)")
        return []

    # Wrong CAPTCHA
    if "invalid captcha" in text.lower() or "captcha" in text.lower() and "invalid" in text.lower():
        raise WrongCaptchaError("CAPTCHA was rejected by server")

    cases = []
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 1:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = [c.get_text(strip=True).lower() for c in header_cells]

        data_rows = rows[1:] if len(rows) > 1 else rows
        for row in data_rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            case = _parse_case_row(cells, headers)
            if case:
                cases.append(case)

    return cases


def _parse_case_row(cells: list, headers: list) -> Optional[dict]:
    """Map a table row's cells to a case dict, using headers where possible."""
    texts = [c.get_text(strip=True) for c in cells]
    # Also capture any links (eCourts often makes the case number a clickable
    # link that triggers a detail view via JS - the onclick attr may contain
    # the CNR or case identifiers)
    links = []
    for c in cells:
        a = c.find("a")
        if a:
            links.append({"text": a.get_text(strip=True), "onclick": a.get("onclick", ""), "href": a.get("href", "")})

    case = {
        "cnr_number": "",
        "case_number": "",
        "petitioner": "",
        "respondent": "",
        "next_hearing_date": "",
        "case_stage": "",
        "court_number_judge": "",
        "raw_cells": texts,
        "links": links,
    }

    for i, header in enumerate(headers):
        if i >= len(texts):
            break
        val = texts[i]
        if "case" in header and ("no" in header or "type" in header):
            case["case_number"] = val
        elif "petitioner" in header or "vs" in header or "party" in header:
            case["petitioner"] = val
        elif "respondent" in header:
            case["respondent"] = val
        elif "hearing" in header or "next date" in header or "date" in header:
            case["next_hearing_date"] = val
        elif "stage" in header:
            case["case_stage"] = val
        elif "court" in header or "judge" in header:
            case["court_number_judge"] = val

    # Try to extract CNR from any onclick handlers (common pattern: viewCaseDetails('CNR','...'))
    for link in links:
        onclick = link.get("onclick", "")
        cnr_match = re.search(r"[A-Z]{2}[A-Z0-9]{2,6}\d{8,12}", onclick)
        if cnr_match:
            case["cnr_number"] = cnr_match.group()
            break

    if not any([case["case_number"], case["petitioner"], case["raw_cells"]]):
        return None

    return case


# ── Custom Exceptions ──────────────────────────────────────────────────────────
class WrongCaptchaError(Exception):
    """Raised when CAPTCHA answer is rejected by the server."""
    pass
