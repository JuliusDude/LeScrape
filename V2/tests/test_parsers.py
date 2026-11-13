import json
import unittest

from V2 import parsers as P
from V2.tests.fakes import fx, fxj, live_cnrs, live_html

# cnr: (hearings, processes, main_matters, orders, petitioners, respondents, sub_stage)
GROUND_TRUTH = {
    "KABC010342242022": (32, 1, 1, 1, 1, 1, None),
    "KABC010342472022": (21, 3, 0, 1, 1, 3, None),
    "KABC010342612022": (3, 3, 0, 1, 1, 3, None),
    "KABC010342952022": (14, 1, 1, 1, 1, 1, "ISSUE DELIVERY WARRANT"),
    "KABC010342962022": (21, 3, 0, 2, 1, 1, None),
    "KABC010343082022": (16, 8, 1, 1, 1, 2, "ISSUE MOVABLE WARRANT"),
    "KABC010343092022": (10, 2, 1, 1, 1, 2, "STEPS"),
    "KABC010343102022": (19, 6, 1, 1, 1, 2, "ISSUE MOVABLE WARRANT"),
    "KABC010343112022": (12, 6, 1, 1, 1, 2, "STEPS"),
    "KABC010343122022": (19, 6, 1, 1, 1, 2, "ISSUE MOVABLE WARRANT"),
}
RESPONDENTS = {
    "KABC010342472022": ["SHIVARAJU N", "NETHRAVATHI", "SHOBA M"],
    "KABC010342612022": ["SWATI ENGINEERS", "CHETHANA C K", "CHANDRAPRAKASH KARKARA"],
    "KABC010343082022": ["BALAJI M D", "ASHA"],
    "KABC010343092022": ["SHIVANNA C", "BABYCHUDI"],
    "KABC010343102022": ["RAJESHWAR RAO", "RADHIKA"],
    "KABC010343112022": ["HEMANTH KUMAR H J", "CHOODAMANI K"],
    "KABC010343122022": ["RAJASHEKAR B R", "MANJULA"],
}


def live(cnr):
    return P.parse_case_page(live_html(cnr))


class TestLiveGroundTruth(unittest.TestCase):
    def test_all_ten(self):
        self.assertEqual(len(GROUND_TRUTH), 10)       # explicit list: IA fixtures live in the same dir
        self.assertTrue(set(GROUND_TRUTH) <= set(live_cnrs()))
        total = 0
        for cnr, (hear, proc, mm, orders, pets, resps, sub) in GROUND_TRUTH.items():
            with self.subTest(cnr=cnr):
                c = live(cnr)
                self.assertEqual(c["cnr"], cnr)
                self.assertEqual(len(c["hearings"]), hear)
                self.assertEqual(len(c["processes"]), proc)
                self.assertEqual(len(c["main_matters"]), mm)
                self.assertEqual(len(c["orders"]), orders)
                self.assertEqual(len(c["petitioners"]), pets)
                self.assertEqual(len(c["respondents"]), resps)
                self.assertEqual(c["case_sub_stage"], sub)
                self.assertEqual(c["case_status"], "Case disposed")
                self.assertEqual(c["national_court_code"], "KABC01")
                self.assertEqual([h["seq"] for h in c["hearings"]], list(range(hear, 0, -1)))
                self.assertEqual(sum(1 for h in c["hearings"] if h["vb_args"]), hear)
                self.assertEqual(c["warnings"], [])
                total += len(c["hearings"])
        self.assertEqual(total, 167)

    def test_respondent_names_and_seq(self):
        for cnr, names in RESPONDENTS.items():
            with self.subTest(cnr=cnr):
                c = live(cnr)
                self.assertEqual([r["name"] for r in c["respondents"]], names)
                self.assertEqual([r["seq"] for r in c["respondents"]], list(range(1, len(names) + 1)))

    def test_advocate_attaches_to_petitioner(self):
        c = live("KABC010342472022")
        self.assertEqual(c["petitioners"][0]["advocate"], "VENKATARAMANA K S")
        self.assertTrue(all(r["advocate"] == "" for r in c["respondents"]))

    def test_sub_stage_row_parts(self):
        c = live("KABC010342952022")
        self.assertEqual(c["case_status"], "Case disposed")
        self.assertEqual(c["nature_of_disposal"], "Uncontested--DISPOSED OTHERWISE")
        self.assertEqual(c["decision_date_iso"], "2023-11-28")

    def test_main_matters_and_filing_verbatim(self):
        c = live("KABC010342952022")
        m = c["main_matters"][0]
        self.assertEqual((m["case_no"], m["cnr"], m["filing_no"]), ("O.S./0000797/2017", "KABC010027452017", "8360"))

    def test_two_order_tables(self):
        c = live("KABC010342962022")
        self.assertEqual([(o["number"], o["order_date"]) for o in c["orders"]],
                         [("1", "30-09-2024"), ("2", "10-10-2025")])
        self.assertEqual([o["section"] for o in c["orders"]], ["Interim Orders", "Final Orders / Judgements"])
        self.assertEqual(c["orders"][0]["description"], "Interim Orders | Order on Exhibit on 30-09-2024")
        for o in c["orders"]:
            self.assertEqual(len(o["pdf_args"]), 5)
            self.assertEqual(o["pdf_args"][0], "cl+sGJoSJ58oyVespa8VHg==")
            self.assertEqual(o["pdf_args"][4], "")


