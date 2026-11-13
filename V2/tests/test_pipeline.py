import csv
import json
import os
import tempfile
import unittest

from V2.captcha import CallableSolver
from V2.config import Config, Location, SearchSpec
from V2.export import export_all
from V2.http import EcourtsSession
from V2.pipeline import CaseScraper, Pipeline, ia_body, pdf_body, vb_body
from V2.store import Store
from V2.tests.fakes import FakeEcourts, FakeTransport, GOOD_CAPTCHA

# first 6 rows of the (real) search result: EX/2, 9, 10, 11, 12, 13 of 2023
EXPECTED_HEARINGS = 32 + 14 + 16 + 10 + 19 + 12


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.out = os.path.join(self._td.name, "run")
        self.server = FakeEcourts(n_rows=6)

    def make(self, sleep=lambda d: None, transport_cls=FakeTransport, **cfgkw):
        kw = dict(max_rps=10000, workers=3, backoff_base=0.0, breaker_cooldown=0.0, block_cooldown_base=0.0,
              block_cooldown_max=0.0)
        kw.update(cfgkw)
        cfg = Config(**kw)
        wrong_first = iter(["wrongg"])
        solver = CallableSolver(lambda b: next(wrong_first, GOOD_CAPTCHA))
        pipe = Pipeline(cfg, Location(), SearchSpec(), self.out, solver=solver, sleep=sleep)
        pipe.session_factory = lambda name: EcourtsSession(
            cfg, pipe.loc, transport_cls(self.server), pipe.limiter, pipe.governor, sleep=lambda d: None, name=name)
        self.addCleanup(pipe.store.close)
        return pipe


class TestBodies(unittest.TestCase):
    def test_vb_body_matches_site_js(self):
        args = ["3", "20", "20230306", "KABC010342962022", "3", "DisposedP", "02-01-2023", "18", "KABC01",
                "CScaseType", "1"]
        b = vb_body(args, Location())
        self.assertEqual(list(b), ["court_code", "state_code", "dist_code", "court_complex_code", "nextdate1",
                                   "case_number1", "disposal_flag", "businessDate", "national_court_code",
                                   "court_no", "search_by", "srno"])
        self.assertEqual((b["court_complex_code"], b["nextdate1"], b["case_number1"], b["srno"]),
                         ("1030135", "20230306", "KABC010342962022", "1"))
        args[9] = "cnr"
        self.assertNotIn("court_complex_code", vb_body(args, Location()))

    def test_pdf_body(self):
        self.assertEqual(pdf_body(["a", "b", "c", "d", ""]),
                         {"normal_v": "a", "case_val": "b", "court_code": "c", "filename": "d", "appFlag": ""})


