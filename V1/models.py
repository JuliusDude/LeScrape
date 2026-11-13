"""
V1.models
==================
Rich data models capturing comprehensive eCourts India case records,
including Processes, Main Matters, Final Orders/Judgments, Disposal details,
e-Filing details, Parties, Acts, and Chronological Hearing History.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProcessItem:
    """Represents a court process issued in the case."""
    process_id: str
    process_title: str
    process_date: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class MainMatterItem:
    """Represents connected or parent main matter details."""
    main_case_number: str
    main_cnr_number: str
    main_filing_number: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class OrderItem:
    """Represents an interim or final order / judgment."""
    order_number: str
    order_date: str
    order_details: str
    pdf_params: Optional[str] = ""
    order_section: str = ""  # heading of the table, e.g. 'Interim Orders'
    order_category: str = ""  # aria-label text before ' | '
    order_description: str = ""  # full aria-label
    pdf_args_json: str = ""  # displayPdf(...) string args as a JSON list

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class PartyItem:
    """Represents a petitioner or respondent with their advocate."""
    type: str  # 'petitioner' or 'respondent'
    name: str
    advocate: str = ""
    party_seq: int = 0  # 1-based position within its type (page order)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActItem:
    """Represents a statutory act and section."""
    act: str
    section: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class HearingItem:
    """Represents an entry in the chronological hearing history."""
    hearing_index: int
    judge: str
    business_date: str
    hearing_date: str
    purpose: str
    hearing_seq: int = 0  # 1 = OLDEST hearing (hearing_index 1 = first row = newest)
    # The 11 args of onclick=viewBusiness(...), in order (see parser.VB_ARG_FIELDS).
    vb_court_code: str = ""
    vb_dist_code: str = ""
    vb_nextdate: str = ""  # yyyymmdd of the next hearing
    vb_cino: str = ""
    vb_state_code: str = ""
    vb_disposal_flag: str = ""  # e.g. 'Disposed' / 'DisposedP'
    vb_business_date: str = ""
    vb_court_no: str = ""
    vb_national_court_code: str = ""
    vb_search_by: str = ""
    vb_srno: str = ""
    vb_args_json: str = ""  # all args verbatim as a JSON list

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeeperCaseDetail:
    """Complete deep representation of an eCourts case."""
    cnr_number: str
    case_type: str = ""
    case_number: str = ""
    filing_number: str = ""
    filing_date: str = ""
    registration_number: str = ""
    registration_date: str = ""
    efiling_number: str = ""
    efiling_date: str = ""
    
    # Status & Disposal
    first_hearing_date: str = ""
    decision_date: str = ""
    case_status: str = "Pending"  # 'Case disposed' or 'Pending'
    nature_of_disposal: str = ""
    court_number_judge: str = ""
    court_name: str = ""
    case_status_raw: str = ""  # verbatim status cell text (e.g. 'Case disposedSub Stage')
    case_sub_stage: Optional[str] = None  # None = label absent; '' = label present, no value
    case_stage: str = ""
    next_hearing_date: str = ""
    national_court_code: str = ""
    state_code: str = ""
    dist_code: str = ""
    court_complex_code: str = ""
    est_code: str = ""
    status_assumed: bool = False
    view_params_json: str = ""
    source_response_id: Optional[int] = None

    # Relational Child Data
    parties: List[PartyItem] = field(default_factory=list)
    acts: List[ActItem] = field(default_factory=list)
    processes: List[ProcessItem] = field(default_factory=list)
    main_matters: List[MainMatterItem] = field(default_factory=list)
    hearings: List[HearingItem] = field(default_factory=list)
    orders: List[OrderItem] = field(default_factory=list)

    @property
    def petitioners(self) -> List[PartyItem]:
        return [p for p in self.parties if p.type == "petitioner"]

    @property
    def respondents(self) -> List[PartyItem]:
        return [p for p in self.parties if p.type == "respondent"]

    def to_dict(self) -> Dict[str, Any]:
        """Convert entire case tree into a serializable nested dictionary."""
        return {
            "cnr_number": self.cnr_number,
            "case_type": self.case_type,
            "case_number": self.case_number,
            "filing_number": self.filing_number,
            "filing_date": self.filing_date,
            "registration_number": self.registration_number,
            "registration_date": self.registration_date,
            "efiling_number": self.efiling_number,
            "efiling_date": self.efiling_date,
            "first_hearing_date": self.first_hearing_date,
            "decision_date": self.decision_date,
            "case_status": self.case_status,
            "nature_of_disposal": self.nature_of_disposal,
            "court_number_judge": self.court_number_judge,
            "court_name": self.court_name,
            "state_code": self.state_code,
            "dist_code": self.dist_code,
            "court_complex_code": self.court_complex_code,
            "est_code": self.est_code,
            "parties": [p.to_dict() for p in self.parties],
            "petitioners": [p.to_dict() for p in self.petitioners],
            "respondents": [p.to_dict() for p in self.respondents],
            "acts": [a.to_dict() for a in self.acts],
            "processes": [pr.to_dict() for pr in self.processes],
            "main_matters": [m.to_dict() for m in self.main_matters],
            "hearings": [h.to_dict() for h in self.hearings],
            "orders": [o.to_dict() for o in self.orders],
            "status_assumed": self.status_assumed,
            "view_params_json": self.view_params_json,
            "source_response_id": self.source_response_id,
            "case_status_raw": self.case_status_raw,
            "case_sub_stage": self.case_sub_stage,
            "case_stage": self.case_stage,
            "next_hearing_date": self.next_hearing_date,
            "national_court_code": self.national_court_code,
        }