class TestEx16(unittest.TestCase):
    def setUp(self):
        self.c = P.parse_case_page(fxj("viewHistory.txt")["data_list"])

    def test_counts_and_vb_args(self):
        c = self.c
        self.assertEqual(c["cnr"], "KABC010342962022")
        self.assertEqual(c["case_number"], "EX/16/2023")
        self.assertEqual(len(c["orders"]), 2)
        self.assertEqual(len(c["hearings"]), 21)
        newest, oldest = c["hearings"][0], c["hearings"][-1]
        self.assertEqual(newest["vb_args"], ["3", "20", "", "KABC010342962022", "3", "Disposed", "10-10-2025", "18",
                                             "KABC01", "CScaseType", "0"])
        self.assertEqual(oldest["vb_args"], ["3", "20", "20230306", "KABC010342962022", "3", "DisposedP",
                                             "02-01-2023", "18", "KABC01", "CScaseType", "1"])
        self.assertEqual((newest["seq"], oldest["seq"]), (21, 1))
        self.assertEqual(oldest["business_on_date_iso"], "2023-01-02")
        self.assertTrue(all(len(h["vb_args"]) == 11 for h in c["hearings"]))

    def test_details(self):
        c = self.c
        self.assertEqual(c["filing_number"], "2797/2022")
        self.assertEqual(c["filing_date_iso"], "2022-12-17")
        self.assertEqual(c["case_type"], "EX")
        self.assertEqual(c["first_hearing_date_iso"], "2023-01-02")
        self.assertEqual(c["acts"][0]["act"], "U/O 21 RULE 11 OF CPC")
        self.assertEqual(c["acts"][0]["section"], "")
        self.assertEqual(len(c["processes"]), 3)
        self.assertTrue(c["ack_url"].startswith("home/case_acknowlegement&cino=KABC010342962022"))
        self.assertEqual(c["court_name"], "PRL. CITY CIVIL AND SESSIONS JUDGE")


