"""
Unit and boundary tests for ecourts.parser.case_details.
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases) for:
  - Feature 5: Deep Case Record Parsing
  - Feature 6: Party Roster & Advocate Parsing
  - Feature 7: Statutory Acts & Sections Parsing
  - Feature 8: Hearing History Extraction
  - Feature 9: Edge Cases & Malformed HTML Handling
"""

from __future__ import annotations

import json
import os
import unittest

from ecourts.parser.case_details import (
    CaseDetail,
    extract_cnr,
    parse_case_detail,
)


class TestCaseDetailsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_case_detail.html")
        with open(fixture_path, "r", encoding="utf-8") as f:
            cls.sample_html = f.read()


# ── Feature 5: Deep Case Record Parsing ─────────────────────────────────────────

class TestFeature5Tier1(TestCaseDetailsBase):
    """Tier 1: Feature 5 - Deep Case Record Parsing (>= 5 tests)."""

    def test_f5_parse_sample_fixture_metadata(self):
        """Verify parse_case_detail extracts core classification and dates."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(detail.cnr_number, "KABC0E0001992025")
        self.assertIn("Misc", detail.case_type)
        self.assertEqual(detail.filing_number, "204900000022025")
        self.assertEqual(detail.filing_date, "15-01-2025")
        self.assertEqual(detail.registration_number, "Misc/2/2025")
        self.assertEqual(detail.registration_date, "16-01-2025")

    def test_f5_parse_case_stage_and_judge(self):
        """Verify case stage and presiding judge / court hall are extracted."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(detail.stage, "Evidence")
        self.assertEqual(detail.court_number_judge, "15 - PRL. JUDGE FAMILY COURT")

    def test_f5_parse_court_heading(self):
        """Verify court name extracted from h2#chHeading."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertIn("FAMILY COURT, BENGALURU", detail.court_name)

    def test_f5_extract_cnr_with_trailing_qr_text(self):
        """Verify extract_cnr isolates CNR string despite trailing QR notice."""
        raw = "KABC0E0001992025 (Note: Always quote CNR Number for quick reference)"
        cnr = extract_cnr(raw)
        self.assertEqual(cnr, "KABC0E0001992025")

    def test_f5_case_detail_to_dict_conversion(self):
        """Verify CaseDetail.to_dict generates dictionary suitable for storage and export."""
        detail = CaseDetail(
            cnr_number="KABC0E0001992025",
            case_type="Misc",
            stage="Evidence",
            coram="Judge 1",
        )
        d = detail.to_dict()
        self.assertEqual(d["cnr_number"], "KABC0E0001992025")
        self.assertEqual(d["case_stage"], "Evidence")
        self.assertEqual(d["court_number_judge"], "Judge 1")


class TestFeature5Tier2(TestCaseDetailsBase):
    """Tier 2: Feature 5 - Deep Case Record Boundary & Edge Cases (>= 5 tests)."""

    def test_f5_empty_html_fragment_returns_empty_model(self):
        """Verify empty HTML fragment returns empty CaseDetail without crashing."""
        detail = parse_case_detail("")
        self.assertEqual(detail.cnr_number, "")
        self.assertEqual(detail.case_type, "")

    def test_f5_whitespace_and_excess_colons_in_labels(self):
        """Verify label mapping tolerates excess colons, tabs, and spaces."""
        html = """
        <table class="case_details_table">
            <tr>
                <th>  CASE TYPE : : </th>
                <td>  Special Civil Suit  </td>
                <td> Filing Date : </td>
                <td> 01-01-2024 </td>
            </tr>
            <tr>
                <td> CNR NUMBER </td>
                <td> MHAA010000012024 </td>
            </tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "MHAA010000012024")
        self.assertEqual(detail.case_type, "Special Civil Suit")
        self.assertEqual(detail.filing_date, "01-01-2024")

    def test_f5_missing_cnr_in_details_table_fallback(self):
        """Verify CNR search across general rows if not in standard details table."""
        html = "<div><table><tr><td>CNR: DLHC010000992024</td></tr></table></div>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "DLHC010000992024")

    def test_f5_efiling_fields_optional(self):
        """Verify filing number and filing date are extracted when present."""
        html = """
        <table class="case_details_table">
            <tr><td>CNR Number</td><td>DLHC010000992024</td></tr>
            <tr><td>Filing Number</td><td>FN/100/2024</td></tr>
            <tr><td>Filing Date</td><td>12-04-2024</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.filing_number, "FN/100/2024")
        self.assertEqual(detail.filing_date, "12-04-2024")

    def test_f5_case_status_disposed_parsing(self):
        """Verify case status 'Disposed' is extracted correctly."""
        html = """
        <table class="case_details_table">
            <tr><td>CNR Number</td><td>DLHC010000992024</td></tr>
            <tr><td>Case Status</td><td>Disposed</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.case_status, "Disposed")


