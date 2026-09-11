"""
deeper_tool.test_deeper
=======================
Automated test suite verifying the deeper extraction model, parser,
database persistence, and exporter against live fixture data.
"""

import os
import sys
import unittest
import tempfile

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from deeper_tool.models import (
    ActItem,
    DeeperCaseDetail,
    HearingItem,
    MainMatterItem,
    OrderItem,
    PartyItem,
    ProcessItem,
)
from deeper_tool.parser import parse_deeper_case_detail, extract_cnr
from deeper_tool.db import DeeperDatabase
from deeper_tool.exporter import export_to_json, export_to_csv_flat, export_to_multisheet_excel


class TestDeeperModelsAndParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html_path = os.path.join(repo_root, "deeper_tool", "fixtures", "target_case_view.html")
        if not os.path.exists(html_path):
            html_path = os.path.join(repo_root, "target_case_view.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                cls.html = f.read()
        else:
            cls.html = ""

    def test_extract_cnr(self):
        self.assertEqual(extract_cnr("CNR: KABC010342242022 (Note the CNR)"), "KABC010342242022")
        self.assertEqual(extract_cnr("None"), None)
        self.assertEqual(extract_cnr(""), None)

    def test_parse_target_case(self):
        if not self.html:
            self.skipTest("target_case_view.html fixture not found")

        case = parse_deeper_case_detail(self.html)
        self.assertEqual(case.cnr_number, "KABC010342242022")
        self.assertEqual(case.case_type, "EX - Execution Petition Under Order")
        self.assertEqual(case.filing_number, "2786/2022")
        self.assertEqual(case.filing_date, "17-12-2022")
        self.assertEqual(case.registration_number, "2/2023")
        self.assertEqual(case.registration_date, "02-01-2023")
        self.assertEqual(case.case_status, "Case disposed")
        self.assertEqual(case.nature_of_disposal, "Uncontested--DISMISSED")
        self.assertEqual(case.decision_date, "06th December 2025")
        self.assertIn("1147-CCH64", case.court_number_judge)

        # Parties
        self.assertEqual(len(case.petitioners), 1)
        self.assertEqual(case.petitioners[0].name, "KRISHNAMURTHY G")
        self.assertEqual(case.petitioners[0].advocate, "DINESH J S")

        self.assertEqual(len(case.respondents), 1)
        self.assertEqual(case.respondents[0].name, "SATHISH M")

        # Acts
        self.assertEqual(len(case.acts), 1)
        self.assertIn("U/O 21 RULE 11 OF CPC", case.acts[0].act)

        # Processes
        self.assertEqual(len(case.processes), 1)
        self.assertEqual(case.processes[0].process_id, "PKABC010342242022_1_1")
        self.assertIn("Notice to show cause", case.processes[0].process_title)
        self.assertEqual(case.processes[0].process_date, "07-01-2023")

        # Main Matters
        self.assertEqual(len(case.main_matters), 1)
        self.assertEqual(case.main_matters[0].main_case_number, "O.S./0004555/2020")
        self.assertEqual(case.main_matters[0].main_cnr_number, "KABC010166632020")
        self.assertEqual(case.main_matters[0].main_filing_number, "6849")

        # Hearings
        self.assertEqual(len(case.hearings), 32)
        self.assertEqual(case.hearings[0].judge, "CCH64 LXIII ADDL. CITY CIVIL AND SESSIONS JUDGE")
        self.assertEqual(case.hearings[0].business_date, "06-12-2025")
        self.assertEqual(case.hearings[0].purpose, "Disposed")

        # Orders
        self.assertEqual(len(case.orders), 1)
        self.assertEqual(case.orders[0].order_number, "1")
        self.assertEqual(case.orders[0].order_date, "06-12-2025")
        self.assertEqual(case.orders[0].order_details, "Judgment")

    def test_database_and_exporters(self):
        if not self.html:
            self.skipTest("target_case_view.html fixture not found")

        case = parse_deeper_case_detail(self.html)
        case.court_name = "PRL. CITY CIVIL AND SESSIONS JUDGE"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = DeeperDatabase(db_path)
            saved_cnr = db.save_case(case)
            self.assertEqual(saved_cnr, case.cnr_number)

            fetched = db.get_case(saved_cnr)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.cnr_number, case.cnr_number)
            self.assertEqual(len(fetched.processes), 1)
            self.assertEqual(len(fetched.main_matters), 1)
            self.assertEqual(len(fetched.orders), 1)
            self.assertEqual(len(fetched.hearings), 32)

            # Test JSON Export
            json_p = os.path.join(tmpdir, "out.json")
            export_to_json(db, json_p)
            self.assertTrue(os.path.exists(json_p))
            self.assertGreater(os.path.getsize(json_p), 1000)

            # Test CSV Export
            csv_p = os.path.join(tmpdir, "out.csv")
            export_to_csv_flat(db, csv_p)
            self.assertTrue(os.path.exists(csv_p))
            self.assertGreater(os.path.getsize(csv_p), 200)

            # Test Excel Export
            xlsx_p = os.path.join(tmpdir, "out.xlsx")
            export_to_multisheet_excel(db, xlsx_p)
            self.assertTrue(os.path.exists(xlsx_p))
            self.assertGreater(os.path.getsize(xlsx_p), 2000)


if __name__ == "__main__":
    unittest.main()