class TestBusinessAckSearch(unittest.TestCase):
    def test_business(self):
        b = P.parse_business(fxj("viewBusiness.txt")["data_list"])
        self.assertEqual(b["business_text"], "Case called out. Parties absent. No representation. To hear by 15-11-2025.")
        self.assertEqual(b["next_purpose"], "HEARING")
        self.assertEqual(b["next_hearing_date"], "10-10-2025")
        self.assertEqual(b["next_hearing_date_iso"], "2025-10-10")
        self.assertEqual(b["judge"], "CCH16 XVII ADDL. CITY CIVIL AND SESSIONS JUDGE")
        self.assertEqual(b["header"]["date"], "20-08-2025")
        self.assertEqual(b["header"]["cnr"], "KABC010342962022")

    def test_ack(self):
        a = P.parse_ack(fxj("ack.txt")["ack_details"])
        self.assertEqual(a["decree_holder"], "H L L LIFECARE LIMITED")
        self.assertEqual(a["judgement_debtor"], "M/S SUPER THREADS INDIA")
        self.assertEqual(a["filing_number"], "EX/2797/2022")
        self.assertEqual(a["fields"]["Petitioner Advocate"], "M R RAVI RAJ MALALI")
        self.assertEqual(a["court_name"], "PRL CITY CIVIL AND SESSIONS JUDGE")

    def test_cnr_history_same_layout(self):
        c = P.parse_case_page(fxj("viewCNRHistory.txt")["casetype_list"])
        self.assertEqual(c["cnr"], "KABC010342962022")
        self.assertEqual(len(c["hearings"]), 21)
        self.assertEqual(len(c["orders"]), 0 if not c["orders"] else len(c["orders"]))

    def test_search_results_real(self):
        r = P.parse_search_results(fxj("submit_case_type_ok.json")["case_data"])
        self.assertEqual(r["total"], 2344)
        self.assertEqual(len(r["rows"]), 2344)
        first = r["rows"][0]
        self.assertEqual(first["case_number"], "EX/2/2023")
        self.assertEqual((first["petitioner"], first["respondent"]), ("KRISHNAMURTHY G", "SATHISH M"))
        self.assertEqual(first["court_name"], "PRL. CITY CIVIL AND SESSIONS JUDGE")
        self.assertEqual(first["view_args"], ["202300000022023", "KABC010342242022", "3", "", "CScaseNumber", "3",
                                              "20", "1030135", "CScaseType"])
        self.assertEqual(first["view_params"]["search_flag"], "CScaseNumber")
        self.assertEqual(first["view_params"]["search_by"], "CScaseType")
        self.assertTrue(all(len(x["cnr"]) == 16 and x["view_params"] for x in r["rows"]))
        self.assertEqual(len({x["cnr"] for x in r["rows"]}), 2344)

    def test_rejected_has_fresh_captcha(self):
        j = fxj("submit_case_type_rejected.json")
        self.assertIn("Invalid Captcha", j["errormsg"])
        src = P.parse_captcha_src(j["div_captcha"])
        self.assertIn("securimage_show.php?", src)

    def test_index_and_components(self):
        tok, comp = P.parse_index(fx("casestatus_index.html"))
        self.assertEqual(len(tok), 64)
        self.assertEqual(comp, "/ecourtindia_v6/js/components.js?v=1793871209")
        self.assertEqual(P.parse_components(fx("components.js")), ("fd794rfh", "Ajfeh753tr"))
        self.assertEqual(P.parse_components("nothing"), (None, None))


class TestHelpers(unittest.TestCase):
    def test_to_iso(self):
        self.assertEqual(P.to_iso("10-10-2025"), "2025-10-10")
        self.assertEqual(P.to_iso(" 17-12-2022  &nbsp;".replace("&nbsp;", "")), "2022-12-17")
        self.assertEqual(P.to_iso("02nd January 2023"), "2023-01-02")
        self.assertEqual(P.to_iso("15th January 2026"), "2026-01-15")
        self.assertEqual(P.to_iso("2025-10-10"), "2025-10-10")
        self.assertIsNone(P.to_iso("-"))
        self.assertIsNone(P.to_iso("31-02-2025"))
        self.assertIsNone(P.to_iso(""))

    def test_js_args(self):
        self.assertEqual(P.split_js_args("'a','b,c',,3,\"d\""), ["a", "b,c", "", "3", "d"])
        self.assertEqual(P.first_call("x onclick=foo('1','2') y", "foo"), ["1", "2"])
        self.assertIsNone(P.first_call("nothing", "foo"))


def case_html(**kw):
    return (
        "<h2 id='chHeading'>COURT</h2><h3>Case Details</h3><table><tr><th>Case Type</th><td>XX - Test</td></tr>"
        "<tr><th>CNR Number</th><td><span>ABCD012345678901</span></td></tr></table>"
        "<h3>Case Status</h3><table>" + kw.get("status", "") + "</table>" + kw.get("body", ""))


