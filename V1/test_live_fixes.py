"""
V1.test_live_fixes
===========================
Tests for the 7 issues found by the 2026-11-12 live run (FIX-LIVE-7).

Ground truth: the real viewHistory pages in ``fixtures/live_20261112/`` and the
per-case numbers of the independent verification report (hard-coded below).
No network: ``subprocess.run`` is patched where the transport is exercised.
"""

from __future__ import annotations

import csv
from contextlib import closing
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from V1 import db as dbm
from V1.db import DeeperDatabase
from V1.exporter import export_to_csv_flat, export_to_json
from V1.models import DeeperCaseDetail, MainMatterItem
from V1.parser import VB_ARG_FIELDS, parse_deeper_case_detail
from V1.test_phase1 import Base, Resp

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "fixtures", "live_20261112")
SYN = os.path.join(HERE, "fixtures", "synthetic")
TARGET = os.path.join(HERE, "fixtures", "target_case_view.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def live(cnr):
    return parse_deeper_case_detail(_read(os.path.join(LIVE, cnr + ".html")))


def syn(name):
    return parse_deeper_case_detail(_read(os.path.join(SYN, name)))


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


class TestGroundTruthAllLiveFixtures(unittest.TestCase):
    def test_counts_for_all_ten_cases(self):
        total_hearings = 0
        for cnr, (hear, proc, mm, orders, pets, resps, sub) in GROUND_TRUTH.items():
            with self.subTest(cnr=cnr):
                c = live(cnr)
                self.assertEqual(c.cnr_number, cnr)
                self.assertEqual(len(c.hearings), hear)
                self.assertEqual(len(c.processes), proc)
                self.assertEqual(len(c.main_matters), mm)
                self.assertEqual(len(c.orders), orders)
                self.assertEqual(len(c.petitioners), pets)
                self.assertEqual(len(c.respondents), resps)
                self.assertEqual(c.case_sub_stage, sub)
                self.assertEqual(c.case_status, "Case disposed")
                self.assertEqual(c.national_court_code, "KABC01")
                self.assertEqual([h.hearing_seq for h in c.hearings], list(range(hear, 0, -1)))
                self.assertEqual(sum(1 for h in c.hearings if h.vb_args_json), hear)
                total_hearings += len(c.hearings)
        self.assertEqual(total_hearings, 167)


class TestIssue1MultiParty(unittest.TestCase):
    def test_respondents_in_order_with_seq(self):
        for cnr, names in RESPONDENTS.items():
            with self.subTest(cnr=cnr):
                c = live(cnr)
                self.assertEqual([r.name for r in c.respondents], names)
                self.assertEqual([r.party_seq for r in c.respondents], list(range(1, len(names) + 1)))

    def test_472_three_respondents_and_petitioner_advocate_kept(self):
        c = live("KABC010342472022")
        self.assertEqual([r.name for r in c.respondents], ["SHIVARAJU N", "NETHRAVATHI", "SHOBA M"])
        self.assertEqual(len(c.petitioners), 1)
        self.assertEqual(c.petitioners[0].advocate, "VENKATARAMANA K S")
        self.assertEqual(c.petitioners[0].party_seq, 1)
        self.assertTrue(all(r.advocate == "" for r in c.respondents))

    def test_advocate_belongs_to_preceding_entry(self):
        html = (
            "<ul class='petitioner-advocate-list'><li>1) P ONE<br/>Advocate- ADV A<br/>"
            "2) P TWO<br/>Advocate- ADV B<br/>3) P THREE<br/></li></ul>"
            "<ul class='respondent-advocate-list'><li>1) R ONE<br/>2)  R TWO&nbsp;&nbsp;<br/>"
            "Advocate- ADV C<br/></li><li>3) R THREE</li></ul>"
        )
        c = parse_deeper_case_detail(html)
        self.assertEqual([(p.name, p.advocate, p.party_seq) for p in c.petitioners],
                         [("P ONE", "ADV A", 1), ("P TWO", "ADV B", 2), ("P THREE", "", 3)])
        self.assertEqual([(p.name, p.advocate, p.party_seq) for p in c.respondents],
                         [("R ONE", "", 1), ("R TWO", "ADV C", 2), ("R THREE", "", 3)])

    def test_unnumbered_extra_line_is_not_a_party(self):
        # Opus review: an unnumbered non-advocate line (e.g. an address) after a name
        # must be ignored, as the original parser did, not turned into a phantom party.
        html = (
            "<ul class='petitioner-advocate-list'><li>1) P ONE<br/>NO 12, MG ROAD, BENGALURU<br/>"
            "Advocate- ADV A<br/></li></ul>"
            "<ul class='respondent-advocate-list'><li>1) R ONE<br/>S/O SOMEONE<br/>2) R TWO</li>"
            "<li>R THREE UNNUMBERED</li></ul>"
        )
        c = parse_deeper_case_detail(html)
        self.assertEqual([(p.name, p.advocate) for p in c.petitioners], [("P ONE", "ADV A")])
        self.assertEqual([(p.name, p.party_seq) for p in c.respondents],
                         [("R ONE", 1), ("R TWO", 2), ("R THREE UNNUMBERED", 3)])

    def test_regression_single_party_per_li_unchanged(self):
        c = parse_deeper_case_detail(_read(TARGET))
        self.assertEqual(len(c.petitioners), 1)
        self.assertEqual(len(c.respondents), 1)
        self.assertEqual(c.petitioners[0].party_seq, 1)

    def test_party_seq_round_trips_through_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = DeeperDatabase(os.path.join(td, "t.db"))
            c = live("KABC010342472022")
            db.save_case(c)
            got = db.get_case(c.cnr_number)
            self.assertEqual([(p.type, p.name, p.party_seq) for p in got.parties],
                             [(p.type, p.name, p.party_seq) for p in c.parties])


class TestIssue2SubStage(unittest.TestCase):
    def test_live_sub_stage(self):
        c = live("KABC010342952022")
        self.assertEqual(c.case_status, "Case disposed")
        self.assertEqual(c.case_status_raw, "Case disposedSub Stage")
        self.assertEqual(c.case_sub_stage, "ISSUE DELIVERY WARRANT")
        self.assertFalse(c.status_assumed)
        self.assertEqual(c.nature_of_disposal, "Uncontested--DISPOSED OTHERWISE")

    def test_other_live_status_cells_split_the_same_way(self):
        c = live("KABC010343092022")
        self.assertEqual((c.case_status, c.case_sub_stage), ("Case disposed", "STEPS"))

    def test_synthetic_variants(self):
        cases = {
            "sub_stage_four_cell_row.html": "Final decree",
            "sub_stage_separate_row.html": "Final decree",
            "sub_stage_with_value_in_cell.html": "Final decree",
            "sub_stage_in_status_cell.html": "",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                c = syn(name)
                self.assertEqual(c.case_status, "Case disposed")
                self.assertEqual(c.case_sub_stage, expected)

    def test_regression_no_sub_stage_keeps_status_and_none(self):
        c = live("KABC010342242022")
        self.assertEqual(c.case_status, "Case disposed")
        self.assertEqual(c.case_status_raw, "Case disposed")
        self.assertIsNone(c.case_sub_stage)

    def test_pending_synthetic_stage_and_next_date(self):
        c = syn("pending_next_date_stage.html")
        self.assertEqual(c.case_stage, "Evidence")
        self.assertEqual(c.next_hearing_date, "15th January 2026")
        self.assertEqual(c.case_status, "Pending")  # default kept
        self.assertTrue(c.status_assumed)
        self.assertEqual(c.case_status_raw, "")

    def test_db_round_trip_and_none_vs_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = DeeperDatabase(os.path.join(td, "t.db"))
            for c in (live("KABC010342952022"), live("KABC010342242022"), syn("pending_next_date_stage.html"),
                      syn("sub_stage_in_status_cell.html")):
                if not c.cnr_number:
                    c.cnr_number = "SYNTHETIC00000001"
                db.save_case(c)
                got = db.get_case(c.cnr_number)
                for f in ("case_status", "case_status_raw", "case_sub_stage", "case_stage", "next_hearing_date"):
                    self.assertEqual(getattr(got, f), getattr(c, f), f)


class TestIssue3Orders(unittest.TestCase):
    def test_two_order_tables_live(self):
        c = live("KABC010342962022")
        self.assertEqual([(o.order_number, o.order_date) for o in c.orders],
                         [("1", "30-09-2024"), ("2", "10-10-2025")])
        self.assertEqual([o.order_section for o in c.orders], ["Interim Orders", "Final Orders / Judgements"])
        self.assertEqual(c.orders[0].order_description, "Interim Orders | Order on Exhibit on 30-09-2024")
        self.assertEqual(c.orders[1].order_description, "Interim Orders | Order on Exhibit on 2025-10-10")
        self.assertEqual([o.order_category for o in c.orders], ["Interim Orders", "Interim Orders"])

    def test_pdf_params_unchanged_and_args_split(self):
        c = live("KABC010342962022")
        for o in c.orders:
            self.assertTrue(o.pdf_params.startswith("displayPdf('cl+sGJoSJ58oyVespa8VHg==','"))
            args = json.loads(o.pdf_args_json)
            self.assertEqual(len(args), 5)
            self.assertEqual(args[0], "cl+sGJoSJ58oyVespa8VHg==")
            self.assertEqual(args[4], "")
            self.assertEqual(o.pdf_params, "displayPdf(" + ",".join("'%s'" % a for a in args) + ")")

    def test_synthetic_two_tables(self):
        c = syn("two_order_tables.html")
        self.assertEqual(len(c.orders), 2)
        self.assertEqual(len({o.order_section for o in c.orders}), 2)

    def test_regression_single_table(self):
        c = live("KABC010342242022")
        self.assertEqual(len(c.orders), 1)
        self.assertEqual(c.orders[0].order_section, "Final Orders / Judgements")  # site heading, verbatim
        self.assertEqual(c.orders[0].order_category, "Interim Orders")
        self.assertTrue(c.orders[0].pdf_params.startswith("displayPdf("))

    def test_db_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db = DeeperDatabase(os.path.join(td, "t.db"))
            c = live("KABC010342962022")
            db.save_case(c)
            got = db.get_case(c.cnr_number)
            self.assertEqual([o.to_dict() for o in got.orders], [o.to_dict() for o in c.orders])


class TestIssue4Transport(Base):
    def test_w_flag_is_literal_backslash_n(self):
        self.curl.on("x.test", Resp(b"ok"))
        self.sc._curl("https://x.test/")
        argv = self.curl.calls[0]
        self.assertEqual(argv[argv.index("-w") + 1], "\\n%{http_code}")
        self.assertEqual(len(argv[argv.index("-w") + 1]), len("\\n%{http_code}"))

    def test_302_returns_body_with_warning_no_raise(self):
        self.curl.on("x.test", Resp(b"<a>moved</a>", status=302))
        with self.assertLogs("V1.scraper", level="WARNING") as cm:
            body = self.sc._curl("https://x.test/")
        self.assertEqual(body, "<a>moved</a>")
        self.assertEqual(self.sc.last_http_status, 302)
        self.assertTrue(any("302" in m for m in cm.output))

    def test_4xx_5xx_still_raise_with_transient_flag(self):
        for status, transient in ((404, False), (403, False), (429, True), (500, True), (503, True)):
            with self.subTest(status=status):
                self.curl.routes.clear()
                self.curl.on("x.test", Resp(b"err", status=status))
                with self.assertRaises(sm_error()) as cm:
                    self.sc._curl("https://x.test/")
                self.assertEqual(cm.exception.http_status, status)
                self.assertEqual(cm.exception.transient, transient)

    def test_200_still_ok(self):
        self.curl.on("x.test", Resp(b"fine"))
        self.assertEqual(self.sc._curl("https://x.test/"), "fine")
        self.assertEqual(self.sc.last_http_status, 200)


def sm_error():
    from V1.scraper import ScraperHTTPError
    return ScraperHTTPError


class TestIssue5Hearings(unittest.TestCase):
    def test_vb_args_live(self):
        c = live("KABC010342242022")
        self.assertEqual(len(c.hearings), 32)
        first, last = c.hearings[0], c.hearings[-1]
        self.assertEqual((first.hearing_index, first.hearing_seq), (1, 32))
        self.assertEqual((last.hearing_index, last.hearing_seq), (32, 1))
        expected_last = ["3", "20", "20230107", "KABC010342242022", "3", "DisposedP", "02-01-2023",
                         "1147", "KABC01", "CScaseType", "1"]
        self.assertEqual(json.loads(last.vb_args_json), expected_last)
        for name, val in zip(VB_ARG_FIELDS, expected_last):
            self.assertEqual(getattr(last, "vb_" + name), val, name)
        self.assertEqual(last.business_date, "02-01-2023")
        self.assertEqual(last.vb_business_date, last.business_date)
        # first row (latest): nextdate empty, srno 0
        self.assertEqual((first.vb_nextdate, first.vb_srno, first.vb_disposal_flag), ("", "0", "Disposed"))

    def test_all_rows_vb_cino_matches_case_and_date_matches_row(self):
        for cnr in GROUND_TRUTH:
            c = live(cnr)
            for h in c.hearings:
                self.assertEqual(h.vb_cino, cnr)
                self.assertEqual(h.vb_business_date, h.business_date)

    def test_national_court_code_from_ack_link(self):
        self.assertEqual(live("KABC010342242022").national_court_code, "KABC01")

    def test_national_court_code_falls_back_to_vb_args(self):
        html = _read(os.path.join(LIVE, "KABC010342242022.html"))
        html = html.replace("display_case_acknowlegement", "display_case_xx")
        self.assertEqual(parse_deeper_case_detail(html).national_court_code, "KABC01")

    def test_regression_existing_hearing_fields_unchanged(self):
        c = parse_deeper_case_detail(_read(TARGET))
        self.assertEqual([h.hearing_index for h in c.hearings], list(range(1, 33)))
        self.assertEqual(c.hearings[-1].hearing_date, "07-01-2023")

    def test_db_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db = DeeperDatabase(os.path.join(td, "t.db"))
            c = live("KABC010342242022")
            db.save_case(c)
            got = db.get_case(c.cnr_number)
            self.assertEqual([h.to_dict() for h in got.hearings], [h.to_dict() for h in c.hearings])
            self.assertEqual(got.national_court_code, "KABC01")


class TestIssue6SuspectMainFiling(Base):
    def _case(self, cnr, main_cnr, filing):
        return DeeperCaseDetail(
            cnr_number=cnr,
            main_matters=[MainMatterItem("EX/1/2020", main_cnr, filing)] if main_cnr else [],
        )

    def _run(self, cases):
        by_cnr = {c.cnr_number: c for c in cases}

        def fake(vp, meta=None):
            return by_cnr[vp["cino"]]

        batch = [{"case_number": c.cnr_number, "cnr_number": c.cnr_number, "view_params": {"cino": c.cnr_number}}
                 for c in cases]
        with mock.patch.object(self.sc, "scrape_and_save_case", side_effect=fake):
            out = self.sc.batch_extract(batch, limit=len(batch))
        self.assertEqual(len(out), len(cases))
        return self.sc.last_run_summary

    def test_flags_shared_filing_number_across_different_main_cnrs(self):
        with self.assertLogs("V1.scraper", level="WARNING") as cm:
            summ = self._run([
                self._case("A1", "KABC000000010001", "8360"),
                self._case("A2", "KABC000000010002", "8360"),
                self._case("A3", None, ""),
            ])
        self.assertEqual(summ["suspect_main_filing_number"], "8360")
        self.assertTrue(any("8360" in m for m in cm.output))

    def test_absent_when_values_differ(self):
        summ = self._run([self._case("A1", "KABC000000010001", "11"), self._case("A2", "KABC000000010002", "12")])
        self.assertNotIn("suspect_main_filing_number", summ)

    def test_absent_when_same_main_cnr(self):
        summ = self._run([self._case("A1", "KABC000000010001", "11"), self._case("A2", "KABC000000010001", "11")])
        self.assertNotIn("suspect_main_filing_number", summ)

    def test_absent_for_single_case(self):
        summ = self._run([self._case("A1", "KABC000000010001", "11")])
        self.assertNotIn("suspect_main_filing_number", summ)

    def test_parsed_value_kept_verbatim_and_existing_summary_keys(self):
        c = live("KABC010342242022")
        self.assertEqual(c.main_matters[0].main_filing_number, "8360")
        summ = self._run([self._case("A1", "KABC000000010001", "8360"), self._case("A2", "KABC000000010002", "8360")])
        for k in ("run_id", "total", "ok", "failed", "skipped", "errors_by_class"):
            self.assertIn(k, summ)


class TestIssue7SearchRowExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = DeeperDatabase(os.path.join(self._tmp.name, "t.db"))

    def test_search_row_appended_and_null_when_missing(self):
        searched = live("KABC010342242022")
        other = live("KABC010342472022")
        self.db.save_case(searched)
        self.db.save_case(other)
        run1 = self.db.create_search_run(run_id="r1", started_at="t")
        run2 = self.db.create_search_run(run_id="r2", started_at="t")
        self.db.add_search_results(run1, [{"sr_no": "1", "case_number": "OLD", "petitioner": "p-old",
                                           "respondent": "r-old", "cnr_number": searched.cnr_number}])
        self.db.add_search_results(run2, [{
            "sr_no": "7", "case_number": "EX/7/2023", "petitioner": "PET TEXT", "respondent": "RESP TEXT",
            "court_name": "City Civil Court", "cnr_number": searched.cnr_number,
            "view_params": {"cino": searched.cnr_number, "hideparty": "N"}, "raw_cells": ["7", "EX/7/2023"],
        }])
        out = os.path.join(self._tmp.name, "o.json")
        export_to_json(self.db, out)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        by = {d["cnr_number"]: d for d in data}
        row = by[searched.cnr_number]["search_row"]
        self.assertEqual((row["sr_no"], row["case_number"], row["petitioner"], row["respondent"]),
                         ("7", "EX/7/2023", "PET TEXT", "RESP TEXT"))
        self.assertEqual(row["view_params"]["hideparty"], "N")
        self.assertEqual(row["raw_cells"], ["7", "EX/7/2023"])
        self.assertIsNone(by[other.cnr_number]["search_row"])
        # append-only: every pre-existing key is unchanged and still first
        for cnr, case in ((searched.cnr_number, searched), (other.cnr_number, other)):
            base = self.db.get_case(cnr).to_dict()
            self.assertEqual(list(by[cnr])[:-1], list(base))
            self.assertEqual(list(by[cnr])[-1], "search_row")
            for k, v in base.items():
                self.assertEqual(by[cnr][k], v)

    def test_csv_new_columns_appended_at_end(self):
        self.db.save_case(live("KABC010342952022"))
        out = os.path.join(self._tmp.name, "o.csv")
        export_to_csv_flat(self.db, out)
        with open(out, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        head, row = rows[0], rows[1]
        self.assertEqual(head[:19][0], "cnr_number")
        self.assertEqual(head[18], "orders_count")
        self.assertEqual(head[19:], ["case_status_raw", "case_sub_stage", "case_stage",
                                     "next_hearing_date", "national_court_code"])
        self.assertEqual(row[head.index("case_status")], "Case disposed")
        self.assertEqual(row[head.index("case_sub_stage")], "ISSUE DELIVERY WARRANT")

    @unittest.skipUnless(__import__("importlib").util.find_spec("openpyxl"), "openpyxl not installed")
    def test_excel_new_columns(self):
        import openpyxl
        from V1.exporter import export_to_multisheet_excel
        self.db.save_case(live("KABC010342962022"))
        out = os.path.join(self._tmp.name, "o.xlsx")
        export_to_multisheet_excel(self.db, out)
        wb = openpyxl.load_workbook(out)
        orders = [c.value for c in wb["Orders & Judgments"][1]]
        self.assertEqual(orders[:5], ["Case CNR", "Order Number", "Order Date", "Order Details", "PDF Link Params"])
        self.assertEqual(orders[5:], ["Order Section", "Order Category", "Order Description", "PDF Args (JSON)"])
        self.assertEqual(wb["Orders & Judgments"].max_row, 3)
        parties = [c.value for c in wb["Parties & Advocates"][1]]
        self.assertEqual(parties[-1], "Party Seq")
        hear = [c.value for c in wb["Hearing History"][1]]
        self.assertEqual(hear[6], "Hearing Seq")


class TestV3Migration(unittest.TestCase):
    NEW_COLS = {
        "cases": {"case_status_raw", "case_sub_stage", "case_stage", "next_hearing_date", "national_court_code"},
        "parties": {"party_seq"},
        "hearings": {"hearing_seq", "vb_args_json"} | {"vb_" + n for n in VB_ARG_FIELDS},
        "orders": {"order_section", "order_category", "order_description", "pdf_args_json"},
    }

    def _cols(self, path, table):
        with closing(sqlite3.connect(path)) as raw:
            return {r[1] for r in raw.execute("PRAGMA table_info(%s)" % table)}

    def test_pre_v3_db_opens_keeps_data_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "old.db")
            raw = sqlite3.connect(path)
            for ddl in dbm.DDL_STATEMENTS:
                raw.execute(ddl)
            v2 = [m for m in dbm.MIGRATIONS if m[0] == 2][0]
            for ddl in v2[1]:
                raw.execute(ddl)
            for table, col, decl in v2[2]:
                raw.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
            raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)")
            raw.execute("INSERT INTO schema_version VALUES (2, 'then')")
            raw.execute("INSERT INTO cases (cnr_number, case_status, updated_at) VALUES ('OLD1', 'Case disposedSub Stage', 'x')")
            raw.execute("INSERT INTO parties (case_cnr, type, name, advocate) VALUES ('OLD1', 'respondent', 'R', 'A')")
            raw.execute("INSERT INTO hearings (case_cnr, hearing_index, judge, purpose) VALUES ('OLD1', 1, 'J', 'P')")
            raw.execute("INSERT INTO orders (case_cnr, order_number, pdf_params) VALUES ('OLD1', '1', 'displayPdf(x)')")
            raw.commit()
            for table, cols in self.NEW_COLS.items():
                self.assertFalse(cols & {r[1] for r in raw.execute("PRAGMA table_info(%s)" % table)})
            raw.close()

            db = DeeperDatabase(path)
            for table, cols in self.NEW_COLS.items():
                self.assertTrue(cols <= self._cols(path, table), table)
            got = db.get_case("OLD1")
            self.assertEqual(got.case_status, "Case disposedSub Stage")  # old data untouched
            self.assertIsNone(got.case_sub_stage)
            self.assertEqual((got.parties[0].name, got.parties[0].advocate, got.parties[0].party_seq), ("R", "A", 0))
            self.assertEqual((got.hearings[0].judge, got.hearings[0].hearing_seq), ("J", 0))
            self.assertEqual((got.orders[0].pdf_params, got.orders[0].order_section), ("displayPdf(x)", ""))
            with closing(sqlite3.connect(path)) as raw:
                self.assertEqual([r[0] for r in raw.execute("SELECT version FROM schema_version ORDER BY version")], [2, 3])
            before = {t: self._cols(path, t) for t in self.NEW_COLS}

            DeeperDatabase(path)  # reopen: no-op
            with closing(sqlite3.connect(path)) as raw:
                self.assertEqual([r[0] for r in raw.execute("SELECT version FROM schema_version ORDER BY version")], [2, 3])
            self.assertEqual(before, {t: self._cols(path, t) for t in self.NEW_COLS})
            self.assertEqual(DeeperDatabase(path).list_cnrs(), ["OLD1"])

    def test_full_case_round_trip_after_migration(self):
        with tempfile.TemporaryDirectory() as td:
            db = DeeperDatabase(os.path.join(td, "n.db"))
            for cnr in ("KABC010342472022", "KABC010342952022", "KABC010342962022", "KABC010342242022"):
                c = live(cnr)
                db.save_case(c)
                got = db.get_case(cnr).to_dict()
                want = c.to_dict()
                for k in want:
                    self.assertEqual(got[k], want[k], "%s.%s" % (cnr, k))
                db.save_case(c)  # idempotent re-save
                self.assertEqual(len(db.get_case(cnr).hearings), len(c.hearings))


if __name__ == "__main__":
    unittest.main()