class TestFullRun(Base):
    def test_search_to_export_all_tables(self):
        pipe = self.make()
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        self.assertEqual(s["failed_final"], 0)
        self.assertEqual(s["search"], {"attempts": 2, "total": 2344})
        counts = pipe.store.counts()
        self.assertEqual(counts["cases"], 6)
        self.assertEqual(counts["hearings"], EXPECTED_HEARINGS)
        self.assertEqual(counts["orders"], 6)
        self.assertEqual(counts["ack"], 6)
        for t in ("parties", "acts", "processes", "main_matters", "raw_responses", "search_results", "runs",
                  "search_runs"):
            self.assertGreater(counts[t], 0, t)
        self.assertEqual(counts["errors"], 0)
        # every hearing has per-hearing business text; every order has pdf text and a file on disk
        cur = pipe.store.conn
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM hearings WHERE fetched=1 AND business_text LIKE "
                                     "'Case called out%' AND next_purpose='HEARING'").fetchone()[0],
                         EXPECTED_HEARINGS)
        rows = cur.execute("SELECT pages,length(text),pdf_path,needs_ocr,fetched,sha256 FROM orders").fetchall()
        self.assertEqual(len(rows), 6)
        for pages, n, path, ocr, fetched, sha in rows:
            self.assertGreaterEqual(pages, 1)
            self.assertGreater(n, 20)
            self.assertTrue(os.path.exists(path))
            self.assertEqual((ocr, fetched), (0, 1))
        self.assertIn(8, {r[0] for r in cur.execute("SELECT pages FROM orders")})   # order_0.pdf: 8 pages
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM cases WHERE complete=1").fetchone()[0], 6)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM cases WHERE vb_links<>vb_fetched OR pdf_links<>pdf_saved"
                                     " OR ack_expected<>ack_fetched").fetchone()[0], 0)
        a = cur.execute("SELECT decree_holder, judgement_debtor FROM ack LIMIT 1").fetchone()
        self.assertTrue(a[0] and a[1])
        # summary file
        with open(os.path.join(self.out, "summary.json")) as f:
            self.assertEqual(json.load(f)["ok"], 6)
        for k in ("s_per_case", "cases_per_min", "requests_per_s", "requests"):
            self.assertIsNotNone(s[k], k)
        # export
        info = export_all(pipe.store, self.out)
        self.assertEqual((info["cases"], info["hearings"], info["orders"]), (6, EXPECTED_HEARINGS, 6))
        with open(os.path.join(self.out, "hearings.csv"), encoding="utf-8-sig", newline="") as f:
            hr = list(csv.DictReader(f))
        self.assertEqual(len(hr), EXPECTED_HEARINGS)
        self.assertTrue(all(r["business_text"] for r in hr))
        with open(os.path.join(self.out, "orders.csv"), encoding="utf-8-sig", newline="") as f:
            orr = list(csv.DictReader(f))
        self.assertEqual(len(orr), 6)
        self.assertTrue(all(len(r["text"]) > 20 for r in orr))
        with open(os.path.join(self.out, "cases.csv"), encoding="utf-8-sig", newline="") as f:
            cr = list(csv.DictReader(f))
        self.assertEqual(len(cr), 6)
        by = {r["cnr"]: r for r in cr}
        self.assertEqual(by["KABC010342952022"]["case_sub_stage"], "ISSUE DELIVERY WARRANT")
        self.assertEqual(by["KABC010343082022"]["n_respondents"], "2")
        self.assertEqual(by["KABC010342242022"]["complete"], "1")
        with open(os.path.join(self.out, "json", "KABC010343082022.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(len(d["hearings"]), 16)
        self.assertEqual([p["name"] for p in d["respondents"]], ["BALAJI M D", "ASHA"])
        self.assertEqual(d["hearings"][0]["seq"], 1)
        self.assertTrue(d["hearings"][0]["business_text"])
        self.assertIn("text", d["orders"][0])
        self.assertTrue(d["orders"][0]["text"])
        self.assertTrue(d["ack"]["decree_holder"])
        self.assertEqual(len(d["hearings"][0]["vb_args"]), 11)
        # raw responses stored gzip and readable
        raw = pipe.store.get_raw("KABC010343082022", "home/viewHistory")
        self.assertEqual(len(raw), 1)
        self.assertIn("Case History", json.loads(raw[0]["body"])["data_list"])

    def test_each_worker_has_its_own_session(self):
        pipe = self.make(workers=3)
        pipe.run()
        cookies = {r["cookie"] for r in self.server.log if r["cookie"]}
        threads = {r["thread"] for r in self.server.log if r["endpoint"] == "home/viewHistory"}
        self.assertGreaterEqual(len(cookies), 3)
        self.assertGreaterEqual(len(threads), 2)
        # search ran once only (one captcha flow)
        self.assertEqual(self.server.count("casestatus/submit_case_type"), 2)

    def test_limit(self):
        pipe = self.make(limit=2)
        s = pipe.run()
        self.assertEqual((s["ok"], pipe.store.counts()["cases"]), (2, 2))

    def test_idempotent_and_resume(self):
        pipe = self.make()
        pipe.run()
        before = pipe.store.counts()
        before.pop("runs"), before.pop("search_runs"), before.pop("search_results")
        # plain re-run upserts: identical row counts
        pipe2 = self.make()
        pipe2.run()
        after = pipe2.store.counts()
        for k in before:
            self.assertEqual(after[k], before[k], k)
        # resume: nothing re-scraped
        calls = self.server.count("home/viewHistory")
        pipe3 = self.make(resume=True)
        s3 = pipe3.run()
        self.assertEqual((s3["ok"], s3["skipped_resume"]), (0, 6))
        self.assertEqual(self.server.count("home/viewHistory"), calls)
        self.assertEqual(pipe3.store.counts()["cases"], 6)


class TestRobustness(Base):
    def test_session_killed_mid_run_recovers(self):
        server = self.server
        n = [0]

        class Flaky(FakeTransport):
            def request(self, method, url, data=None, headers=None):
                n[0] += 1
                if n[0] == 40:
                    server.kill_sessions()      # all sessions invalidated server-side
                return super().request(method, url, data, headers)

        pipe = self.make(transport_cls=Flaky)
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        self.assertGreaterEqual(s["recoveries"], 1)
        self.assertEqual(pipe.store.counts()["hearings"], EXPECTED_HEARINGS)

    def test_transport_errors_retried(self):
        pipe = self.make(workers=1)
        # inject disconnects after the handshake of the first case
        n = [0]
        orig = self.server.handle

        def handle(*a, **k):
            n[0] += 1
            if n[0] in (25, 26, 60, 61, 62):
                from V2.http import TransportError
                raise TransportError("RemoteDisconnected")
            return orig(*a, **k)

        self.server.handle = handle
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        self.assertGreaterEqual(s["retries"], 5)

    def test_failed_business_fetch_marks_incomplete_then_retry_round_completes(self):
        self.server.fail_business_for = {"5"}

        def sleep(d):
            if d >= 1.0:                      # the retry-round pause: the site "recovers"
                self.server.fail_business_for = set()

        pipe = self.make(sleep=sleep)
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        self.assertEqual(s["failed_final"], 0)
        c = pipe.store.conn
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cases WHERE complete=1").fetchone()[0], 6)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM hearings WHERE fetched=0").fetchone()[0], 0)

    def test_permanent_failure_is_saved_incomplete_and_reported(self):
        self.server.fail_business_for = {"1"}      # every case has a hearing with srno 1
        pipe = self.make(case_retries=1)
        s = pipe.run()
        self.assertEqual(s["ok"], 0)
        self.assertEqual(s["failed_final"], 6)
        self.assertEqual(len(s["failed_cnrs"]), 6)
        c = pipe.store.conn
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cases WHERE complete=0").fetchone()[0], 6)
        # partial data still stored; the failing hearing carries an error
        self.assertEqual(c.execute("SELECT COUNT(*) FROM hearings WHERE fetched=0 AND error IS NOT NULL")
                         .fetchone()[0], 6)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM hearings WHERE fetched=1").fetchone()[0], 50)
        # resume does not skip incomplete cases
        self.assertEqual(pipe.store.completed_cnrs(), set())

    def test_unknown_case_reported_not_fatal(self):
        pipe = self.make(workers=2)
        coord = pipe._new_session("search")
        res = pipe.search(coord)
        rows = res["rows"] + [{"cnr": "KABC999999999999", "case_number": "EX/9/9999",
                               "view_params": dict(res["rows"][0]["view_params"], cino="KABC999999999999")}]
        pipe2 = self.make(workers=2, case_retries=0)
        s = pipe2.run(rows=rows)
        self.assertEqual(s["ok"], 6)
        self.assertEqual(s["failed_cnrs"], ["KABC999999999999"])
        self.assertGreaterEqual(pipe2.store.counts()["errors"], 1)

    def test_pdf_fetch_failure_marked(self):
        orig = self.server.handle

        def handle(cookie, method, url, data, headers):
            if method == "GET" and "reports/" in url:
                return cookie, __import__("V2.http", fromlist=["Response"]).Response(
                    200, "<html>Security Page</html>", b"<html>Security Page</html>", {})
            return orig(cookie, method, url, data, headers)

        self.server.handle = handle
        pipe = self.make(case_retries=0, limit=1)
        s = pipe.run()
        self.assertEqual(s["ok"], 0)
        row = pipe.store.conn.execute("SELECT fetched, error FROM orders").fetchone()
        self.assertEqual(row[0], 0)
        self.assertTrue(row[1])


