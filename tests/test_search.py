"""
Unit and boundary tests for ecourts.parser.search.
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases)
for Feature 3 (Case Search Execution) and Feature 4 (Search Result Table & CNR Extraction).
"""

from __future__ import annotations

import json
import unittest

from ecourts.parser.search import (
    VIEWHISTORY_RE,
    WrongCaptchaError,
    build_search_payload,
    extract_cnr_from_onclick,
    parse_case_results,
    parse_view_params,
)


class TestSearchFeature3Tier1(unittest.TestCase):
    """Tier 1 (Feature Coverage): Feature 3 - Case Search Execution (>= 5 tests)."""

    def test_f3_build_search_payload_full_parameters(self):
        """Verify build_search_payload constructs all required fields with numeric complex code."""
        payload = build_search_payload(
            state_code="3",
            dist_code="20",
            court_complex_code="1030136@15,16@Y",
            est_code="15",
            case_type_value="49^15",
            search_year="2025",
            case_status="Pending",
            captcha_code="abc99",
        )
        self.assertEqual(payload["state_code"], "3")
        self.assertEqual(payload["dist_code"], "20")
        self.assertEqual(payload["court_complex_code"], "1030136")  # Stripped @ suffix
        self.assertEqual(payload["est_code"], "15")
        self.assertEqual(payload["case_type_1"], "49^15")
        self.assertEqual(payload["search_year"], "2025")
        self.assertEqual(payload["case_status"], "Pending")
        self.assertEqual(payload["ct_captcha_code"], "abc99")
        self.assertEqual(payload["ajax_req"], "true")
        self.assertEqual(payload["app_token"], "")

    def test_f3_build_search_payload_disposed_status(self):
        """Verify build_search_payload accepts 'Disposed' case status filter."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2024", case_status="Disposed")
        self.assertEqual(p["case_status"], "Disposed")

    def test_f3_build_search_payload_both_status(self):
        """Verify build_search_payload accepts 'Both' case status filter."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2024", case_status="Both")
        self.assertEqual(p["case_status"], "Both")

    def test_f3_build_search_payload_numeric_complex_without_at(self):
        """Verify court_complex_code already numeric remains unmodified."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2024")
        self.assertEqual(p["court_complex_code"], "1030135")

    def test_f3_build_search_payload_stringifies_year_and_codes(self):
        """Verify integer inputs for year are safely stringified."""
        p = build_search_payload("3", "20", "1030135", "3", "1^3", 2023)
        self.assertEqual(p["state_code"], "3")
        self.assertEqual(p["dist_code"], "20")
        self.assertEqual(p["search_year"], "2023")


class TestSearchFeature4Tier1(unittest.TestCase):
    """Tier 1 (Feature Coverage): Feature 4 - Search Result & CNR Extraction (>= 5 tests)."""

    def test_f4_parse_view_params_9_arguments(self):
        """Verify parse_view_params extracts all 9 positional arguments from viewHistory(...)."""
        onclick = "viewHistory(204900000022025,'KABC0E0001992025',15,'','CScaseNumber',3,20,1030136,'CScaseType')"
        params = parse_view_params(onclick)
        self.assertIsNotNone(params)
        self.assertEqual(params["case_no"], "204900000022025")
        self.assertEqual(params["cino"], "KABC0E0001992025")
        self.assertEqual(params["court_code"], "15")
        self.assertEqual(params["hideparty"], "")
        self.assertEqual(params["search_flag"], "CScaseNumber")
        self.assertEqual(params["state_code"], "3")
        self.assertEqual(params["dist_code"], "20")
        self.assertEqual(params["court_complex_code"], "1030136")
        self.assertEqual(params["search_by"], "CScaseType")

    def test_f4_extract_cnr_from_onclick(self):
        """Verify extract_cnr_from_onclick extracts 16-character CNR string."""
        onclick = "viewHistory(204900000022025,'KABC0E0001992025',15,'','CScaseNumber',3,20,1030136,'CScaseType')"
        cnr = extract_cnr_from_onclick(onclick)
        self.assertEqual(cnr, "KABC0E0001992025")

    def test_f4_parse_case_results_successful_table(self):
        """Verify parse_case_results extracts table rows, court heading, parties, and links."""
        sample_html = """
        <table class="table-fixed" id="dispTable">
            <tr>
                <th colspan="3" id="td_court_name_15">PRL. JUDGE FAMILY COURT, BENGALURU</th>
            </tr>
            <tr>
                <td>1</td>
                <td>Misc/2/2025</td>
                <td>RAMESH SHARMA<br>Vs</br>SITA SHARMA</td>
                <td><a href="#" onClick="viewHistory(204900000022025,'KABC0E0001992025',15,'','CScaseNumber',3,20,1030136,'CScaseType')">View</a></td>
            </tr>
        </table>
        """
        resp_json = json.dumps({"status": 1, "case_data": sample_html})
        cases = parse_case_results(resp_json)
        self.assertEqual(len(cases), 1)

        c = cases[0]
        self.assertEqual(c["sr_no"], "1")
        self.assertEqual(c["case_number"], "Misc/2/2025")
        self.assertEqual(c["petitioner"], "RAMESH SHARMA")
        self.assertEqual(c["respondent"], "SITA SHARMA")
        self.assertEqual(c["court_name"], "PRL. JUDGE FAMILY COURT, BENGALURU")
        self.assertEqual(c["cnr_number"], "KABC0E0001992025")
        self.assertIsNotNone(c["view_params"])

    def test_f4_parse_case_results_record_not_found(self):
        """Verify parse_case_results returns empty list when 'Record not found' is returned."""
        not_found_html = "<div class='col border-top pt-1' align='center' id='nodata' style='color:red' >Record not found</div>"
        data = {"case_data": not_found_html, "status": 1}
        cases = parse_case_results(data)
        self.assertEqual(cases, [])

    def test_f4_parse_case_results_captcha_rejection_error(self):
        """Verify parse_case_results raises WrongCaptchaError on server rejection."""
        with self.assertRaises(WrongCaptchaError):
            parse_case_results({"errormsg": "Invalid Captcha", "status": 0})


class TestSearchFeature3Tier2(unittest.TestCase):
    """Tier 2 (Boundary & Corner Cases): Feature 3 - Case Search Execution (>= 5 tests)."""

    def test_f3_search_payload_empty_captcha(self):
        """Verify build_search_payload allows initial empty captcha before solving."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2025", captcha_code="")
        self.assertEqual(p["ct_captcha_code"], "")

    def test_f3_search_payload_complex_with_multiple_at(self):
        """Verify court_complex_code with multiple @ tokens strips to initial numeric ID."""
        p = build_search_payload("3", "20", "1030136@15,16,17@Y@extra", "15", "49^15", "2025")
        self.assertEqual(p["court_complex_code"], "1030136")

    def test_f3_search_payload_default_status(self):
        """Verify case_status defaults to 'Pending' if unspecified."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2025")
        self.assertEqual(p["case_status"], "Pending")

    def test_f3_search_payload_keys_exact_match(self):
        """Verify all verified eCourts parameter keys are present in payload."""
        expected_keys = {
            "case_type_1", "search_year", "case_status", "ct_captcha_code",
            "state_code", "dist_code", "court_complex_code", "est_code",
            "ajax_req", "app_token",
        }
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "2025")
        self.assertEqual(set(p.keys()), expected_keys)

    def test_f3_search_payload_year_boundary_strings(self):
        """Verify search year formats such as 4-digit strings are preserved."""
        p = build_search_payload("3", "20", "1030135", "3", "12^3", "1999")
        self.assertEqual(p["search_year"], "1999")


class TestSearchFeature4Tier2(unittest.TestCase):
    """Tier 2 (Boundary & Corner Cases): Feature 4 - Result Table & CNR Extraction (>= 5 tests)."""

    def test_f4_party_parsing_without_vs(self):
        """Verify party parsing when 'Vs' is absent (single party or ex-parte)."""
        html = """
        <table class="table-fixed">
            <tr>
                <td>1</td>
                <td>Misc/10/2025</td>
                <td>STATE OF KARNATAKA</td>
                <td><a href="#" onclick="viewHistory(100,'KABC0E0001002025',15,'','',3,20,1030136,'')">View</a></td>
            </tr>
        </table>
        """
        cases = parse_case_results(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["petitioner"], "STATE OF KARNATAKA")
        self.assertEqual(cases[0]["respondent"], "")

    def test_f4_duplicate_table_ids_handling(self):
        """Verify parser functions when table contains multiple invalid duplicate id attributes."""
        html = """
        <table id="dispTable" id="titlehid" class="table-fixed">
            <tr>
                <td>1</td>
                <td>OS/1/2025</td>
                <td>ALICE Vs BOB</td>
                <td><a href="#" onClick="viewHistory(1,'KABC0E0000012025',15,'','',3,20,1030136,'')">View</a></td>
            </tr>
        </table>
        """
        cases = parse_case_results(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["cnr_number"], "KABC0E0000012025")

    def test_f4_malformed_onclick_syntax_fallback(self):
        """Verify parse_view_params returns None and extract_cnr uses regex fallback on malformed onclick."""
        bad_onclick = "doSomethingElse('KABC0E0009992025')"
        self.assertIsNone(parse_view_params(bad_onclick))
        self.assertEqual(extract_cnr_from_onclick(bad_onclick), "KABC0E0009992025")

    def test_f4_empty_or_truncated_table_rows_skipped(self):
        """Verify rows with fewer than 4 cells are skipped without error."""
        html = """
        <table class="table-fixed">
            <tr><td>Only one cell</td></tr>
            <tr><td>1</td><td>2</td><td>3</td></tr>
        </table>
        """
        cases = parse_case_results(html)
        self.assertEqual(cases, [])

    def test_f4_case_insensitive_vs_splitting(self):
        """Verify party parsing splits correctly on lowercase 'vs'."""
        html = """
        <table class="table-fixed">
            <tr>
                <td>1</td>
                <td>Misc/5/2025</td>
                <td>JOHN DOE vs JANE DOE</td>
                <td><a href="#" onclick="viewHistory(5,'KABC0E0000052025',15,'','',3,20,1030136,'')">View</a></td>
            </tr>
        </table>
        """
        cases = parse_case_results(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["petitioner"], "JOHN DOE")
        self.assertEqual(cases[0]["respondent"], "JANE DOE")


if __name__ == "__main__":
    unittest.main()