# ── Feature 6: Party Roster & Advocate Extraction ──────────────────────────────

class TestFeature6Tier1(TestCaseDetailsBase):
    """Tier 1: Feature 6 - Party Roster & Advocate Parsing (>= 5 tests)."""

    def test_f6_parse_petitioner_roster_with_advocate(self):
        """Verify petitioner names and advocates are parsed into structured dicts."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertGreaterEqual(len(detail.petitioners), 1)
        self.assertEqual(detail.petitioners[0]["name"], "RAMESH KUMAR")
        self.assertEqual(detail.petitioners[0]["advocate"], "S. N. MURTHY")

    def test_f6_parse_respondent_roster_with_advocate(self):
        """Verify respondent names and advocates are parsed into structured dicts."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(len(detail.respondents), 1)
        self.assertEqual(detail.respondents[0]["name"], "SITA DEVI")
        self.assertEqual(detail.respondents[0]["advocate"], "P. N. HEGDE")

    def test_f6_parse_in_person_advocate(self):
        """Verify 'IN PERSON' advocate designation is preserved."""
        detail = parse_case_detail({"data_list": self.sample_html})
        in_person_pets = [p for p in detail.petitioners if p["advocate"] == "IN PERSON"]
        self.assertEqual(len(in_person_pets), 1)
        self.assertEqual(in_person_pets[0]["name"], "SURESH KUMAR")

    def test_f6_strip_leading_ordinal_from_party_name(self):
        """Verify leading numbers and closing parentheses are stripped from names."""
        html = """
        <ul class="petitioner-advocate-list">
            <li>1) ANAND RAO<br/>Advocate- K. S. REDDY</li>
            <li>2. BHASKAR RAO<br/>Advocate- M. SHETTY</li>
        </ul>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.petitioners[0]["name"], "ANAND RAO")
        self.assertEqual(detail.petitioners[1]["name"], "BHASKAR RAO")

    def test_f6_multiple_parties_in_roster(self):
        """Verify multiple petitioners and respondents are indexed and categorized."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(len(detail.parties), 3)
        self.assertEqual(len(detail.petitioners), 2)
        self.assertEqual(len(detail.respondents), 1)


