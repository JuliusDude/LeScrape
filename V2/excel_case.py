"""Write one scraped case as a formatted, human-readable Excel workbook (offline).

Usage:
    python -m V2.excel_case <CNR> --out <run_dir> [--xlsx <file>]
Reads <run_dir>/json/<CNR>.json and writes <run_dir>/excel/<CNR>.xlsx unless --xlsx is given.
Sheets: Case, Parties, Hearings, Orders, Order Text, IA, Acts & Processes, Acknowledgement, Provenance.
Requires openpyxl (pip install openpyxl).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

HEAD_FILL = "1F3B57"
HEAD_FONT = "FFFFFF"
LABEL_FILL = "EEF2F6"
ZEBRA = "F7F9FB"
EXCEL_MAX = 32000


def _s(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(x) for x in v)
    if isinstance(v, str):
        return v[:EXCEL_MAX]
    return v


def _paragraphs(text: str, target: int = 700) -> List[str]:
    """Re-flow PDF text (hard line breaks every few words) into readable paragraphs of ~target chars,
    breaking at sentence ends or blank lines; each fits comfortably in one Excel cell."""
    out: List[str] = []
    buf = ""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            if buf:
                out.append(buf)
                buf = ""
            continue
        buf = f"{buf} {line}".strip()
        if len(buf) >= target and buf.endswith((".", ":", ";")):
            out.append(buf)
            buf = ""
        elif len(buf) >= 3 * target:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [p[:EXCEL_MAX] for p in out]


def _find_pdf(o: Dict[str, Any], run_dir: str) -> str:
    sha = o.get("sha256") or ""
    cands = [os.path.join(run_dir, "pdfs", sha[:2], sha + ".pdf")] if len(sha) > 2 else []
    if o.get("pdf_path"):
        cands += [o["pdf_path"], os.path.abspath(o["pdf_path"])]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return ""


def build_workbook(case: Dict[str, Any], run_dir: str = ""):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="D5DCE3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(bold=True, color=HEAD_FONT)
    head_fill = PatternFill("solid", fgColor=HEAD_FILL)
    label_fill = PatternFill("solid", fgColor=LABEL_FILL)
    zebra = PatternFill("solid", fgColor=ZEBRA)
    wrap = Alignment(wrap_text=True, vertical="top")

    wb = Workbook()
    wb.remove(wb.active)
    c = case
    title = c.get("case_number") or c.get("cnr")
    pet = "; ".join(p.get("name", "") for p in c.get("petitioners", []))
    res = "; ".join(p.get("name", "") for p in c.get("respondents", []))

    def kv_sheet(name: str, heading: str, rows: Sequence[Sequence[Any]], widths=(32, 90)):
        ws = wb.create_sheet(name)
        ws["A1"] = heading
        ws["A1"].font = Font(bold=True, size=14, color=HEAD_FILL)
        ws.merge_cells("A1:B1")
        r = 3
        for row in rows:
            if row and row[0] == "__section__":
                ws.cell(r, 1, row[1]).font = head_font
                for col in (1, 2):
                    ws.cell(r, col).fill = head_fill
                r += 1
                continue
            k, v = row
            a, b = ws.cell(r, 1, k), ws.cell(r, 2, _s(v))
            a.font, a.fill = Font(bold=True), label_fill
            for cell in (a, b):
                cell.border, cell.alignment = border, wrap
            r += 1
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = widths
        return ws

    def table_sheet(name: str, heading: str, cols: List[tuple], rows: List[List[Any]], empty: str = "None recorded on the site."):
        ws = wb.create_sheet(name)
        ws["A1"] = heading
        ws["A1"].font = Font(bold=True, size=14, color=HEAD_FILL)
        if not rows:
            ws["A3"] = empty
            ws["A3"].font = Font(italic=True, color="777777")
            ws.column_dimensions["A"].width = 40
            return ws
        for j, (h, wdt) in enumerate(cols, start=1):
            cell = ws.cell(3, j, h)
            cell.font, cell.fill, cell.border = head_font, head_fill, border
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(j)].width = wdt
        for i, row in enumerate(rows):
            for j, v in enumerate(row, start=1):
                cell = ws.cell(4 + i, j, _s(v) if not hasattr(v, "hyperlink_target") else v.text)
                cell.border, cell.alignment = border, wrap
                if i % 2:
                    cell.fill = zebra
                if hasattr(v, "hyperlink_target"):
                    cell.hyperlink = v.hyperlink_target
                    cell.font = Font(color="1F5FBF", underline="single")
        ws.freeze_panes = "A4"
        ws.auto_filter.ref = f"A3:{get_column_letter(len(cols))}{3 + len(rows)}"
        return ws

    class Link:
        def __init__(self, text: str, target: str):
            self.text, self.hyperlink_target = text, target

    sub = c.get("case_sub_stage")
    kv_sheet("Case", f"{title}  —  {pet} vs {res}", [
        ("__section__", "Case details"),
        ("Case number", c.get("case_number")), ("CNR", c.get("cnr")), ("Court", c.get("court_name")),
        ("Case type", c.get("case_type_raw")), ("Filing number", c.get("filing_number")),
        ("Filing date", c.get("filing_date")), ("Registration number", c.get("registration_number")),
        ("Registration date", c.get("registration_date")), ("e-Filing number / date",
                                                             f"{c.get('efiling_number') or '—'} / {c.get('efiling_date') or '—'}"),
        ("__section__", "Status"),
        ("Case status", c.get("case_status") + (f"  (Sub stage: {sub})" if sub else "")),
        ("Nature of disposal", c.get("nature_of_disposal")), ("First hearing date", c.get("first_hearing_date")),
        ("Decision date", c.get("decision_date")), ("Next hearing date", c.get("next_hearing_date") or "—"),
        ("Case stage", c.get("case_stage") or "—"), ("Court number and judge", c.get("court_number_and_judge")),
        ("__section__", "Parties"),
        ("Petitioner(s)", pet), ("Respondent(s)", res),
        ("__section__", "Summary"),
        ("Hearings", len(c.get("hearings", []))), ("Orders / judgments", len(c.get("orders", []))),
        ("PDF pages", sum((o.get("pages") or 0) for o in c.get("orders", []))),
        ("Interim applications", len(c.get("interim_applications") or [])),
        ("Main matter(s)", "; ".join(f"{m.get('case_no')} ({m.get('main_cnr')})" for m in c.get("main_matters", [])) or "—"),
        ("Record complete", "Yes" if c.get("complete") == 1 else "NO — see Provenance"),
    ])

    prow = []
    for role, key in (("Petitioner", "petitioners"), ("Respondent", "respondents")):
        for p in c.get(key, []):
            advs = p.get("advocates") or ([p["advocate"]] if p.get("advocate") else [])
            prow.append([role, p.get("seq"), p.get("name"), "; ".join(advs), "; ".join(p.get("extra_lines") or [])])
    table_sheet("Parties", "Parties and advocates", [("Role", 13), ("#", 5), ("Name", 45), ("Advocate(s)", 35), ("Other lines", 30)], prow)

    hrow = []
    for h in sorted(c.get("hearings", []), key=lambda x: x.get("seq") or 0):
        biz = h.get("business_text") or ("(no business link on the site)" if not h.get("vb_args") else "")
        hrow.append([h.get("seq"), h.get("business_on_date"), h.get("hearing_date"), h.get("purpose"), h.get("judge"),
                     biz, h.get("next_purpose"), h.get("next_hearing_date")])
    table_sheet("Hearings", f"Hearings, oldest first ({len(hrow)})",
                [("#", 5), ("Business on date", 13), ("Hearing date", 13), ("Purpose", 18), ("Judge", 30),
                 ("Business (daily status)", 70), ("Next purpose", 16), ("Next hearing", 13)], hrow)

    orow, trows = [], []
    for o in c.get("orders", []):
        path = _find_pdf(o, run_dir)
        link = Link("Open PDF", "file:///" + path.replace(os.sep, "/")) if path else "file not found locally"
        orow.append([o.get("seq"), o.get("section"), o.get("number"), o.get("order_date"), o.get("description"),
                     o.get("pages"), len(o.get("text") or ""), "yes" if o.get("needs_ocr") else "no", link, o.get("sha256")])
        trows.append(o)
    table_sheet("Orders", f"Orders and judgments ({len(orow)})",
                [("#", 5), ("Section", 22), ("No.", 6), ("Date", 12), ("Description", 40), ("Pages", 7),
                 ("Text chars", 10), ("Needs OCR", 10), ("PDF", 12), ("SHA-256", 24)], orow)

    ws = wb.create_sheet("Order Text")
    ws["A1"] = "Full text of each order (extracted from the PDF)"
    ws["A1"].font = Font(bold=True, size=14, color=HEAD_FILL)
    ws.column_dimensions["A"].width = 120
    r = 3
    if not trows:
        ws["A3"] = "No orders."
    for o in trows:
        hdr = ws.cell(r, 1, f"Order {o.get('number')} — {o.get('section')} — {o.get('order_date')} ({o.get('pages')} pages)")
        hdr.font, hdr.fill = head_font, head_fill
        r += 1
        for para in _paragraphs(o.get("text") or ""):
            cell = ws.cell(r, 1, para)
            cell.alignment = wrap
            r += 1
        r += 1

    ias = c.get("interim_applications") or []
    irow = [[ia.get("seq"), ia.get("ia_number"), "; ".join(ia.get("parties") or []), ia.get("filing_date"),
             ia.get("next_date"), ia.get("next_purpose"), ia.get("ia_status"), len(ia.get("business") or [])] for ia in ias]
    table_sheet("IA", "Interim applications", [("#", 5), ("IA number", 14), ("Parties", 45), ("Filed", 12), ("Next date", 12),
                                               ("Next purpose", 18), ("Status", 12), ("Business entries", 10)], irow,
                empty="No interim applications on the site.")

    ws = table_sheet("Acts & Processes", "Acts", [("#", 5), ("Act", 60), ("Section (as shown)", 22)],
                     [[a.get("seq"), a.get("act"), a.get("section_raw") or a.get("section")] for a in c.get("acts", [])])
    start = ws.max_row + 3
    ws.cell(start, 1, "Processes").font = Font(bold=True, size=14, color=HEAD_FILL)
    for j, h in enumerate(("#", "Process ID", "Title", "Date"), start=1):
        cell = ws.cell(start + 1, j, h)
        cell.font, cell.fill, cell.border = head_font, head_fill, border
    ws.column_dimensions["D"].width = 14
    for i, p in enumerate(c.get("processes", [])):
        for j, v in enumerate((p.get("seq"), p.get("process_id"), p.get("title"), p.get("date")), start=1):
            cell = ws.cell(start + 2 + i, j, _s(v))
            cell.border, cell.alignment = border, wrap

    ack = (c.get("ack") or {}).get("fields") or {}
    kv_sheet("Acknowledgement", "Acknowledgement / cause title", sorted(ack.items()) or [("—", "Not fetched")])

    mm = c.get("main_matters", [])
    kv_sheet("Provenance", "Provenance and completeness", [
        ("Record complete", "Yes" if c.get("complete") == 1 else "NO"),
        ("Business links fetched", f"{c.get('vb_fetched')}/{c.get('vb_links')}"),
        ("PDFs saved", f"{c.get('pdf_saved')}/{c.get('pdf_links')}"),
        ("Acknowledgement fetched", f"{c.get('ack_fetched')}/{c.get('ack_expected')}"),
        ("Missing", "; ".join(c.get("missing") or []) or "—"),
        ("Main Filing No. (site counter, not case data)", "; ".join(str(m.get("filing_no")) for m in mm) or "—"),
        ("Scraped at (UTC)", c.get("scraped_at")), ("Run", c.get("run_id")),
        ("Requests for this case", c.get("requests")), ("Seconds for this case", c.get("elapsed_s")),
        ("Source", "services.ecourts.gov.in (eCourts District Courts portal)"),
    ])
    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
    return wb


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m V2.excel_case", description="Write one case as a formatted Excel workbook.")
    ap.add_argument("cnr")
    ap.add_argument("--out", required=True, help="run directory (contains json/)")
    ap.add_argument("--xlsx", help="output file (default <out>/excel/<CNR>.xlsx)")
    a = ap.parse_args(argv)
    src = os.path.join(a.out, "json", a.cnr + ".json")
    if not os.path.exists(src):
        print(f"no such case: {src}", file=sys.stderr)
        return 1
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl is required: pip install openpyxl", file=sys.stderr)
        return 2
    with open(src, encoding="utf-8") as f:
        case = json.load(f)
    dest = a.xlsx or os.path.join(a.out, "excel", a.cnr + ".xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    build_workbook(case, a.out).save(dest)
    print(os.path.abspath(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
