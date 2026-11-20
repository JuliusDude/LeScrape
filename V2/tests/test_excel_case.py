import json
import os
import tempfile
import unittest

try:
    import openpyxl  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

from V2.excel_case import _paragraphs, main


def _case():
    return {
        "cnr": "KABC010342962022", "case_number": "EX/16/2023", "court_name": "PRL. CITY CIVIL AND SESSIONS JUDGE",
        "case_status": "Case disposed", "case_sub_stage": "STEPS", "complete": 1,
        "petitioners": [{"seq": 1, "name": "H L L LIFECARE LIMITED", "advocates": ["M R RAVI RAJ MALALI"]}],
        "respondents": [{"seq": 1, "name": "R ONE"}, {"seq": 2, "name": "R TWO"}],
        "hearings": [{"seq": 2, "business_on_date": "20-08-2025", "business_text": "Case called out.", "vb_args": ["x"]},
                     {"seq": 1, "business_on_date": "02-01-2023", "business_text": "", "vb_args": []}],
        "orders": [{"seq": 1, "section": "Final Orders / Judgements", "number": "2", "order_date": "10-10-2025",
                    "pages": 1, "text": "The suit is dismissed.\nSd/-", "sha256": "ab" * 32}],
        "acts": [{"seq": 1, "act": "U/O 21 RULE 11 OF CPC", "section_raw": ","}],
        "processes": [{"seq": 1, "process_id": "P1", "title": "Precept", "date": "02-01-2023"}],
        "main_matters": [{"case_no": "O.S./1/2020", "main_cnr": "KABC010000000001", "filing_no": "8362"}],
        "ack": {"fields": {"Decree Holder": "H L L LIFECARE LIMITED"}},
        "interim_applications": [],
    }


@unittest.skipUnless(HAVE, "openpyxl not installed")
class TestExcelCase(unittest.TestCase):
    def test_workbook_sheets_and_content(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "json"))
            with open(os.path.join(td, "json", "C1.json"), "w", encoding="utf-8") as f:
                json.dump(_case(), f)
            self.assertEqual(main(["C1", "--out", td]), 0)
            wb = openpyxl.load_workbook(os.path.join(td, "excel", "C1.xlsx"))
            self.assertEqual(wb.sheetnames, ["Case", "Parties", "Hearings", "Orders", "Order Text", "IA",
                                             "Acts & Processes", "Acknowledgement", "Provenance"])
            case_vals = [c.value for c in wb["Case"]["B"]]
            self.assertIn("Case disposed  (Sub stage: STEPS)", case_vals)
            self.assertEqual(wb["Parties"].max_row, 6)                       # heading + header + 3 parties
            h = wb["Hearings"]
            self.assertEqual(h["B4"].value, "02-01-2023")                     # oldest first
            self.assertEqual(h["F4"].value, "(no business link on the site)")
            self.assertEqual(h["F5"].value, "Case called out.")
            self.assertEqual(wb["Orders"]["I4"].value, "file not found locally")
            self.assertIn("The suit is dismissed.", [c.value for c in wb["Order Text"]["A"]][3])
            self.assertEqual(wb["IA"]["A3"].value, "No interim applications on the site.")

    def test_missing_case(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "json"))
            self.assertEqual(main(["NOPE", "--out", td]), 1)


class TestParagraphs(unittest.TestCase):
    def test_reflows_hard_wrapped_pdf_text(self):
        text = "\n".join(["word " * 20 + "end."] * 10) + "\n\nNew para."
        paras = _paragraphs(text, target=300)
        self.assertGreater(len(paras), 1)
        self.assertTrue(all(len(p) < 1500 for p in paras))
        self.assertEqual(paras[-1], "New para.")
        self.assertNotIn("\n", "".join(paras))


if __name__ == "__main__":
    unittest.main()