class TestFeature6Tier2(TestCaseDetailsBase):
    """Tier 2: Feature 6 - Party Roster Boundary & Edge Cases (>= 5 tests)."""

    def test_f6_unassigned_advocate_party_roster(self):
        """Verify party with no advocate line parses with advocate as empty string."""
        html = """
        <ul class="petitioner-advocate-list">
            <li>1) CHANDRA SHEKAR<br/></li>
        </ul>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.petitioners), 1)
        self.assertEqual(detail.petitioners[0]["name"], "CHANDRA SHEKAR")
        self.assertEqual(detail.petitioners[0]["advocate"], "")

    def test_f6_missing_petitioner_ul_returns_empty_list(self):
        """Verify missing petitioner-advocate-list returns empty list."""
        html = "<ul class='respondent-advocate-list'><li>1) Resp<br/>Advocate- A</li></ul>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.petitioners, [])
        self.assertEqual(len(detail.respondents), 1)

    def test_f6_missing_respondent_ul_returns_empty_list(self):
        """Verify missing respondent-advocate-list returns empty list."""
        html = "<ul class='petitioner-advocate-list'><li>1) Pet<br/>Advocate- A</li></ul>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.respondents, [])
        self.assertEqual(len(detail.petitioners), 1)

    def test_f6_advocate_with_colons_dashes_spaces(self):
        """Verify various advocate prefixes ('Advocate : ', 'Advocate - ') are stripped cleanly."""
        html = """
        <ul class="petitioner-advocate-list">
            <li>1) P1<br/>Advocate:   G. K. HEGDE</li>
            <li>2) P2<br/>Advocate - - S. KUMAR</li>
        </ul>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.petitioners[0]["advocate"], "G. K. HEGDE")
        self.assertEqual(detail.petitioners[1]["advocate"], "S. KUMAR")

    def test_f6_empty_li_tags_skipped(self):
        """Verify empty <li> tags do not produce empty or dummy party records."""
        html = """
        <ul class="petitioner-advocate-list">
            <li></li>
            <li>   </li>
            <li>1) REAL PERSON<br/>Advocate- ADV</li>
        </ul>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.petitioners), 1)
        self.assertEqual(detail.petitioners[0]["name"], "REAL PERSON")


# ── Feature 7: Statutory Acts & Sections Extraction ────────────────────────────

class TestFeature7Tier1(TestCaseDetailsBase):
    """Tier 1: Feature 7 - Acts & Sections Extraction (>= 5 tests)."""

    def test_f7_parse_acts_and_sections_standard(self):
        """Verify parse_case_detail extracts statutory acts and section records."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(len(detail.acts), 2)
        self.assertEqual(detail.acts[0]["act"], "HINDU MARRIAGE ACT")
        self.assertEqual(detail.acts[0]["sections"], "13(1)(ia)")

    def test_f7_multiple_acts_table_rows(self):
        """Verify multiple rows in act_table are extracted."""
        detail = parse_case_detail({"data_list": self.sample_html})
        acts = [a["act"] for a in detail.acts]
        self.assertIn("HINDU MARRIAGE ACT", acts)
        self.assertIn("CODE OF CIVIL PROCEDURE", acts)

    def test_f7_act_table_skips_header_row(self):
        """Verify act table header row (Act, Section) is not included in records."""
        detail = parse_case_detail({"data_list": self.sample_html})
        for a in detail.acts:
            self.assertNotEqual(a["act"].lower(), "act")
            self.assertNotEqual(a["sections"].lower(), "section")

    def test_f7_act_name_and_section_preserved(self):
        """Verify exact text of acts and sections is preserved without alterations."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(detail.acts[1]["sections"], "ORDER IX RULE 13")

    def test_f7_act_table_lookup_by_id_and_class(self):
        """Verify act_table matches by id='act_table' or class='act_table'."""
        html = "<table class='act_table'><tr><th>Act</th><th>Section</th></tr><tr><td>IPC</td><td>302</td></tr></table>"
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.acts), 1)
        self.assertEqual(detail.acts[0]["act"], "IPC")


class TestFeature7Tier2(TestCaseDetailsBase):
    """Tier 2: Feature 7 - Acts & Sections Boundary Cases (>= 5 tests)."""

    def test_f7_missing_act_table_returns_empty_list(self):
        """Verify detail view without act_table defaults to empty list."""
        detail = parse_case_detail("<div><p>No acts</p></div>")
        self.assertEqual(detail.acts, [])

    def test_f7_act_row_missing_section_cell(self):
        """Verify rows with only 1 cell are skipped gracefully."""
        html = "<table id='act_table'><tr><td>Incomplete Row</td></tr></table>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.acts, [])

    def test_f7_act_table_with_empty_rows(self):
        """Verify table with empty rows does not crash parser."""
        html = "<table id='act_table'><tr><th>Act</th><th>Sec</th></tr><tr><td></td><td></td></tr></table>"
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.acts), 0)

    def test_f7_act_table_under_act_header_skipped(self):
        """Verify rows where act name starts with 'under act' header are skipped."""
        html = """
        <table id='act_table'>
            <tr><td>Under Act</td><td>Section</td></tr>
            <tr><td>MOTOR VEHICLES ACT</td><td>166</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.acts), 1)
        self.assertEqual(detail.acts[0]["act"], "MOTOR VEHICLES ACT")

    def test_f7_act_sections_with_complex_subsections(self):
        """Verify complex section strings with multiple subsections are preserved."""
        html = """
        <table id='act_table'>
            <tr><th>Act</th><th>Section</th></tr>
            <tr><td>NI ACT</td><td>138, 141, 142(1)(b)</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.acts[0]["sections"], "138, 141, 142(1)(b)")


# ── Feature 8: Chronological Hearing History Extraction ────────────────────────

class TestFeature8Tier1(TestCaseDetailsBase):
    """Tier 1: Feature 8 - Hearing History Extraction (>= 5 tests)."""

    def test_f8_parse_hearing_history_standard_columns(self):
        """Verify judge, business date, hearing date, and purpose are extracted."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(len(detail.hearings), 2)
        h = detail.hearings[0]
        self.assertEqual(h["judge"], "PRL. JUDGE FAMILY COURT")
        self.assertEqual(h["business_date"], "16-01-2025")
        self.assertEqual(h["hearing_date"], "10-02-2025")
        self.assertEqual(h["purpose"], "Notice Returnable")

    def test_f8_hearing_chronological_ordering(self):
        """Verify hearings are returned in table order."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(detail.hearings[0]["business_date"], "16-01-2025")
        self.assertEqual(detail.hearings[1]["business_date"], "10-02-2025")

    def test_f8_hearing_purpose_and_judge_extracted(self):
        """Verify hearing purpose and judge names are cleanly parsed."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(detail.hearings[1]["purpose"], "Hearing")
        self.assertEqual(detail.hearings[1]["judge"], "PRL. JUDGE FAMILY COURT")

    def test_f8_hearing_date_and_business_date(self):
        """Verify date and next_date alias properties are populated."""
        detail = parse_case_detail({"data_list": self.sample_html})
        h = detail.hearings[0]
        self.assertEqual(h["date"], h["business_date"])
        self.assertEqual(h["next_date"], h["hearing_date"])

    def test_f8_history_table_lookup_by_id_or_class(self):
        """Verify table.history_table and table#history_table are both detected."""
        html = """
        <table id="history_table">
            <tr><td>Judge A</td><td>01-01-2025</td><td>02-02-2025</td><td>Notice</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.hearings), 1)
        self.assertEqual(detail.hearings[0]["judge"], "Judge A")


class TestFeature8Tier2(TestCaseDetailsBase):
    """Tier 2: Feature 8 - Hearing History Boundary Cases (>= 5 tests)."""

    def test_f8_missing_history_table_returns_empty_list(self):
        """Verify missing history table defaults to empty list."""
        detail = parse_case_detail("<div><p>No hearing history</p></div>")
        self.assertEqual(detail.hearings, [])

    def test_f8_embedded_headers_skipped(self):
        """Verify embedded header text in <td> rows is ignored."""
        html = """
        <table class="history_table">
            <tr><td>Judge</td><td>Business Date</td><td>Hearing Date</td><td>Purpose</td></tr>
            <tr><td>JUDGE 1</td><td>10-05-2025</td><td>20-06-2025</td><td>Arguments</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.hearings), 1)
        self.assertEqual(detail.hearings[0]["judge"], "JUDGE 1")

    def test_f8_row_with_fewer_than_4_cells_skipped(self):
        """Verify rows missing columns are skipped safely."""
        html = """
        <table class="history_table">
            <tr><td>J1</td><td>10-05-2025</td><td>20-06-2025</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.hearings, [])

    def test_f8_empty_history_table(self):
        """Verify empty history table returns empty list without error."""
        html = "<table class='history_table'></table>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.hearings, [])

    def test_f8_hearings_with_whitespace_and_special_chars(self):
        """Verify hearing rows with excess whitespace in purpose or judge parse cleanly."""
        html = """
        <table class="history_table">
            <tr>
                <td>  PRL. SESSIONS JUDGE (CCH-1)  </td>
                <td>  15-08-2025  </td>
                <td>  25-09-2025  </td>
                <td>  For Framing of Charges  </td>
            </tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.hearings[0]["judge"], "PRL. SESSIONS JUDGE (CCH-1)")
        self.assertEqual(detail.hearings[0]["business_date"], "15-08-2025")
        self.assertEqual(detail.hearings[0]["purpose"], "For Framing of Charges")