class TestSyntheticVariants(unittest.TestCase):
    def test_advocate_belongs_to_preceding_entry(self):
        html = ("<h3>Petitioner and Advocate</h3><ul><li>1) P ONE<br/>Advocate- ADV A<br/>"
                "2) P TWO<br/>Advocate- ADV B<br/>3) P THREE<br/></li></ul>"
                "<h3>Respondent and Advocate</h3><ul><li>1) R ONE<br/>2)  R TWO&nbsp;&nbsp;<br/>"
                "Advocate- ADV C<br/>Advocate- ADV D<br/></li><li>3) R THREE</li></ul>")
        c = P.parse_case_page(html)
        self.assertEqual([(p["name"], p["advocate"], p["seq"]) for p in c["petitioners"]],
                         [("P ONE", "ADV A", 1), ("P TWO", "ADV B", 2), ("P THREE", "", 3)])
        self.assertEqual([(p["name"], p["advocates"], p["seq"]) for p in c["respondents"]],
                         [("R ONE", [], 1), ("R TWO", ["ADV C", "ADV D"], 2), ("R THREE", [], 3)])

    def test_unnumbered_extra_line_is_not_a_party_but_kept(self):
        html = ("<h3>Petitioner and Advocate</h3><ul><li>1) P ONE<br/>NO 12, MG ROAD<br/>Advocate- ADV A<br/></li></ul>"
                "<h3>Respondent and Advocate</h3><ul><li>1) R ONE<br/>S/O SOMEONE<br/>2) R TWO</li>"
                "<li>R THREE UNNUMBERED</li></ul>")
        c = P.parse_case_page(html)
        self.assertEqual([(p["name"], p["advocate"]) for p in c["petitioners"]], [("P ONE", "ADV A")])
        self.assertEqual(c["petitioners"][0]["extra_lines"], ["NO 12, MG ROAD"])
        self.assertEqual([(p["name"], p["seq"]) for p in c["respondents"]],
                         [("R ONE", 1), ("R TWO", 2), ("R THREE UNNUMBERED", 3)])
        self.assertEqual(c["respondents"][0]["extra_lines"], ["S/O SOMEONE"])

    def test_pending_case_labels(self):
        status = ("<tr><th>First Hearing Date</th><td>02nd January 2023</td></tr>"
                  "<tr><th>Next Hearing Date</th><td><strong>15th January 2026</strong></td></tr>"
                  "<tr><th>Case Stage</th><td><strong>Evidence</strong></td></tr>"
                  "<tr><th>Court Number and Judge</th><td>5-JUDGE X</td></tr>"
                  "<tr><th>Weird Label</th><td>weird</td></tr>")
        c = P.parse_case_page(case_html(status=status))
        self.assertEqual(c["case_stage"], "Evidence")
        self.assertEqual(c["next_hearing_date_iso"], "2026-01-15")
        self.assertEqual(c["case_status"], "")
        self.assertEqual(c["status_extra"], {"Weird Label": "weird"})
        self.assertEqual(c["cnr"], "ABCD012345678901")

    def test_sub_stage_variants(self):
        row = "<tr><td>Case Status</td><td><strong>Case disposed</strong><strong>Sub Stage </strong></td><td><strong>FINAL DECREE</strong></td></tr>"
        c = P.parse_case_page(case_html(status=row))
        self.assertEqual((c["case_status"], c["case_sub_stage"]), ("Case disposed", "FINAL DECREE"))
        row = "<tr><td>Case Status</td><td><strong>Case disposed</strong></td></tr>"
        c = P.parse_case_page(case_html(status=row))
        self.assertEqual((c["case_status"], c["case_sub_stage"]), ("Case disposed", None))

    def test_unknown_section_not_dropped(self):
        body = "<h3>Other Details</h3><table><tr><th>IA No</th><th>Party</th></tr><tr><td>1/2023</td><td>X</td></tr></table>"
        c = P.parse_case_page(case_html(body=body))
        self.assertEqual(len(c["extra_sections"]), 1)
        es = c["extra_sections"][0]
        self.assertEqual(es["heading"], "Other Details")
        self.assertEqual(es["tables"][0], [["IA No", "Party"], ["1/2023", "X"]])

    def test_multiple_main_matters(self):
        body = ("<h3>Main Matters</h3><table><tr><td>Main Case No. : </td><td>OS/1/2000 (<a onClick=\"funView_CinoHistory('AAAA000000000001')\">AAAA000000000001</a>)</td></tr>"
                "<tr><td>Main Filing No. : </td><td>11</td></tr>"
                "<tr><td>Main Case No. : </td><td>OS/2/2001 (<a onClick=\"funView_CinoHistory('AAAA000000000002')\">x</a>)</td></tr>"
                "<tr><td>Main Filing No. : </td><td>22</td></tr></table>")
        c = P.parse_case_page(case_html(body=body))
        self.assertEqual([(m["cnr"], m["filing_no"]) for m in c["main_matters"]],
                         [("AAAA000000000001", "11"), ("AAAA000000000002", "22")])

    def test_empty_page_warns_not_crashes(self):
        c = P.parse_case_page("")
        self.assertIn("cnr not found", c["warnings"])


