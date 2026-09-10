"""
ecourts.parser.case_details
===========================
High-fidelity parser for eCourts India deep case details (home/viewHistory).
Extracts 16-character CNR numbers, case classification metadata, petitioner and
respondent rosters with advocates, statutory acts and sections, chronological
hearing logs, and interim applications.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

from bs4 import BeautifulSoup

log = logging.getLogger("ecourts.parser.case_details")

CNR_REGEX = re.compile(r"\b([A-Z]{4}\d{12})\b")
CNR_16_ALPHANUM_REGEX = re.compile(r"\b([A-Z0-9]{16})\b")


def extract_cnr(text: str) -> Optional[str]:
    """Extracts a valid 16-character eCourts CNR number from arbitrary text,

    stripping out extraneous label text or trailing notices such as '(Scan QR code)'.
    """
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


@dataclass
class CaseDetail:
    """Structured data model representing complete eCourts case record."""

    cnr_number: str
    case_type: str = ""
    case_number: str = ""
    filing_number: str = ""
    filing_date: str = ""
    registration_number: str = ""
    registration_date: str = ""
    case_status: str = "Pending"
    stage: str = ""  # case_stage
    court_name: str = ""
    coram: str = ""  # court_number_judge
    first_hearing_date: str = ""
    next_hearing_date: str = ""
    parties: List[Dict[str, str]] = field(default_factory=list)
    acts: List[Dict[str, str]] = field(default_factory=list)
    hearings: List[Dict[str, str]] = field(default_factory=list)
    ia_details: List[Dict[str, Any]] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def case_stage(self) -> str:
        return self.stage

    @property
    def court_number_judge(self) -> str:
        return self.coram

    @property
    def petitioners(self) -> List[Dict[str, str]]:
        return [p for p in self.parties if p.get("type") == "petitioner"]

    @property
    def respondents(self) -> List[Dict[str, str]]:
        return [p for p in self.parties if p.get("type") == "respondent"]

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to a dictionary suitable for JSON serialization and DB storage."""
        d = asdict(self)
        d["case_stage"] = self.stage
        d["court_number_judge"] = self.coram
        d["petitioners"] = self.petitioners
        d["respondents"] = self.respondents
        return d


def _parse_roster(ul_tag: Optional[Any], party_type: str) -> List[Dict[str, str]]:
    """Parse a party roster <ul> list tag:

    <li>1) PARTY NAME<br/>   Advocate- COUNSEL NAME<br/></li>
    Handles unassigned advocates, in-person appearances, and missing advocate tags.
    """
    if not ul_tag:
        return []

    roster: List[Dict[str, str]] = []
    for li in ul_tag.find_all("li"):
        # Split on delimiter inserted for <br> tags
        text = li.get_text(separator="|", strip=True)
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if not parts:
            continue

        # Strip leading ordinal (e.g. "1) ", "2. ")
        raw_name = parts[0]
        clean_name = re.sub(r"^\d+[\)\.]\s*", "", raw_name).strip()

        advocate = ""
        for part in parts[1:]:
            if re.match(r"^advocate", part, flags=re.IGNORECASE):
                # Clean prefix "Advocate - ", "Advocate: ", etc.
                advocate = re.sub(r"^advocate[\s\-:]*", "", part, flags=re.IGNORECASE).strip()
                break

        roster.append({
            "type": party_type,
            "name": clean_name,
            "advocate": advocate,
        })

    return roster