# ── Feature 9: Edge Cases & Malformed HTML Handling ─────────────────────────────

class TestFeature9Tier1(TestCaseDetailsBase):
    """Tier 1: Feature 9 - Edge Cases & Malformed HTML Handling (>= 5 tests)."""

    def test_f9_sample_fixture_ia_details_extracted(self):
        """Verify interim application details extracted from sample fixture."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertEqual(len(detail.ia_details), 1)
        self.assertEqual(detail.ia_details[0]["ia_number"], "1/2025")
        self.assertEqual(detail.ia_details[0]["party"], "RAMESH KUMAR")
        self.assertEqual(detail.ia_details[0]["status"], "Pending")

    def test_f9_malformed_html_missing_closing_tags(self):
        """Verify unclosed HTML table and tr tags do not cause BeautifulSoup parse crash."""
        unclosed_html = "<table class='case_details_table'><tr><td>CNR Number</td><td>KABC0E0001992025</td><tr><td>Case Type</td><td>Civil</td>"
        detail = parse_case_detail(unclosed_html)
        self.assertEqual(detail.cnr_number, "KABC0E0001992025")
        self.assertEqual(detail.case_type, "Civil")

    def test_f9_nested_div_containers(self):
        """Verify tables deeply nested within arbitrary divs parse accurately."""
        html = "<div><div><div><table class='case_details_table'><tr><td>CNR Number</td><td>KABC0E0001992025</td></tr></table></div></div></div>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "KABC0E0001992025")

    def test_f9_json_wrapper_with_data_list(self):
        """Verify parse_case_detail correctly unwraps JSON envelope with 'data_list'."""
        data_json = json.dumps({"data_list": "<table class='case_details_table'><tr><td>CNR Number</td><td>DLHC010000012025</td></tr></table>"})
        detail = parse_case_detail(data_json)
        self.assertEqual(detail.cnr_number, "DLHC010000012025")

    def test_f9_duplicate_class_attributes_on_tables(self):
        """Verify tables with multiple classes parse correctly."""
        html = "<table class='case_details_table table-striped table-bordered'><tr><td>CNR Number</td><td>DLHC010000012025</td></tr></table>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "DLHC010000012025")


class TestFeature9Tier2(TestCaseDetailsBase):
    """Tier 2: Feature 9 - Edge Cases & Malformed HTML Boundary Cases (>= 5 tests)."""

    def test_f9_missing_ia_table_graceful_fallback(self):
        """Verify missing IA table returns empty ia_details list."""
        detail = parse_case_detail("<div><p>No IA</p></div>")
        self.assertEqual(detail.ia_details, [])

    def test_f9_ia_table_skips_header_text(self):
        """Verify IA table header row starting with 'IA' is skipped."""
        html = """
        <table class="ia_table">
            <tr><td>IA Number</td><td>Applicant</td><td>Status</td></tr>
            <tr><td>2/2025</td><td>Sita</td><td>Disposed</td></tr>
        </table>
        """
        detail = parse_case_detail(html)
        self.assertEqual(len(detail.ia_details), 1)
        self.assertEqual(detail.ia_details[0]["ia_number"], "2/2025")

    def test_f9_corrupted_response_format_non_json_string(self):
        """Verify non-JSON, non-HTML plain string handled without crash."""
        detail = parse_case_detail("502 Bad Gateway: nginx")
        self.assertEqual(detail.cnr_number, "")

    def test_f9_plain_html_with_no_sections(self):
        """Verify plain HTML with no recognized tables returns clean empty model."""
        html = "<html><body><h1>Welcome to eCourts</h1></body></html>"
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "")
        self.assertEqual(detail.parties, [])
        self.assertEqual(detail.acts, [])
        self.assertEqual(detail.hearings, [])

    def test_f9_none_input_handled_safely(self):
        """Verify None input does not crash parse_case_detail."""
        detail = parse_case_detail(None)  # type: ignore
        self.assertEqual(detail.cnr_number, "")


if __name__ == "__main__":
    unittest.main()
