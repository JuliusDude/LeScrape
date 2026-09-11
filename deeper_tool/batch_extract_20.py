"""
deeper_tool.batch_extract_20
============================
Batch extraction of 20 Execution Petition cases (EX) under:
- State: Karnataka (3)
- District: BENGALURU (20)
- Court Complex: City Civil Court Complex, Bangalore (1030135)
- Establishment: PRL. CITY CIVIL AND SESSIONS JUDGE (3)
- Case Type: EX - Execution Petition Under Order (23^3)

Extracts all deep case fields (Cases, Parties, Acts, Processes, Main Matters,
Hearings, and Final Orders) into SQLite and exports to Excel, JSON, and CSV.
"""

import os
import sys
import time
import logging

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from deeper_tool.db import DeeperDatabase
from deeper_tool.exporter import export_to_json, export_to_csv_flat, export_to_multisheet_excel
from deeper_tool.scraper import DeeperScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("deeper_tool.batch")


def main():
    t_start = time.time()
    print("=" * 80)
    print(" deeper_tool: Batch 20 Execution Cases Extraction")
    print(" Jurisdiction : Karnataka (3) -> BENGALURU (20)")
    print(" Complex      : City Civil Court Complex, Bangalore (1030135)")
    print(" Establishment: PRL. CITY CIVIL AND SESSIONS JUDGE (3)")
    print(" Case Type    : EX - Execution Petition Under Order (23^3)")
    print(" Batch Size   : 20 Cases (Deep Extraction)")
    print("=" * 80)

    out_dir = "deeper_tool"
    os.makedirs(out_dir, exist_ok=True)
    db_file = os.path.join(out_dir, "deeper_ecourts.db")
    excel_file = os.path.join(out_dir, "execution_cases_20_deep.xlsx")
    json_file = os.path.join(out_dir, "execution_cases_20_deep.json")
    csv_file = os.path.join(out_dir, "execution_cases_20_deep.csv")

    scraper = DeeperScraper(db_path=db_file, pacing=2.0)

    # 1. Initialize portal session handshake
    print("\n[1/4] Connecting to eCourts portal and establishing dynamic handshake...")
    scraper.init_session()

    # 2. Search for 2023 Disposed Execution Cases
    print("\n[2/4] Searching for Execution Cases (EX, Year: 2023, Status: Disposed)...")
    cases = scraper.search_cases(
        case_type="23^3",
        search_year="2023",
        case_status="Disposed",
        complex_code="1030135",
        state_code="3",
        dist_code="20",
        est_code="3",
    )

    if not cases:
        print("[-] Error: No search results returned from eCourts.")
        sys.exit(1)

    print(f"[+] Found {len(cases)} Execution Cases in registry! Selecting top 20 for deep extraction.")

    # 3. Deeply extract top 20 cases
    print("\n[3/4] Starting deep extraction of 20 cases with 2.0s pacing guardrail...")
    meta_info = {
        "court_name": "PRL. CITY CIVIL AND SESSIONS JUDGE",
        "state_code": "3",
        "dist_code": "20",
        "court_complex_code": "1030135",
        "est_code": "3",
    }
    extracted_cases = scraper.batch_extract(cases, limit=20, meta=meta_info)

    if not extracted_cases:
        print("[-] Error: No cases could be extracted.")
        sys.exit(1)

    cnr_list = [c.cnr_number for c in extracted_cases]

    # 4. Export artifacts
    print("\n[4/4] Exporting 20-case dataset to Excel, JSON, and CSV...")
    db = DeeperDatabase(db_file)
    export_to_multisheet_excel(db, excel_file, cnr_list=cnr_list)
    print(f"      [+] Multi-sheet Styled Excel : {excel_file}")
    export_to_json(db, json_file, cnr_list=cnr_list)
    print(f"      [+] Complete Nested JSON     : {json_file}")
    export_to_csv_flat(db, csv_file, cnr_list=cnr_list)
    print(f"      [+] Summary Flat CSV         : {csv_file}")
    print(f"      [+] Relational SQLite DB     : {db_file}")

    total_time = time.time() - t_start
    total_hearings = sum(len(c.hearings) for c in extracted_cases)
    total_processes = sum(len(c.processes) for c in extracted_cases)
    total_main_matters = sum(len(c.main_matters) for c in extracted_cases)
    total_orders = sum(len(c.orders) for c in extracted_cases)
    total_parties = sum(len(c.parties) for c in extracted_cases)

    print("\n" + "=" * 80)
    print(" BATCH EXTRACTION SUMMARY")
    print("=" * 80)
    print(f" Cases Extracted       : {len(extracted_cases)}")
    print(f" Parties & Advocates   : {total_parties}")
    print(f" Chronological Hearings: {total_hearings}")
    print(f" Court Processes       : {total_processes}")
    print(f" Connected Main Matters: {total_main_matters}")
    print(f" Judicial Orders       : {total_orders}")
    print(f" Total Elapsed Time    : {total_time:.2f} seconds (~{total_time/60:.1f} minutes)")
    print(f" Average Time per Case : {total_time / len(extracted_cases):.2f} seconds")
    print("=" * 80)


if __name__ == "__main__":
    main()
