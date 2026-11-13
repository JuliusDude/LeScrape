"""Export the SQLite store: nested JSON per case + flat CSV (+ xlsx if openpyxl is installed)."""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional

from .store import Store

CASE_CSV = ["cnr", "kind", "parent_cnr", "case_number", "case_type", "court_name", "filing_number", "filing_date_iso",
            "registration_number", "registration_date_iso", "first_hearing_date_iso", "decision_date_iso",
            "next_hearing_date_iso", "case_stage", "case_status", "case_sub_stage", "nature_of_disposal",
            "court_number_and_judge", "national_court_code", "search_parties", "n_petitioners", "n_respondents",
            "petitioners", "respondents", "n_hearings", "n_orders", "n_processes", "n_main_matters",
            "main_case_nos", "vb_links", "vb_fetched", "pdf_links", "pdf_saved", "ack_fetched", "complete",
            "missing", "decree_holder", "judgement_debtor", "elapsed_s", "requests"]
HEARING_CSV = ["cnr", "seq", "judge", "business_on_date", "business_on_date_iso", "hearing_date", "hearing_date_iso",
               "purpose", "business_text", "next_purpose", "next_hearing_date", "next_hearing_date_iso",
               "business_judge", "fetched", "error"]
ORDER_CSV = ["cnr", "seq", "section", "category", "number", "order_date", "order_date_iso", "description", "pdf_url",
             "pdf_path", "sha256", "size", "pages", "needs_ocr", "fetched", "error", "text"]
IA_CSV = ["cnr", "seq", "ia_number", "parties", "filing_date", "filing_date_iso", "next_date", "next_date_iso",
          "next_purpose", "ia_status", "business_case_no", "n_business", "business_on_date", "hearing_date",
          "business", "fetched", "error"]
PARTY_CSV = ["cnr", "role", "seq", "name", "advocate"]


def _flat_case(d: Dict[str, Any]) -> Dict[str, Any]:
    r = {k: d.get(k) for k in CASE_CSV}
    r["n_petitioners"], r["n_respondents"] = len(d["petitioners"]), len(d["respondents"])
    r["petitioners"] = " | ".join(p["name"] for p in d["petitioners"])
    r["respondents"] = " | ".join(p["name"] for p in d["respondents"])
    r["n_hearings"], r["n_orders"] = len(d["hearings"]), len(d["orders"])
    r["n_processes"], r["n_main_matters"] = len(d["processes"]), len(d["main_matters"])
    r["main_case_nos"] = " | ".join(m["case_no"] for m in d["main_matters"])
    r["missing"] = "; ".join(d.get("missing") or [])
    a = d.get("ack") or {}
    r["decree_holder"], r["judgement_debtor"] = a.get("decree_holder"), a.get("judgement_debtor")
    return r


def _ia_rows(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per IA business entry (one row for an IA with none), cnr/IA fields repeated."""
    out = []
    for ia in d.get("interim_applications") or []:
        base = dict(ia, cnr=d["cnr"], parties=" | ".join(ia["parties"]), n_business=len(ia["business"]))
        for b in ia["business"] or [{}]:
            out.append(dict(base, business_on_date=b.get("business_on_date"), hearing_date=b.get("hearing_date"),
                            business=b.get("business")))
    return out


def _write_csv(path: str, cols: List[str], rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _cell(v: Any) -> Any:
    if isinstance(v, str):
        return v[:32000]          # Excel cell limit
    return v


def export_all(store: Store, out_dir: str, kind: Optional[str] = None) -> Dict[str, Any]:
    os.makedirs(os.path.join(out_dir, "json"), exist_ok=True)
    cases, hearings, orders, parties, ias, index = [], [], [], [], [], []
    for cnr in store.case_cnrs(kind):
        d = store.get_case(cnr)
        with open(os.path.join(out_dir, "json", f"{cnr}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
        cases.append(_flat_case(d))
        hearings += d["hearings"]
        orders += d["orders"]
        ias += _ia_rows(d)
        parties += d["petitioners"] + d["respondents"]
        index.append({"cnr": cnr, "case_number": d.get("case_number"), "complete": d.get("complete"),
                      "hearings": len(d["hearings"]), "orders": len(d["orders"])})
    with open(os.path.join(out_dir, "json", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    _write_csv(os.path.join(out_dir, "cases.csv"), CASE_CSV, cases)
    _write_csv(os.path.join(out_dir, "hearings.csv"), HEARING_CSV, hearings)
    _write_csv(os.path.join(out_dir, "orders.csv"), ORDER_CSV, orders)
    _write_csv(os.path.join(out_dir, "ia.csv"), IA_CSV, ias)
    _write_csv(os.path.join(out_dir, "parties.csv"), PARTY_CSV, parties)
    xlsx = None
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, cols, rows in (("cases", CASE_CSV, cases), ("hearings", HEARING_CSV, hearings),
                                 ("orders", ORDER_CSV, orders), ("ia", IA_CSV, ias),
                                 ("parties", PARTY_CSV, parties)):
            ws = wb.create_sheet(name)
            ws.append(cols)
            for r in rows:
                ws.append([_cell(r.get(c)) for c in cols])
        xlsx = os.path.join(out_dir, "cases.xlsx")
        wb.save(xlsx)
    except ImportError:
        pass
    return {"cases": len(cases), "hearings": len(hearings), "orders": len(orders), "parties": len(parties),
            "ia": len(ias), "xlsx": xlsx}
