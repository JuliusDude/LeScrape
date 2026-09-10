"""
Unit and boundary tests for ecourts.parser.hierarchy.
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases)
for Feature 1 (Hierarchy Dropdown Cascades) and Feature 2 (Compound Token Parsing).
"""

from __future__ import annotations

import json
import unittest

from ecourts.parser.hierarchy import (
    complex_code_numeric,
    parse_case_types,
    parse_complexes,
    parse_districts,
    parse_dropdown_options,
    parse_establishments,
    split_case_type_token,
    split_complex_token,
    unwrap_json_or_dict,
)


class TestHierarchyFeature1Tier1(unittest.TestCase):
    """Tier 1 (Feature Coverage): Feature 1 - Hierarchy Dropdown Cascade (>= 5 tests)."""

    def test_f1_parse_districts_from_json(self):
        """Verify parse_districts extracts code and name from fillDistrict JSON."""
        response_json = json.dumps({
            "dist_list": (
                "<option value='' >Select district</option>"
                "<option value=2 >BAGALKOT</option>"
                "<option value=20 >BENGALURU</option>"
                "<option value=1 >BELAGAVI</option>"
            )
        })
        districts = parse_districts(response_json)
        self.assertEqual(len(districts), 3)
        self.assertEqual(districts[0], {"code": "2", "name": "BAGALKOT"})
        self.assertEqual(districts[1], {"code": "20", "name": "BENGALURU"})
        self.assertEqual(districts[2], {"code": "1", "name": "BELAGAVI"})

    def test_f1_parse_districts_from_dict(self):
        """Verify parse_districts works when response is already a deserialized Python dict."""
        data = {
            "dist_list": "<option value='15'>MYSURU</option><option value='16'>MANDYA</option>"
        }
        districts = parse_districts(data)
        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]["code"], "15")
        self.assertEqual(districts[1]["name"], "MANDYA")

    def test_f1_parse_complexes_dropdown(self):
        """Verify parse_complexes extracts all court complexes under a district."""
        data = {
            "complex_list": (
                "<option value=''>Select Court Complex</option>"
                "<option value='1030134@2,5@Y'>City Civil Court</option>"
                "<option value='1030135@3@Y'>Family Court Complex</option>"
            )
        }
        complexes = parse_complexes(data)
        self.assertEqual(len(complexes), 2)
        self.assertEqual(complexes[0]["name"], "City Civil Court")
        self.assertEqual(complexes[1]["name"], "Family Court Complex")

    def test_f1_parse_establishments_dropdown(self):
        """Verify parse_establishments extracts establishments from fillCourtEstablishment."""
        html = (
            "<option value=''>Select court establishment</option>"
            "<option value='3'>PRL. CITY CIVIL AND SESSIONS JUDGE</option>"
            "<option value='15'>PRL. JUDGE FAMILY COURT</option>"
        )
        data = {"establishment_list": html}
        establishments = parse_establishments(data)
        self.assertEqual(len(establishments), 2)
        self.assertEqual(establishments[0], {"code": "3", "name": "PRL. CITY CIVIL AND SESSIONS JUDGE"})
        self.assertEqual(establishments[1], {"code": "15", "name": "PRL. JUDGE FAMILY COURT"})

    def test_f1_parse_case_types_dropdown(self):
        """Verify parse_case_types extracts case types under court establishment."""
        html = (
            "<option value=''>Select Case Type</option>"
            "<option value='12^3'>C.C. - CRIMINAL CASES</option>"
            "<option value='1^3'>A.C. - Arbitration Cases</option>"
        )
        case_types = parse_case_types({"casetype_list": html})
        self.assertEqual(len(case_types), 2)
        self.assertEqual(case_types[0]["name"], "C.C. - CRIMINAL CASES")
        self.assertEqual(case_types[1]["name"], "A.C. - Arbitration Cases")


class TestHierarchyFeature2Tier1(unittest.TestCase):
    """Tier 1 (Feature Coverage): Feature 2 - Compound Token Parsing (>= 5 tests)."""

    def test_f2_split_complex_token_single_est(self):
        """Verify split_complex_token parses single establishment complex tokens."""
        num, ests, flag = split_complex_token("1030135@3@Y")
        self.assertEqual(num, "1030135")
        self.assertEqual(ests, ["3"])
        self.assertEqual(flag, "Y")

    def test_f2_split_complex_token_multi_est(self):
        """Verify split_complex_token parses comma-separated establishment lists."""
        token = "1030136@15,16,17,18,19,20,21,23@Y"
        num, ests, flag = split_complex_token(token)
        self.assertEqual(num, "1030136")
        self.assertEqual(ests, ["15", "16", "17", "18", "19", "20", "21", "23"])
        self.assertEqual(flag, "Y")

    def test_f2_complex_code_numeric_prefix(self):
        """Verify complex_code_numeric helper isolates numeric complex code prefix."""
        self.assertEqual(complex_code_numeric("1030136@15,16@Y"), "1030136")
        self.assertEqual(complex_code_numeric("1030135"), "1030135")
        self.assertEqual(complex_code_numeric(" 9999 @1@N "), "9999")

    def test_f2_split_case_type_token(self):
        """Verify split_case_type_token decomposes {type_code}^{est_code}."""
        t_code, est_code = split_case_type_token("12^3")
        self.assertEqual(t_code, "12")
        self.assertEqual(est_code, "3")

    def test_f2_parse_case_types_token_enrichment(self):
        """Verify parse_case_types enriches returned dicts with type_code and est_code."""
        data = {"casetype_list": "<option value='49^15'>Misc - MISCELLANEOUS CASE</option>"}
        enriched = parse_case_types(data)
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["type_code"], "49")
        self.assertEqual(enriched[0]["est_code"], "15")
        self.assertEqual(enriched[0]["code"], "49^15")


