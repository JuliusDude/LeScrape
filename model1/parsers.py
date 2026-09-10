"""
Parsers for eCourts HTML responses
====================================
All endpoints return text/html (not JSON), so we parse with BeautifulSoup.

NOTE: eCourts HTML structure may change when they release ecourtindia_v7+.
      Keep selectors in one place (here) so updates are easy.
"""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parsers")


# ── Selector Config (update here if site changes) ─────────────────────────────
SELECTORS = {
    # Dropdown <option> tags
    "dropdown_option": "option",

    # Case results table
    "results_table": "table.case_details, table#resultTable, table.table-bordered",

    # Individual case row
    "case_row": "tr",

    # Case detail sections (in case detail page)
    "case_detail_section": "table.case_details",

    # CNR number
    "cnr_pattern": r"[A-Z]{2}[A-Z0-9]{2,4}\d{9,12}",
}


# ── Dropdown Parser ────────────────────────────────────────────────────────────
def parse_dropdown_options(html: str, value_attr: str = "value") -> list[dict]:
    """
    Parse <select><option value="X">Name</option></select> responses.
    Returns: [{"code": "X", "name": "District Name"}, ...]

    eCourts dropdown responses are HTML fragments like:
      <option value="">--Select--</option>
      <option value="1">Ahmednagar</option>
      ...
    """
    soup = BeautifulSoup(html, "html.parser")
    options = soup.find_all("option")

    results = []
    for opt in options:
        val = opt.get("value", "").strip()
        name = opt.get_text(strip=True)
        if val and val not in ("", "0", "--Select--") and name and name != "--Select--":
            results.append({"code": val, "name": name})

    log.debug(f"Parsed {len(results)} dropdown options")
    return results


# ── Case Results Parser ────────────────────────────────────────────────────────
def parse_case_results(html: str) -> list[dict]:
    """
    Parse the HTML response from submit_case_type or getCaseStatus.

    eCourts returns a table with case rows. Structure varies slightly
    by search type but generally has these columns:
      - Sr No
      - Case Type & Number
      - Party Name
      - Court Name & Number
      - Next Hearing Date
      - Case Status
    """
    soup = BeautifulSoup(html, "html.parser")

    # Check for "no results" indicators
    text = soup.get_text()
    if any(phrase in text.lower() for phrase in [
        "no record found", "no case found", "record not found", "0 records"
    ]):
        log.info("No cases found in response")
        return []

    # Check for wrong CAPTCHA
    if any(phrase in text.lower() for phrase in [
        "invalid captcha", "wrong captcha", "captcha does not match"
    ]):
        raise WrongCaptchaError("CAPTCHA was incorrect")

    cases = []

    # Try parsing as a results table
    tables = soup.select("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Detect header row
        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
        if not any(h in " ".join(headers) for h in ["case", "party", "hearing", "cnr"]):
            continue

        log.debug(f"Found results table with headers: {headers}")

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            case = _parse_case_row(cells, headers)
            if case:
                cases.append(case)

    # Fallback: try parsing case detail page (single case view)
    if not cases:
        case = parse_case_detail(soup)
        if case:
            cases.append(case)

    return cases


def _parse_case_row(cells: list, headers: list) -> Optional[dict]:
    """Parse a single table row into a case dict."""
    try:
        texts = [c.get_text(strip=True) for c in cells]

        case = {
            "cnr_number": "",
            "case_type": "",
            "case_number": "",
            "filing_number": "",
            "registration_number": "",
            "petitioner": "",
            "respondent": "",
            "next_hearing_date": "",
            "court": "",
            "judge": "",
            "status": "",
            "raw_cells": texts,  # keep raw for debugging
        }

        # Map by header names where available
        for i, header in enumerate(headers):
            if i >= len(texts):
                break
            val = texts[i]
            if "cnr" in header:
                case["cnr_number"] = val
            elif "case type" in header or "case no" in header:
                case["case_number"] = val
            elif "party" in header or "petitioner" in header:
                case["petitioner"] = val
            elif "respondent" in header:
                case["respondent"] = val
            elif "hearing" in header or "next date" in header:
                case["next_hearing_date"] = val
            elif "court" in header:
                case["court"] = val
            elif "status" in header:
                case["status"] = val

        # Extract CNR from any cell if not found by header
        if not case["cnr_number"]:
            all_text = " ".join(texts)
            cnr_match = re.search(SELECTORS["cnr_pattern"], all_text)
            if cnr_match:
                case["cnr_number"] = cnr_match.group()

        # Skip empty rows
        if not any([case["cnr_number"], case["case_number"], case["petitioner"]]):
            return None

        return case

    except Exception as e:
        log.debug(f"Error parsing row: {e}")
        return None


def parse_case_detail(soup: BeautifulSoup) -> Optional[dict]:
    """
    Parse a detailed single-case view (e.g. from CNR lookup).
    These pages have labelled table rows like "Case Type: ... | Case No: ..."
    """
    case = {}

    # Look for label: value pairs in tables
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower().rstrip(":")
            value = cells[1].get_text(strip=True)

            if "cnr" in label:
                case["cnr_number"] = value
            elif "case type" in label:
                case["case_type"] = value
            elif "case no" in label or "case number" in label:
                case["case_number"] = value
            elif "filing number" in label:
                case["filing_number"] = value
            elif "registration" in label:
                case["registration_number"] = value
            elif "petitioner" in label or "appellant" in label:
                case["petitioner"] = value
            elif "respondent" in label:
                case["respondent"] = value
            elif "next hearing" in label or "next date" in label:
                case["next_hearing_date"] = value
            elif "court" in label:
                case["court"] = value
            elif "judge" in label:
                case["judge"] = value
            elif "status" in label:
                case["status"] = value

    return case if case else None


# ── Custom Exceptions ──────────────────────────────────────────────────────────
class WrongCaptchaError(Exception):
    """Raised when CAPTCHA answer is rejected by the server."""
    pass