IA_ARGS = ["300000012023", "{cnr}", "IA", "1", "2023", "KABC01", "CScaseType"]


class TestInterimApplications(unittest.TestCase):
    def test_0902023(self):
        c = live("KABC010000902023")
        self.assertEqual(len(c["interim_applications"]), 1)
        ia = c["interim_applications"][0]
        self.assertEqual(ia["ia_number"], "IA/1/2023")
        self.assertEqual(ia["parties"], ["ILYAS SAIT H", "SAMAR SHAIKH ALIAS SHAIKH JALALUDDIN"])
        self.assertEqual((ia["filing_date"], ia["filing_date_iso"]), ("02-01-2023", "2023-01-02"))
        self.assertEqual((ia["next_date"], ia["next_purpose"], ia["ia_status"]), ("", "", "Pending"))
        self.assertEqual(ia["args"], [x.format(cnr="KABC010000902023") for x in IA_ARGS])
        self.assertEqual(ia["params"], dict(zip(P.IA_ARG_NAMES, ia["args"])))
        self.assertEqual(ia["params"]["ia_case_type_name"], "IA")
        self.assertEqual(ia["params"]["search_by"], "CScaseType")
        self.assertEqual(c["extra_sections"], [])           # not duplicated into the catch-all

    def test_0532023(self):
        c = live("KABC010000532023")
        self.assertEqual(len(c["interim_applications"]), 1)
        ia = c["interim_applications"][0]
        self.assertEqual((ia["ia_number"], ia["parties"], ia["filing_date"], ia["ia_status"]),
                         ("IA/1/2023", ["JYOTHI RAO", "RAMESH M P"], "02-01-2023", "Pending"))
        self.assertEqual((ia["next_date"], ia["next_purpose"]), ("", ""))
        self.assertEqual(ia["args"], [x.format(cnr="KABC010000532023") for x in IA_ARGS])
        self.assertEqual(c["extra_sections"], [])

    def test_next_date_and_purpose(self):
        html = ("<h3>IA Status</h3><table class='IAheading'><tr><th>IA Number</th></tr><tr><td><a onclick=\"viewIABusiness("
                "'1','C','IA','2','2024','K','CScaseType')\">IA/2/2024</a></td><td>A<br/>B<br/></td><td>01-02-2024 </td>"
                "<td>05-03-2024 <br>(HEARING)</td><td>Disposed</td></tr></table>")
        ia = P.parse_case_page(html)["interim_applications"][0]
        self.assertEqual((ia["next_date"], ia["next_date_iso"], ia["next_purpose"]),
                         ("05-03-2024", "2024-03-05", "HEARING"))

    def test_no_ia_section(self):
        self.assertEqual(live("KABC010342242022")["interim_applications"], [])

    def test_parse_ia_business_zero_rows_real(self):
        r = P.parse_ia_business(fxj("viewIABusiness.txt")["data_list"])
        self.assertEqual(r, {"case_no": "EX/24/2023", "rows": []})

    def test_parse_ia_business_rows_synthetic(self):
        from V2.tests.fakes import IA_BUSINESS_SYNTHETIC
        r = P.parse_ia_business(IA_BUSINESS_SYNTHETIC)
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["rows"][0]["business"], "ISSUE NOTICE; TO RESPONDENT")
        self.assertEqual(r["rows"][0]["business_on_date_iso"], "2023-01-02")
        self.assertEqual(r["rows"][1]["hearing_date"], "")


class TestCatchAllBr(unittest.TestCase):
    def test_br_boundaries_kept(self):
        html = "<h3>Weird</h3><table><tr><td>A<br/>B</td><td>C</td></tr></table>"
        sec = P.parse_case_page(html)["extra_sections"][0]
        self.assertEqual(sec["tables"], [[["A; B", "C"]]])


if __name__ == "__main__":
    unittest.main()


class TestEntityResidue(unittest.TestCase):
    def test_double_escaped_entities_are_decoded(self):
        # live viewBusiness text contained "Hon&amp;#039;ble" -> literal "Hon&#039;ble" after one decode
        from V2.parsers import clean
        self.assertEqual(clean("Hon&#039;ble  Court&rsquo;s\xa0order"), "Hon'ble Court’s order")
        self.assertEqual(clean("A & B"), "A & B")
