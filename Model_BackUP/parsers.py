"""
Parsers for eCourts HTML/JSON responses
=========================================
CONFIRMED from two HAR captures:
  - services_ecourts_gov_in_2.har  (dropdowns + captcha + empty search)
  - services_ecourts_gov_in_4.har  (real results + case detail view)

Every AJAX endpoint returns JSON with the HTML fragment nested inside a
named key:
    {"dist_list": "<option ...>...", "status": 1}
    {"complex_list": "<option value=CCC@EST@Y>...", "status": 1}
    {"establishment_list": "<option ...>...", "status": 1}
    {"casetype_list": "<option value=CT^EST>...", ...}
    {"case_data": "<table ...>...</table> or 'Record not found'", "div_captcha": "..."}
    {"div_captcha": "<div ...><img id=captcha_image src=securimage_show.php?HASH ...></div>"}
    {"data_list": "<h2>...case detail html...</h2>"}   <- from home/viewHistory

Results table rows look like:
    <tr><td>1</td><td>M.C./1/2025</td><td>XXXXXXX<br>Vs</br>XXXXXXX</td>
        <td><a ... onClick="viewHistory(204600000012025,'KABC0E0000012025',
                 15,'','CScaseNumber',3,20,1030136,'CScaseType')">View</a></td></tr>

The 9 positional args to viewHistory() map directly onto the form fields
for the home/viewHistory endpoint:
    case_no, cino, court_code, hideparty, search_flag,
    state_code, dist_code, court_complex_code, search_by
"""

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parsers")


# ── JSON Envelope Helper ───────────────────────────────────────────────────────
def unwrap_json(response_text: str) -> dict:
    """Parse the JSON envelope all eCourts AJAX endpoints return."""
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        log.warning("Response was not valid JSON, returning empty dict")
        log.debug(f"Raw response: {response_text[:300]}")
        return {}


