"""
deeper_tool.models
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

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class PartyItem:
    """Represents a petitioner or respondent with their advocate."""
    type: str  # 'petitioner' or 'respondent'
    name: str
    advocate: str = ""

    def to_dict(self) -> Dict[str, str]:
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
    state_code: str = ""
    dist_code: str = ""
    court_complex_code: str = ""
    est_code: str = ""

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
        }