class TestMainMatters(Base):
    def test_follow_main_matters_cycle_safe_and_suspect_flag(self):
        pipe = self.make(follow_main_matters=True)
        s = pipe.run()
        self.assertEqual(s["ok"], 6)
        c = pipe.store.conn
        mains = {r[0] for r in c.execute("SELECT main_cnr FROM main_matters WHERE main_cnr<>''")}
        linked = {r[0] for r in c.execute("SELECT cnr FROM cases WHERE kind='linked'")}
        self.assertTrue(mains)
        self.assertEqual(len(mains), 6)
        self.assertEqual(s["linked_main_matters"], len(linked))
        # fake returns the same CNR page for every main matter, so only one stored row per distinct CNR
        self.assertEqual(self.server.count("cnr_status/viewCNRHistory/"), len(mains))   # once per main CNR
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cases WHERE kind='searched'").fetchone()[0], 6)
        self.assertEqual(s["suspect_main_filing_numbers"], ["8360"])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM main_matters WHERE filing_no_suspect=1").fetchone()[0], 6)
        self.assertEqual(c.execute("SELECT MIN(filing_no) FROM main_matters").fetchone()[0], "8360")  # verbatim

    def test_not_followed_by_default(self):
        pipe = self.make()
        pipe.run()
        self.assertEqual(self.server.count("cnr_status/viewCNRHistory/"), 0)