# ── Dropdown Parser ────────────────────────────────────────────────────────────
def parse_dropdown_options(response_text: str, list_key: str) -> list[dict]:
    """
    Parse a dropdown response:
        fillDistrict           -> list_key="dist_list"
        fillcomplex             -> list_key="complex_list"
        fillCourtEstablishment  -> list_key="establishment_list"
        fillCaseType            -> list_key="casetype_list"

    Returns: [{"code": "<option value>", "name": "<option text>"}, ...]
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
    """Extract numeric court_complex_code from a full value like '1030135@3@Y'."""
    return full_complex_code.split("@")[0]


# ── CAPTCHA Response Parser ─────────────────────────────────────────────────────
def parse_captcha_response(response_text: str) -> str:
    """Extract the CAPTCHA image src (relative path) from a 'div_captcha' key."""
    data = unwrap_json(response_text)
    html_fragment = data.get("div_captcha", "")

    if not html_fragment:
        raise RuntimeError(f"No 'div_captcha' key found. Response keys: {list(data.keys())}")

    soup = BeautifulSoup(html_fragment, "html.parser")
    img_tag = soup.find("img", id="captcha_image") or soup.find("img", src=re.compile(r"securimage_show\.php"))

    if not img_tag:
        raise RuntimeError("Could not find captcha_image <img> tag in div_captcha")

    return img_tag.get("src", "")


# ── viewHistory onclick parser ──────────────────────────────────────────────────
VIEWHISTORY_RE = re.compile(
    r"viewHistory\(\s*(\d+)\s*,\s*'([^']*)'\s*,\s*(\d+)\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,"
    r"\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']*)'\s*\)"
)


def parse_view_params(onclick_attr: str) -> Optional[dict]:
    """
    Parse a viewHistory(...) onclick attribute into the form fields needed
    for the home/viewHistory endpoint.

    Example onclick:
        viewHistory(204600000012025,'KABC0E0000012025',15,'','CScaseNumber',3,20,1030136,'CScaseType')

    Returns:
        {
            "case_no": "204600000012025",
            "cino": "KABC0E0000012025",
            "court_code": "15",
            "hideparty": "",
            "search_flag": "CScaseNumber",
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030136",
            "search_by": "CScaseType",
        }
    """
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


# ── Case Results (list) Parser ───────────────────────────────────────────────────
def parse_case_results(response_text: str) -> list[dict]:
    """
    Parse the response from submit_case_type.

    Response shape:
        {"case_data": "<html>...</html>", "status": 1, "div_captcha": "..."}

    'case_data' is either:
      - "<div ... id='nodata' ...>Record not found</div>"  -> []
      - A '#dispTable' table with rows: Sr No | Case Type/Number/Year |
        Petitioner vs Respondent | View (link with onClick=viewHistory(...))

    Returns a list of dicts:
        {
            "sr_no": "1",
            "case_number": "M.C./1/2025",
            "petitioner": "XXXXXXX",
            "respondent": "XXXXXXX",
            "court_name": "PRL. JUDGE FAMILY COURT, BENGALURU",
            "view_params": {...}  # for get_case_detail()
        }
    """
    data = unwrap_json(response_text)
    html_fragment = data.get("case_data", "")

    if not html_fragment:
        log.warning(f"No 'case_data' key in response. Keys: {list(data.keys())}")
        return []

    soup = BeautifulSoup(html_fragment, "html.parser")
    text = soup.get_text()

    if "record not found" in text.lower() or "no record" in text.lower():
        log.info("No cases found (Record not found)")
        return []

    if "invalid captcha" in text.lower():
        raise WrongCaptchaError("CAPTCHA was rejected by server")

    # NOTE: the real markup has TWO id attributes on this table
    # (id='dispTable' ... id='titlehid') - BeautifulSoup keeps only one
    # (observed as 'titlehid'), so match by class instead.
    table = soup.find("table", class_="table-fixed") or soup.find("table")
    if not table:
        log.warning("No results table found in case_data")
        return []

    cases = []
    current_court_name = ""

    for row in table.find_all("tr"):
        # Court-name header rows: <tr><th colspan='3' id='td_court_name_XX'>NAME</th></tr>
        th = row.find("th")
        if th and th.get("colspan"):
            current_court_name = th.get_text(strip=True)
            continue

        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        sr_no = cells[0].get_text(strip=True)
        case_number = cells[1].get_text(strip=True)

        # Petitioner/Respondent cell HTML is "PETITIONER<br>Vs</br>RESPONDENT".
        # The </br> is invalid HTML so BeautifulSoup produces no separator
        # there, yielding "PETITIONERVsRESPONDENT" - split on the literal "Vs".
        party_text = cells[2].get_text(separator="|", strip=True).replace("|", "")
        petitioner, respondent = "", ""
        if "Vs" in party_text:
            petitioner, respondent = party_text.split("Vs", 1)
            petitioner, respondent = petitioner.strip(), respondent.strip()
        else:
            petitioner = party_text.strip()

        # View link -> viewHistory(...) params
        view_params = None
        link = cells[3].find("a")
        if link:
            onclick = link.get("onClick") or link.get("onclick") or ""
            view_params = parse_view_params(onclick)

        cases.append({
            "sr_no": sr_no,
            "case_number": case_number,
            "petitioner": petitioner,
            "respondent": respondent,
            "court_name": current_court_name,
            "view_params": view_params,
        })

    log.debug(f"Parsed {len(cases)} case rows")
    return cases


# ── Case Detail Parser (home/viewHistory) ────────────────────────────────────────
def parse_case_detail(response_text: str) -> dict:
    """
    Parse the response from home/viewHistory.

    Response shape:
        {"data_list": "<h2>...court name...</h2><h3>Case Details</h3>
                        <table class='case_details_table'>...</table>
                        <h3>Case Status</h3>
                        <table class='case_status_table'>...</table>
                        <h3>Petitioner and Advocate</h3>
                        <ul class='petitioner-advocate-list'>...</ul>
                        <h3>Respondent and Advocate</h3>
                        <ul class='respondent-advocate-list'>...</ul>
                        <h3>Acts</h3>
                        <table id='act_table'>...</table>
                        ... Processes / IA Status / Case History ..."}

    Returns a dict with keys:
        court_name, case_type, filing_number, filing_date,
        registration_number, registration_date, cnr_number,
        first_hearing_date, next_hearing_date, case_stage,
        court_number_judge, petitioners, respondents,
        acts (list of {act, sections}), case_history (list of dicts)
    """
    data = unwrap_json(response_text)
    html_fragment = data.get("data_list", "")

    if not html_fragment:
        log.warning(f"No 'data_list' key in response. Keys: {list(data.keys())}")
        return {}

    soup = BeautifulSoup(html_fragment, "html.parser")
    detail: dict = {}

    # Court name heading
    h2 = soup.find("h2", id="chHeading")
    if h2:
        detail["court_name"] = h2.get_text(strip=True)

    # ── Case Details + Case Status tables (label/value th-td pairs) ──────────
    label_map = {
        "case type": "case_type",
        "filing number": "filing_number",
        "filing date": "filing_date",
        "registration number": "registration_number",
        "registration date": "registration_date",
        "e-filing number": "efiling_number",
        "e-filing date": "efiling_date",
        "first hearing date": "first_hearing_date",
        "next hearing date": "next_hearing_date",
        "case stage": "case_stage",
        "court number and judge": "court_number_judge",
    }

    for table_class in ("case_details_table", "case_status_table"):
        table = soup.find("table", class_=table_class)
        if not table:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            i = 0
            while i < len(cells) - 1:
                label = cells[i].get_text(strip=True).lower().rstrip(":")
                value_cell = cells[i + 1]
                value = value_cell.get_text(strip=True)

                if "cnr number" in label:
                    # CNR cell text includes a trailing note; extract via regex
                    m = re.search(r"\b[A-Z0-9]{16}\b", value)
                    detail["cnr_number"] = m.group() if m else value
                elif label in label_map:
                    detail[label_map[label]] = value

                i += 2

    # ── Petitioner / Respondent + Advocate lists ─────────────────────────────
    pet_ul = soup.find("ul", class_="petitioner-advocate-list")
    detail["petitioners"] = _parse_party_advocate_list(pet_ul)

    resp_ul = soup.find("ul", class_="respondent-advocate-list")
    detail["respondents"] = _parse_party_advocate_list(resp_ul)

    # ── Acts table ────────────────────────────────────────────────────────────
    acts_table = soup.find("table", id="act_table")
    acts = []
    if acts_table:
        rows = acts_table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) >= 2:
                acts.append({
                    "act": cells[0].get_text(strip=True),
                    "sections": cells[1].get_text(strip=True),
                })
    detail["acts"] = acts

    # ── Case History table ───────────────────────────────────────────────────
    history_table = soup.find("table", class_="history_table")
    history = []
    if history_table:
        rows = history_table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 4:
                history.append({
                    "judge": cells[0].get_text(strip=True),
                    "business_date": cells[1].get_text(strip=True),
                    "hearing_date": cells[2].get_text(strip=True),
                    "purpose": cells[3].get_text(strip=True),
                })
    detail["case_history"] = history

    return detail


def _parse_party_advocate_list(ul_tag) -> list[dict]:
    """
    Parse <ul><li>1) NAME<br/>   Advocate- XYZ<br/></li>...</ul>
    into [{"name": "NAME", "advocate": "XYZ"}, ...]
    """
    if not ul_tag:
        return []

    results = []
    for li in ul_tag.find_all("li"):
        # get_text with a separator so <br/> becomes a delimiter
        text = li.get_text(separator="|", strip=True)
        parts = [p.strip() for p in text.split("|") if p.strip()]

        if not parts:
            continue

        name = re.sub(r"^\d+\)\s*", "", parts[0])  # strip leading "1) "
        advocate = ""
        for p in parts[1:]:
            if p.lower().startswith("advocate"):
                advocate = re.sub(r"^advocate[\s\-:]*", "", p, flags=re.IGNORECASE).strip()
                break

        results.append({"name": name, "advocate": advocate})

    return results


# ── Custom Exceptions ──────────────────────────────────────────────────────────
class WrongCaptchaError(Exception):
    """Raised when CAPTCHA answer is rejected by the server."""
    pass
