import os
import tempfile
import unittest

from V2.view import main, render

HERE = os.path.dirname(__file__)


def _case():
    return {
        "cnr": "KABC010342962022", "case_number": "EX/16/2023", "court_name": "PRL. CITY CIVIL AND SESSIONS JUDGE",
        "case_status": "Case disposed", "case_sub_stage": "STEPS", "complete": 1,
        "petitioners": [{"seq": 1, "name": "H L L LIFECARE <LIMITED>", "advocates": ["M R RAVI RAJ MALALI"]}],
        "respondents": [{"seq": 1, "name": "M/S SUPER THREADS INDIA", "advocates": []},
                        {"seq": 2, "name": "SECOND R", "advocates": []}],
        "hearings": [{"seq": 2, "business_on_date": "20-08-2025", "business_text": "Case called out.", "vb_args": ["x"]},
                     {"seq": 1, "business_on_date": "02-01-2023", "business_text": "", "vb_args": []}],
        "orders": [{"section": "Final Orders / Judgements", "number": "2", "text": "The suit is dismissed.", "pages": 1,
                    "pdf_path": "", "sha256": "e9f7"}],
        "main_matters": [{"seq": 1, "cnr": "KABC010342962022", "case_no": "O.S./1/2020", "main_cnr": "KABC010000000001",
                          "filing_no": "8361", "filing_no_suspect": 1}],
        "ack": {"fields": {"Decree Holder": "H L L LIFECARE LIMITED"}},
    }


class TestView(unittest.TestCase):
    def test_render_contains_every_section_and_escapes(self):
        h = render(_case())
        for s in ("Case details", "Petitioners", "Respondents", "Acts", "Processes", "Main matters",
                  "Hearings (oldest first)", "Orders and judgments", "Acknowledgement", "Provenance"):
            self.assertIn(s, h)
        self.assertIn("H L L LIFECARE &lt;LIMITED&gt;", h)          # escaped, no raw HTML injection
        self.assertNotIn("<LIMITED>", h)
        self.assertIn("SECOND R", h)
        self.assertIn("The suit is dismissed.", h)
        self.assertIn("no link on site", h)
        self.assertIn("site artefact", h)
        self.assertIn("<td>KABC010000000001</td>", h)                  # main CNR, not the case's own CNR
        self.assertNotIn("None/None", h)
        self.assertLess(h.index("02-01-2023"), h.index("20-08-2025"))   # hearings oldest first
        self.assertIn('class="badge ok"', h)

    def test_cli_writes_file(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "json"))
            with open(os.path.join(td, "json", "KABC010342962022.json"), "w", encoding="utf-8") as f:
                json.dump(_case(), f)
            self.assertEqual(main(["KABC010342962022", "--out", td]), 0)
            self.assertTrue(os.path.exists(os.path.join(td, "view", "KABC010342962022.html")))
            self.assertEqual(main(["NOPE", "--out", td]), 1)


class TestFindPdf(unittest.TestCase):
    def test_pdf_found_by_sha_when_stored_path_is_stale(self):
        from V2.view import _find_pdf
        with tempfile.TemporaryDirectory() as td:
            sha = "ab" + "c" * 62
            os.makedirs(os.path.join(td, "pdfs", "ab"))
            open(os.path.join(td, "pdfs", "ab", sha + ".pdf"), "wb").write(b"%PDF-1.4")
            got = _find_pdf({"sha256": sha, "pdf_path": "alternate_tool/output/old/pdfs/ab/x.pdf"}, td)
            self.assertTrue(got.endswith(sha + ".pdf"))
            self.assertEqual(_find_pdf({"sha256": "zz", "pdf_path": "nope.pdf"}, td), "")


if __name__ == "__main__":
    unittest.main()