def parse_case_detail(response_data: Union[str, Dict[str, Any]]) -> CaseDetail:
    """Deeply parses the home/viewHistory response.

    Extracts:
      - Court Name
      - Case Details & Status (CNR, case type, filing, registration, stage, judge)
      - Petitioner & Respondent rosters with advocates
      - Statutory Acts & Sections
      - Chronological Hearing History
      - Interim Application Details (if present)
    """
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
        log.warning("parse_case_detail: Empty HTML fragment provided")
        return CaseDetail(cnr_number="")

    soup = BeautifulSoup(html_fragment, "html.parser")

    # 1. Court Name heading
    court_name = ""
    h2 = soup.find("h2", id="chHeading") or soup.find("h2")
    if h2:
        court_name = h2.get_text(strip=True)

    # 2. Case Details & Status tables
    field_map: Dict[str, str] = {}
    label_norm_map = {
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
        "stage": "case_stage",
        "court number and judge": "court_number_judge",
        "court number & judge": "court_number_judge",
        "court": "court_name",
        "cnr number": "cnr_number",
        "case status": "case_status",
    }

    # Search for all table rows inside case_details_table and case_status_table
    for table_class in ("case_details_table", "case_status_table"):
        for table in soup.find_all("table", class_=table_class):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                i = 0
                while i < len(cells) - 1:
                    raw_label = cells[i].get_text(strip=True).lower().rstrip(":")
                    raw_val = cells[i + 1].get_text(strip=True)

                    for pattern, key in label_norm_map.items():
                        if pattern in raw_label:
                            if key == "cnr_number":
                                parsed_cnr = extract_cnr(raw_val)
                                field_map[key] = parsed_cnr or raw_val
                            else:
                                field_map[key] = raw_val
                            break
                    i += 2

    # Also search general table rows if CNR or filing date wasn't found in specific tables
    if "cnr_number" not in field_map:
        for row in soup.find_all("tr"):
            text = row.get_text(" ", strip=True)
            if "cnr" in text.lower():
                cnr_val = extract_cnr(text)
                if cnr_val:
                    field_map["cnr_number"] = cnr_val
                    break

    # 3. Parties & Advocates
    parties: List[Dict[str, str]] = []
    pet_ul = soup.find("ul", class_="petitioner-advocate-list") or soup.find(
        "ul", class_=re.compile(r"petitioner")
    )
    parties.extend(_parse_roster(pet_ul, "petitioner"))

    resp_ul = soup.find("ul", class_="respondent-advocate-list") or soup.find(
        "ul", class_=re.compile(r"respondent")
    )
    parties.extend(_parse_roster(resp_ul, "respondent"))

    # 4. Acts & Sections
    acts: List[Dict[str, str]] = []
    acts_table = soup.find("table", id="act_table") or soup.find(
        "table", class_=re.compile(r"act_table")
    )
    if acts_table:
        for row in acts_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                act_name = cells[0].get_text(strip=True)
                sec_desc = cells[1].get_text(strip=True)
                if act_name and not act_name.lower().startswith("under act"):
                    acts.append({
                        "act": act_name,
                        "section": sec_desc,
                        "sections": sec_desc,
                    })

    # 5. Chronological Hearing History
    hearings: List[Dict[str, str]] = []
    history_table = soup.find("table", class_="history_table") or soup.find("table", id="history_table")
    if history_table:
        for row in history_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                judge = cells[0].get_text(strip=True)
                b_date = cells[1].get_text(strip=True)
                h_date = cells[2].get_text(strip=True)
                purpose = cells[3].get_text(strip=True)
                # Ignore table headers mistakenly wrapped in td
                if b_date.lower().startswith("business") or h_date.lower().startswith("hearing"):
                    continue
                hearings.append({
                    "judge": judge,
                    "business_date": b_date,
                    "hearing_date": h_date,
                    "date": b_date,
                    "next_date": h_date,
                    "purpose": purpose,
                })

    # 6. Interim Applications (IA Status) [Optional]
    ia_list: List[Dict[str, Any]] = []
    ia_table = soup.find("table", class_="ia_table") or soup.find("table", id="ia_table")
    if ia_table:
        for row in ia_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                ia_num = cells[0].get_text(strip=True)
                if not ia_num or ia_num.lower() in ("ia number", "ia no", "ia no.", "interim application number"):
                    continue
                ia_party = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                ia_status = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                ia_list.append({
                    "ia_number": ia_num,
                    "party": ia_party,
                    "status": ia_status,
                })

    cnr_number = field_map.get("cnr_number", "")
    case_type = field_map.get("case_type", "")
    reg_no = field_map.get("registration_number", "")
    case_number = reg_no or field_map.get("filing_number", "")

    return CaseDetail(
        cnr_number=cnr_number,
        case_type=case_type,
        case_number=case_number,
        filing_number=field_map.get("filing_number", ""),
        filing_date=field_map.get("filing_date", ""),
        registration_number=reg_no,
        registration_date=field_map.get("registration_date", ""),
        case_status=field_map.get("case_status", "Pending"),
        stage=field_map.get("case_stage", ""),
        court_name=court_name or field_map.get("court_name", ""),
        coram=field_map.get("court_number_judge", ""),
        first_hearing_date=field_map.get("first_hearing_date", ""),
        next_hearing_date=field_map.get("next_hearing_date", ""),
        parties=parties,
        acts=acts,
        hearings=hearings,
        ia_details=ia_list,
        raw_data=field_map,
    )
