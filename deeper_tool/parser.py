"""
deeper_tool.parser
==================
Deep parsing engine for eCourts India case detail views (home/viewHistory).
Extracts:
  - Complete Case Details (CNR, filing/reg numbers & dates, e-filing details)
  - Case Status & Disposal details (First hearing, decision date, status, nature of disposal, coram)
  - Petitioner & Respondent rosters with assigned advocates
  - Statutory Acts and Sections
  - Process details (Process ID, Title, Date)
  - Main Matters (Main Case No, Main CNR, Main Filing No)
  - Chronological Hearing History (Judge, business date, hearing date, purpose)
  - Final Orders / Judgments (Order number, date, details, displayPdf parameters)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union
from bs4 import BeautifulSoup

from deeper_tool.models import (
    ActItem,
    DeeperCaseDetail,
    HearingItem,
    MainMatterItem,
    OrderItem,
    PartyItem,
    ProcessItem,
)

log = logging.getLogger("deeper_tool.parser")

CNR_REGEX = re.compile(r"\b([A-Z]{4}\d{12})\b")
CNR_16_ALPHANUM_REGEX = re.compile(r"\b([A-Z0-9]{16})\b")


def extract_cnr(text: str) -> Optional[str]:
    """Extracts a valid 16-character eCourts CNR number from text."""
    if not text:
        return None
    m = CNR_REGEX.search(text)
    if m:
        return m.group(1)
    m16 = CNR_16_ALPHANUM_REGEX.search(text)
    if m16:
        return m16.group(1)
    m_loose = re.search(r"([A-Z]{4}\d{12})", text)
    if m_loose:
        return m_loose.group(1)
    return None


def _clean_text(s: str) -> str:
    """Strip special control characters,  artifacts, and extraneous whitespace."""
    if not s:
        return ""
    cleaned = s.replace("\ufffd", "").replace("", "").strip()
    return re.sub(r"\s+", " ", cleaned)


def _parse_roster(ul_tag: Optional[Any], party_type: str) -> List[PartyItem]:
    """Parses a party roster list <ul>:
    <li>1) KRISHNAMURTHY G<br/>   Advocate- DINESH J S</li>
    """
    if not ul_tag:
        return []

    roster: List[PartyItem] = []
    for li in ul_tag.find_all("li"):
        text = li.get_text(separator="|", strip=True)
        parts = [_clean_text(p) for p in text.split("|") if _clean_text(p)]
        if not parts:
            continue

        # Extract name by stripping leading ordinal (e.g. "1) ")
        raw_name = parts[0]
        clean_name = re.sub(r"^\d+[\)\.]\s*", "", raw_name).strip()

        advocate = ""
        for part in parts[1:]:
            if re.match(r"^advocate", part, flags=re.IGNORECASE):
                advocate = re.sub(r"^advocate[\s\-:]*", "", part, flags=re.IGNORECASE).strip()
                break

        roster.append(PartyItem(
            type=party_type,
            name=clean_name,
            advocate=advocate,
        ))

    return roster


def parse_deeper_case_detail(response_data: Union[str, Dict[str, Any]]) -> DeeperCaseDetail:
    """Parses eCourts home/viewHistory HTML response into a comprehensive DeeperCaseDetail."""
    html_fragment = ""
    if isinstance(response_data, dict):
        html_fragment = str(response_data.get("data_list", ""))
    elif isinstance(response_data, str):
        trimmed = response_data.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                html_fragment = str(data.get("data_list", ""))
            except json.JSONDecodeError:
                html_fragment = response_data
        else:
            html_fragment = response_data

    if not html_fragment:
        log.warning("parse_deeper_case_detail: Empty HTML fragment provided")
        return DeeperCaseDetail(cnr_number="")

    soup = BeautifulSoup(html_fragment, "html.parser")

    # 1. Court Name
    court_name = ""
    h2 = soup.find("h2", id="chHeading") or soup.find("h2") or soup.find("h3")
    if h2:
        court_name = _clean_text(h2.get_text(strip=True))

    field_map: Dict[str, str] = {}

    label_norm_order = [
        ("e-filing number", "efiling_number"),
        ("e-filing date", "efiling_date"),
        ("filing number", "filing_number"),
        ("filing date", "filing_date"),
        ("registration number", "registration_number"),
        ("registration date", "registration_date"),
        ("first hearing date", "first_hearing_date"),
        ("next hearing date", "next_hearing_date"),
        ("decision date", "decision_date"),
        ("nature of disposal", "nature_of_disposal"),
        ("case status", "case_status"),
        ("court number and judge", "court_number_judge"),
        ("court number & judge", "court_number_judge"),
        ("case type", "case_type"),
        ("cnr number", "cnr_number"),
    ]

    # 2. Case Details & Status Tables
    for table_class in ("case_details_table", "case_status_table"):
        for table in soup.find_all("table", class_=table_class):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                i = 0
                while i < len(cells) - 1:
                    raw_label = _clean_text(cells[i].get_text(strip=True)).lower().rstrip(":")
                    raw_val = _clean_text(cells[i + 1].get_text(strip=True))

                    for pattern, key in label_norm_order:
                        if pattern in raw_label:
                            if key == "cnr_number":
                                parsed_cnr = extract_cnr(raw_val)
                                field_map[key] = parsed_cnr or raw_val
                            else:
                                field_map[key] = raw_val
                            break
                    i += 2

    # Fallback search for CNR if not caught
    if "cnr_number" not in field_map or not field_map["cnr_number"]:
        for el in soup.find_all(["span", "td", "strong", "a"]):
            text = el.get_text(strip=True)
            cnr_match = extract_cnr(text)
            if cnr_match:
                field_map["cnr_number"] = cnr_match
                break

    cnr_number = field_map.get("cnr_number", "")

    # Derive case_number from registration_number if present
    case_number = field_map.get("registration_number", "")
    if case_number and field_map.get("case_type"):
        ct_prefix = field_map["case_type"].split("-")[0].strip()
        if not case_number.startswith(ct_prefix):
            case_number = f"{ct_prefix}/{case_number}"

    # 3. Parties & Advocates
    parties: List[PartyItem] = []
    pet_ul = soup.find("ul", class_=re.compile(r"petitioner", re.IGNORECASE))
    parties.extend(_parse_roster(pet_ul, "petitioner"))

    resp_ul = soup.find("ul", class_=re.compile(r"respondent", re.IGNORECASE))
    parties.extend(_parse_roster(resp_ul, "respondent"))

    # 4. Acts & Sections
    acts: List[ActItem] = []
    acts_table = soup.find("table", id="act_table") or soup.find("table", class_=re.compile(r"acts?_table", re.IGNORECASE))
    if acts_table:
        for row in acts_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                act_name = _clean_text(cells[0].get_text(strip=True))
                sec_desc = _clean_text(cells[1].get_text(strip=True))
                if act_name and not act_name.lower().startswith("under act"):
                    acts.append(ActItem(act=act_name, section=sec_desc))

    # 5. Processes
    processes: List[ProcessItem] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any("process id" in h for h in headers):
            rows = table.find_all("tr")
            if any(r.find_all("td") for r in rows):
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        p_id = _clean_text(cells[0].get_text(strip=True))
                        p_title = _clean_text(cells[1].get_text(strip=True))
                        p_date = _clean_text(cells[2].get_text(strip=True))
                        if p_id:
                            processes.append(ProcessItem(process_id=p_id, process_title=p_title, process_date=p_date))
            else:
                # Handle malformed HTML where tds exist without tr inside the table
                tds = table.find_all("td")
                for i in range(0, len(tds), 3):
                    group = tds[i : i + 3]
                    if len(group) == 3:
                        p_id = _clean_text(group[0].get_text(strip=True))
                        p_title = _clean_text(group[1].get_text(strip=True))
                        p_date = _clean_text(group[2].get_text(strip=True))
                        if p_id:
                            processes.append(ProcessItem(process_id=p_id, process_title=p_title, process_date=p_date))

    # 6. Main Matters
    main_matters: List[MainMatterItem] = []
    # Look for table with class "table_o" or rows containing "Main Case No"
    main_case_no = ""
    main_cnr = ""
    main_filing_no = ""
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if "main case no" in text.lower() or "main filing no" in text.lower():
            for tr in table.find_all("tr"):
                row_text = tr.get_text(" ", strip=True)
                if "main case no" in row_text.lower():
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        val = _clean_text(tds[1].get_text(" ", strip=True))
                        main_case_no = val
                        # Check for CNR link or parenthesized CNR
                        m_cnr = extract_cnr(val)
                        if m_cnr:
                            main_cnr = m_cnr
                            # Strip CNR from case number for cleanliness
                            main_case_no = re.sub(r"\(.*?\)", "", val).strip()
                elif "main filing no" in row_text.lower():
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        main_filing_no = _clean_text(tds[1].get_text(" ", strip=True))

    if main_case_no or main_cnr or main_filing_no:
        main_matters.append(MainMatterItem(
            main_case_number=main_case_no,
            main_cnr_number=main_cnr,
            main_filing_number=main_filing_no,
        ))

    # 7. Chronological Hearing History
    hearings: List[HearingItem] = []
    h_table = soup.find("table", class_=re.compile(r"history_table", re.IGNORECASE))
    if h_table:
        h_idx = 1
        for row in h_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                j_name = _clean_text(cells[0].get_text(strip=True))
                b_date = _clean_text(cells[1].get_text(strip=True))
                h_date = _clean_text(cells[2].get_text(strip=True))
                purpose = _clean_text(cells[3].get_text(strip=True))

                # Skip header repetitions if any
                if "judge" in j_name.lower() and "business" in b_date.lower():
                    continue

                hearings.append(HearingItem(
                    hearing_index=h_idx,
                    judge=j_name,
                    business_date=b_date,
                    hearing_date=h_date,
                    purpose=purpose,
                ))
                h_idx += 1

    # 8. Final Orders / Judgements
    orders: List[OrderItem] = []
    order_table = soup.find("table", class_=re.compile(r"order_table", re.IGNORECASE))
    if order_table:
        for row in order_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                ord_no = _clean_text(cells[0].get_text(strip=True))
                ord_date = _clean_text(cells[1].get_text(strip=True))
                ord_details = _clean_text(cells[2].get_text(strip=True))

                # Skip table header if th wasn't used
                if "order number" in ord_no.lower():
                    continue

                # Check for displayPdf onclick params
                pdf_onclick = ""
                a_tag = cells[2].find("a")
                if a_tag and a_tag.get("onclick"):
                    pdf_onclick = a_tag["onclick"].strip()

                orders.append(OrderItem(
                    order_number=ord_no,
                    order_date=ord_date,
                    order_details=ord_details,
                    pdf_params=pdf_onclick,
                ))

    return DeeperCaseDetail(
        cnr_number=cnr_number,
        case_type=field_map.get("case_type", ""),
        case_number=case_number,
        filing_number=field_map.get("filing_number", ""),
        filing_date=field_map.get("filing_date", ""),
        registration_number=field_map.get("registration_number", ""),
        registration_date=field_map.get("registration_date", ""),
        efiling_number=field_map.get("efiling_number", ""),
        efiling_date=field_map.get("efiling_date", ""),
        first_hearing_date=field_map.get("first_hearing_date", ""),
        decision_date=field_map.get("decision_date", ""),
        case_status=field_map.get("case_status", "Pending"),
        nature_of_disposal=field_map.get("nature_of_disposal", ""),
        court_number_judge=field_map.get("court_number_judge", ""),
        court_name=court_name,
        parties=parties,
        acts=acts,
        processes=processes,
        main_matters=main_matters,
        hearings=hearings,
        orders=orders,
    )