class TestStoreRoundTrip(Base):
    def test_save_twice_same_counts_and_fields(self):
        pipe = self.make(limit=1)
        pipe.run()
        st = pipe.store
        cnr = st.case_cnrs()[0]
        d1 = st.get_case(cnr)
        c1 = st.counts()
        # re-save the same data
        from V2 import parsers
        raw = st.get_raw(cnr, "home/viewHistory")[0]
        case = parsers.parse_case_page(json.loads(raw["body"])["data_list"])
        for h in case["hearings"]:
            h["fetched"] = False
        st.save_case("x", {"case": case, "meta": {"complete": False, "missing": ["x"]}, "raw": []})
        c2 = st.counts()
        for k in ("cases", "hearings", "orders", "parties", "acts", "processes", "main_matters"):
            self.assertEqual(c1[k], c2[k], k)
        self.assertEqual(c2["ack"], 0)             # replaced bundle had no ack
        d2 = st.get_case(cnr)
        self.assertEqual(d2["complete"], 0)
        self.assertEqual(d1["case_status"], d2["case_status"])
        self.assertEqual([p["name"] for p in d1["respondents"]], [p["name"] for p in d2["respondents"]])



class TestInterimApplications(Base):
    CNR = "KABC010000902023"

    def row(self, cnr=None):
        cnr = cnr or self.CNR
        return {"cnr": cnr, "case_number": "EX/1/2023", "parties": "x",
                "view_params": {"case_no": "1", "cino": cnr, "court_code": "3", "hideparty": "",
                                "search_flag": "CScaseType", "state_code": "3", "dist_code": "20",
                                "court_complex_code": "1030135", "search_by": "CScaseType"}}

    def test_ia_body_matches_site_js(self):
        args = ["300000012023", "C", "IA", "1", "2023", "KABC01", "CScaseType"]
        b = ia_body(args, Location())
        self.assertEqual(list(b), ["state_code", "dist_code", "court_complex_code", "ia_no", "cinoia",
                                   "ia_case_type_name", "ia_filno", "ia_filyear", "national_court_code", "search_by"])
        args[6] = "cnr"
        self.assertNotIn("court_complex_code", ia_body(args, Location()))

    def test_ia_case_complete_with_business_stored_and_exported(self):
        self.server.ia_with_rows = {self.CNR}
        pipe = self.make(workers=1, case_retries=0)
        s = pipe.run(rows=[self.row()])
        self.assertEqual(s["ok"], 1)
        self.assertEqual(self.server.ia_calls, 1)
        body = self.server.ia_bodies[0]
        self.assertEqual((body["ia_no"], body["cinoia"], body["ia_case_type_name"], body["ia_filno"],
                          body["ia_filyear"], body["national_court_code"], body["search_by"], body["state_code"],
                          body["court_complex_code"]),
                         ("300000012023", self.CNR, "IA", "1", "2023", "KABC01", "CScaseType", "3", "1030135"))
        st = pipe.store
        d = st.get_case(self.CNR)
        self.assertEqual(d["complete"], 1)
        self.assertEqual((d["ia_links"], d["ia_fetched"]), (1, 1))
        self.assertEqual(len(d["interim_applications"]), 1)
        ia = d["interim_applications"][0]
        self.assertEqual((ia["ia_number"], ia["fetched"], ia["ia_status"]), ("IA/1/2023", 1, "Pending"))
        self.assertEqual(len(ia["business"]), 2)
        self.assertEqual(json.loads(st.get_raw(self.CNR, "home/viewIABusiness")[0]["body"])["status"], 1)
        self.assertEqual(d["extra_sections"], [])
        export_all(st, self.out)
        with open(os.path.join(self.out, "json", self.CNR + ".json"), encoding="utf-8") as f:
            j = json.load(f)
        self.assertEqual(len(j["interim_applications"]), 1)
        self.assertEqual(len(j["interim_applications"][0]["business"]), 2)
        with open(os.path.join(self.out, "ia.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["parties"], "ILYAS SAIT H | SAMAR SHAIKH ALIAS SHAIKH JALALUDDIN")
        self.assertEqual(rows[0]["business"], "ISSUE NOTICE; TO RESPONDENT")

    def test_zero_row_ia_response_is_still_fetched(self):
        pipe = self.make(workers=1, case_retries=0)
        pipe.run(rows=[self.row("KABC010000532023")])
        d = pipe.store.get_case("KABC010000532023")
        self.assertEqual(d["complete"], 1)
        self.assertEqual(d["interim_applications"][0]["business"], [])
        self.assertEqual(d["interim_applications"][0]["business_case_no"], "EX/24/2023")

    def test_failed_ia_fetch_makes_case_incomplete_then_retried(self):
        self.server.fail_ia_for = {self.CNR}

        def sleep(d):
            if d >= 1.0:
                self.server.fail_ia_for = set()

        pipe = self.make(workers=1, sleep=sleep)
        s = pipe.run(rows=[self.row()])
        self.assertEqual(s["ok"], 1)       # incomplete first, completed in the retry round
        self.assertEqual(pipe.store.get_case(self.CNR)["ia_fetched"], 1)

        self.server.fail_ia_for = {self.CNR}
        pipe2 = self.make(workers=1, case_retries=0)
        s = pipe2.run(rows=[self.row()])
        self.assertEqual(s["ok"], 0)
        d = pipe2.store.get_case(self.CNR)
        self.assertEqual(d["complete"], 0)
        self.assertIn("ia seq=1", d["missing"])
        self.assertTrue(d["interim_applications"][0]["error"])

    def test_old_db_without_ia_columns_opens(self):
        import sqlite3
        path = os.path.join(self._td.name, "old.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE cases(cnr TEXT PRIMARY KEY, complete INTEGER)")
        c.commit()
        c.close()
        st = Store(path)
        self.addCleanup(st.close)
        cols = {r[1] for r in st.conn.execute("PRAGMA table_info(cases)")}
        self.assertTrue({"ia_links", "ia_fetched"} <= cols)
        Store(path).close()          # second open is a no-op


if __name__ == "__main__":
    unittest.main()