class TestHierarchyFeature1Tier2(unittest.TestCase):
    """Tier 2 (Boundary & Corner Cases): Feature 1 - Hierarchy Cascades (>= 5 tests)."""

    def test_f1_empty_responses_graceful(self):
        """Verify empty string or empty dict inputs return empty lists without crashing."""
        self.assertEqual(parse_districts(""), [])
        self.assertEqual(parse_districts({}), [])
        self.assertEqual(parse_complexes(""), [])
        self.assertEqual(parse_establishments(""), [])
        self.assertEqual(parse_case_types(""), [])

    def test_f1_raw_html_without_json_wrapper(self):
        """Verify dropdown parser parses direct raw HTML string not wrapped in JSON."""
        raw_html = "<option value='10'>DISTRICT TEN</option><option value='20'>DISTRICT TWENTY</option>"
        districts = parse_dropdown_options(raw_html)
        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]["code"], "10")
        self.assertEqual(districts[1]["code"], "20")

    def test_f1_placeholder_and_whitespace_filtering(self):
        """Verify options starting with 'Select', empty values, or spaces are filtered out."""
        html = (
            "<option value=''>Select District</option>"
            "<option value=' '>   </option>"
            "<option value='0'>SELECT A COURT</option>"
            "<option value='99'>   DISTRICT 99   </option>"
        )
        opts = parse_dropdown_options(html)
        self.assertEqual(len(opts), 1)
        self.assertEqual(opts[0]["code"], "99")
        self.assertEqual(opts[0]["name"], "DISTRICT 99")

    def test_f1_corrupted_json_fallback(self):
        """Verify corrupted JSON string gracefully falls back to empty list."""
        self.assertEqual(parse_districts("{corrupted json"), [])
        self.assertEqual(unwrap_json_or_dict("{invalid"), {})
        self.assertEqual(unwrap_json_or_dict(None), {})  # type: ignore

    def test_f1_options_without_value_attribute(self):
        """Verify option tags lacking value attribute are skipped."""
        html = "<option>Plain Option Without Value</option><option value='1'>Valid</option>"
        opts = parse_dropdown_options(html)
        self.assertEqual(len(opts), 1)
        self.assertEqual(opts[0]["code"], "1")


class TestHierarchyFeature2Tier2(unittest.TestCase):
    """Tier 2 (Boundary & Corner Cases): Feature 2 - Compound Tokens (>= 5 tests)."""

    def test_f2_complex_token_missing_delimiters(self):
        """Verify split_complex_token handles missing @ delimiters without crashing."""
        num, ests, flag = split_complex_token("1030135")
        self.assertEqual(num, "1030135")
        self.assertEqual(ests, [])
        self.assertEqual(flag, "")

    def test_f2_complex_token_partial_delimiters(self):
        """Verify split_complex_token handles trailing @ with missing flag."""
        num, ests, flag = split_complex_token("1030135@3@")
        self.assertEqual(num, "1030135")
        self.assertEqual(ests, ["3"])
        self.assertEqual(flag, "")

    def test_f2_complex_token_empty_establishments(self):
        """Verify split_complex_token handles empty middle establishment segment."""
        num, ests, flag = split_complex_token("1030135@@Y")
        self.assertEqual(num, "1030135")
        self.assertEqual(ests, [])
        self.assertEqual(flag, "Y")

    def test_f2_case_type_missing_caret(self):
        """Verify split_case_type_token handles missing caret separator gracefully."""
        t_code, est = split_case_type_token("49")
        self.assertEqual(t_code, "49")
        self.assertEqual(est, "")

    def test_f2_case_type_spaces_around_caret(self):
        """Verify split_case_type_token strips whitespace surrounding caret."""
        t_code, est = split_case_type_token("  71 ^ 3  ")
        self.assertEqual(t_code, "71")
        self.assertEqual(est, "3")


if __name__ == "__main__":
    unittest.main()
