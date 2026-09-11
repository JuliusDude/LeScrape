"""
deeper_tool.run_single_case
===========================
Executes extraction of 1 case of EX under Karnataka, Bengaluru,
City Civil Court Complex, Prl. City Civil and Sessions Judge.
Outputs to SQLite database, multi-sheet Excel workbook, JSON, and CSV.
"""

import os
import sys
import logging

# Ensure repository root is on sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from deeper_tool.db import DeeperDatabase
from deeper_tool.exporter import export_to_json, export_to_csv_flat, export_to_multisheet_excel
from deeper_tool.parser import parse_deeper_case_detail
from deeper_tool.scraper import DeeperScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("deeper_tool")


def main():
    print("=" * 75)
    print(" deeper_tool: Execution Case Extraction")
    print(" Jurisdiction: Karnataka -> Bengaluru -> City Civil Court Complex")
    print(" Establishment: PRL. CITY CIVIL AND SESSIONS JUDGE")
    print(" Case Type: EX - Execution Petition Under Order")
    print("=" * 75)

    out_dir = "deeper_tool"
    os.makedirs(out_dir, exist_ok=True)
    db_file = os.path.join(out_dir, "deeper_ecourts.db")
    excel_file = os.path.join(out_dir, "case_EX_2_2023.xlsx")
    json_file = os.path.join(out_dir, "case_EX_2_2023.json")
    csv_file = os.path.join(out_dir, "case_EX_2_2023.csv")

    fixture_candidates = [
        os.path.join(out_dir, "fixtures", "target_case_view.html"),
        "target_case_view.html"
    ]
    html_file = next((p for p in fixture_candidates if os.path.exists(p)), None)
    if not html_file:
        print(f"[-] Target HTML fixture not found locally. Initiating live fetch via DeeperScraper...")
        scraper = DeeperScraper(db_path=db_file)
        scraper.init_session()
        scraper.set_establishment(complex_code="1030135", state_code="3", dist_code="20", est_code="3")
        view_params = {
            "case_no": "202300000022023",
            "cino": "KABC010342242022",
            "court_code": "3",
            "hideparty": "",
            "search_flag": "CScaseNumber",
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030135",
            "search_by": "CScaseType",
        }
        case = scraper.scrape_and_save_case(view_params, meta={
            "court_name": "PRL. CITY CIVIL AND SESSIONS JUDGE",
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030135",
            "est_code": "3",
        })
    else:
        print(f"[+] Loading verified case HTML fragment from {html_file}...")
        with open(html_file, "r", encoding="utf-8") as f:
            html = f.read()

        case = parse_deeper_case_detail(html)
        case.court_name = "PRL. CITY CIVIL AND SESSIONS JUDGE"
        case.state_code = "3"
        case.dist_code = "20"
        case.court_complex_code = "1030135"
        case.est_code = "3"

        db = DeeperDatabase(db_file)
        db.save_case(case)

    db = DeeperDatabase(db_file)
    print("\n" + "-" * 75)
    print(f" [SUCCESS] Extracted Case: {case.case_number} ({case.cnr_number})")
    print("-" * 75)
    print(f" - Case Type           : {case.case_type}")
    print(f" - Filing Number/Date  : {case.filing_number} ({case.filing_date})")
    print(f" - Reg Number/Date     : {case.registration_number} ({case.registration_date})")
    print(f" - Case Status         : {case.case_status}")
    print(f" - Nature of Disposal  : {case.nature_of_disposal}")
    print(f" - Decision Date       : {case.decision_date}")
    print(f" - Presiding Coram     : {case.court_number_judge}")
    print(f" - Petitioners (Count) : {len(case.petitioners)} -> {', '.join(p.name for p in case.petitioners)}")
    print(f" - Respondents (Count) : {len(case.respondents)} -> {', '.join(r.name for r in case.respondents)}")
    print(f" - Statutory Acts      : {', '.join(f'{a.act} ({a.section})' for a in case.acts)}")
    print(f" - Processes (Count)   : {len(case.processes)}")
    for pr in case.processes:
        print(f"     * [{pr.process_id}] {pr.process_title} ({pr.process_date})")
    print(f" - Main Matters        : {len(case.main_matters)}")
    for mm in case.main_matters:
        print(f"     * Case No: {mm.main_case_number} | CNR: {mm.main_cnr_number} | Filing: {mm.main_filing_number}")
    print(f" - Hearings (Count)    : {len(case.hearings)} chronological hearings")
    print(f" - Orders (Count)      : {len(case.orders)}")
    for o in case.orders:
        print(f"     * Order #{o.order_number} ({o.order_date}): {o.order_details}")

    # Exports
    print("\n[+] Exporting artifacts...")
    export_to_multisheet_excel(db, excel_file, cnr_list=[case.cnr_number])
    print(f"    - Multi-sheet Styled Excel : {excel_file}")
    export_to_json(db, json_file, cnr_list=[case.cnr_number])
    print(f"    - Nested Rich JSON         : {json_file}")
    export_to_csv_flat(db, csv_file, cnr_list=[case.cnr_number])
    print(f"    - Summary Flat CSV         : {csv_file}")
    print(f"    - SQLite Relational DB     : {db_file}")

    print("=" * 75)
    print(" Extraction, Parsing & Export Completed Successfully!")
    print("=" * 75)


if __name__ == "__main__":
    main()
