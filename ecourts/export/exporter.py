"""
ecourts.export.exporter
=======================
Export pipelines for structured JSON documents (nested relations) and
CSV formats (normalized relational tables and denormalized flat files).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from ecourts.storage.db import Database

log = logging.getLogger("ecourts.export.exporter")


def _get_cases_subset(
    db: Database,
    status: Optional[str] = None,
    cnr_list: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Retrieve case records with nested child relations matching filters."""
    if cnr_list:
        cases = []
        for cnr in cnr_list:
            c = db.get_case_by_cnr(cnr)
            if c:
                if status and c.get("status") != status:
                    continue
                cases.append(c)
        return cases

    # List all cases from database
    total_count = db.get_cases_count(status=status)
    return db.list_cases(status=status, limit=max(1000, total_count + 100), offset=0)


def export_to_json(
    db: Database,
    output_path: str,
    status: Optional[str] = None,
    cnr_list: Optional[List[str]] = None,
    indent: int = 2,
) -> int:
    """Export complete case records with nested parties, acts, and hearings

    to a structured JSON document.
    Returns the number of cases exported.
    """
    cases = _get_cases_subset(db, status=status, cnr_list=cnr_list)

    # Clean raw_json from exported JSON objects for clarity if present
    cleaned_cases = []
    for c in cases:
        item = dict(c)
        item.pop("raw_json", None)
        cleaned_cases.append(item)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_cases, f, indent=indent, ensure_ascii=False)

    log.info("Exported %d case(s) to JSON: %s", len(cleaned_cases), output_path)
    return len(cleaned_cases)


def export_to_csv_relational(
    db: Database,
    output_dir: str,
    status: Optional[str] = None,
    cnr_list: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Export relational SQLite tables to clean individual CSV files:

      - cases.csv
      - parties.csv
      - acts.csv
      - hearings.csv
    Returns a dictionary of row counts written per table.
    """
    os.makedirs(os.path.abspath(output_dir), exist_ok=True)
    cases = _get_cases_subset(db, status=status, cnr_list=cnr_list)
    counts = {"cases": len(cases), "parties": 0, "acts": 0, "hearings": 0}

    # 1. cases.csv
    cases_csv_path = os.path.join(output_dir, "cases.csv")
    case_fields = [
        "cnr_number",
        "case_type",
        "case_number",
        "filing_number",
        "filing_date",
        "registration_number",
        "registration_date",
        "case_stage",
        "court_number_judge",
        "court_name",
        "state_code",
        "dist_code",
        "court_complex_code",
        "est_code",
        "first_hearing_date",
        "next_hearing_date",
        "status",
        "first_scraped_at",
        "last_scraped_at",
    ]
    with open(cases_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=case_fields, extrasaction="ignore")
        writer.writeheader()
        for c in cases:
            writer.writerow(c)

    # 2. parties.csv
    parties_csv_path = os.path.join(output_dir, "parties.csv")
    party_fields = ["cnr_number", "party_type", "party_seq", "name", "advocate", "created_at"]
    with open(parties_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=party_fields, extrasaction="ignore")
        writer.writeheader()
        for c in cases:
            for p in c.get("parties", []):
                writer.writerow(p)
                counts["parties"] += 1

    # 3. acts.csv
    acts_csv_path = os.path.join(output_dir, "acts.csv")
    act_fields = ["cnr_number", "act_seq", "act_name", "sections", "created_at"]
    with open(acts_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=act_fields, extrasaction="ignore")
        writer.writeheader()
        for c in cases:
            for a in c.get("acts", []):
                writer.writerow(a)
                counts["acts"] += 1

    # 4. hearings.csv
    hearings_csv_path = os.path.join(output_dir, "hearings.csv")
    hearing_fields = [
        "cnr_number",
        "hearing_seq",
        "business_date",
        "hearing_date",
        "purpose",
        "judge",
        "created_at",
    ]
    with open(hearings_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=hearing_fields, extrasaction="ignore")
        writer.writeheader()
        for c in cases:
            for h in c.get("hearings", []):
                writer.writerow(h)
                counts["hearings"] += 1

    log.info("Exported relational CSVs to %s: %s", output_dir, counts)
    return counts


def export_to_csv_flat(
    db: Database,
    output_path: str,
    status: Optional[str] = None,
    cnr_list: Optional[List[str]] = None,
) -> int:
    """Export denormalized flat CSV file containing aggregated parties, acts,

    and next hearing summaries for spreadsheet analysis.
    Returns the number of cases exported.
    """
    cases = _get_cases_subset(db, status=status, cnr_list=cnr_list)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    fields = [
        "cnr_number",
        "case_number",
        "case_type",
        "court_name",
        "filing_date",
        "registration_date",
        "case_stage",
        "status",
        "court_number_judge",
        "petitioners",
        "respondents",
        "acts_sections",
        "first_hearing_date",
        "next_hearing_date",
        "total_hearings",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for c in cases:
            # Aggregate petitioners
            pets = [
                f"{p['name']} (Advocate: {p['advocate']})" if p.get("advocate") else p["name"]
                for p in c.get("petitioners", [])
            ]
            # Aggregate respondents
            resps = [
                f"{r['name']} (Advocate: {r['advocate']})" if r.get("advocate") else r["name"]
                for r in c.get("respondents", [])
            ]
            # Aggregate acts
            acts = [
                f"{a['act_name']}: {a['sections']}" if a.get("sections") else a["act_name"]
                for a in c.get("acts", [])
            ]

            writer.writerow({
                "cnr_number": c.get("cnr_number", ""),
                "case_number": c.get("case_number", ""),
                "case_type": c.get("case_type", ""),
                "court_name": c.get("court_name", ""),
                "filing_date": c.get("filing_date", ""),
                "registration_date": c.get("registration_date", ""),
                "case_stage": c.get("case_stage", ""),
                "status": c.get("status", ""),
                "court_number_judge": c.get("court_number_judge", ""),
                "petitioners": "; ".join(pets),
                "respondents": "; ".join(resps),
                "acts_sections": "; ".join(acts),
                "first_hearing_date": c.get("first_hearing_date", ""),
                "next_hearing_date": c.get("next_hearing_date", ""),
                "total_hearings": len(c.get("hearings", [])),
            })

    log.info("Exported %d flat case records to CSV: %s", len(cases), output_path)
    return len(cases)


class Exporter:
    """Unified exporter facade providing JSON and CSV export methods."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def export(
        self,
        format: str = "json",
        output_path: str = "export.json",
        mode: str = "flat",
        status: Optional[str] = None,
        cnr_list: Optional[List[str]] = None,
    ) -> Union[int, Dict[str, int]]:
        fmt = format.lower()
        if fmt == "json":
            return export_to_json(self.db, output_path, status=status, cnr_list=cnr_list)
        elif fmt == "csv":
            if mode == "relational":
                return export_to_csv_relational(self.db, output_path, status=status, cnr_list=cnr_list)
            else:
                return export_to_csv_flat(self.db, output_path, status=status, cnr_list=cnr_list)
        else:
            raise ValueError(f"Unsupported export format: '{format}'. Use 'json' or 'csv'.")
