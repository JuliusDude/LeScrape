"""
V1.test_phase1
=======================
Offline tests for Phase 1 remediation (transport errors, raw store, migration,
CNR guard, search outcomes, batch summary, status_assumed, exporter semantics).

``V1.scraper.subprocess.run`` and ``V1.scraper.time`` are patched;
no network is touched.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from V1 import scraper as sm
    from V1.db import DeeperDatabase
    from V1.exporter import export_to_csv_flat, export_to_json
    from V1.parser import parse_deeper_case_detail
    from V1.test_request_snapshots import CAPTCHA_JSON, IMAGE_BYTES, FakeTime, StubSolver
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    sm = None
    _IMPORT_ERROR = exc

HERE = os.path.dirname(os.path.abspath(__file__))
SYN = os.path.join(HERE, "fixtures", "synthetic")
TARGET = os.path.join(HERE, "fixtures", "target_case_view.html")
TARGET_CNR = "KABC010342242022"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _syn(name):
    return _read(os.path.join(SYN, name))


class Resp:
    """Scripted curl result."""

    def __init__(self, body=b"", status=200, returncode=0, exc=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.returncode = returncode
        self.exc = exc


class FakeCurl:
    """subprocess.run replacement: per-URL-substring queues of Resp (last one repeats)."""

    def __init__(self):
        self.routes = []  # list of [substring, [Resp...]]
        self.calls = []

    def on(self, substring, *resps):
        self.routes.append([substring, list(resps)])

    def count(self, substring):
        return sum(1 for c in self.calls if substring in c[-1])

    def run(self, cmd, *args, **kwargs):
        argv = list(cmd)
        self.calls.append(argv)
        url = argv[-1]
        for sub, queue in self.routes:
            if sub in url:
                r = queue.pop(0) if len(queue) > 1 else queue[0]
                break
        else:
            r = Resp(b"<html/>")
        if r.exc:
            raise r.exc
        stdout = r.body
        if "-w" in argv and r.returncode == 0:
            stdout += b"\n" + str(r.status).encode()
        return subprocess.CompletedProcess(argv, r.returncode, stdout=stdout, stderr=b"")


class Base(unittest.TestCase):
    def setUp(self):
        if sm is None:
            self.skipTest("deps unavailable: %s" % _IMPORT_ERROR)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "t.db")
        self.curl = FakeCurl()
        p1 = mock.patch.object(sm.subprocess, "run", self.curl.run)
        p2 = mock.patch.object(sm, "time", FakeTime())
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.sc = sm.DeeperScraper(db_path=self.db_path, cookie_file=os.path.join(self._tmp.name, "c.txt"))
        self.sc.solver = StubSolver()
        self.db = self.sc.db

    def vh_json(self, html, token="tok2"):
        return json.dumps({"data_list": html, "app_token": token})


class TestCurl(Base):
    def test_argv_has_timeout_flags(self):
        self.curl.on("x.test", Resp(b"hi"))
        self.sc._curl("https://x.test/")
        argv = self.curl.calls[0]
        self.assertEqual(argv[argv.index("--connect-timeout") + 1], "15")
        self.assertEqual(argv[argv.index("--max-time") + 1], "60")
        self.assertEqual(argv[argv.index("-w") + 1], "\\n%{http_code}")

    def test_text_body_identical_and_status_recorded(self):
        body = "line1\nline2 हिंदी\n"
        self.curl.on("x.test", Resp(body))
        self.assertEqual(self.sc._curl("https://x.test/"), body)
        self.assertEqual(self.sc.last_http_status, 200)
        self.assertEqual(self.sc.last_raw_body, body)

    def test_binary_body_identical(self):
        self.curl.on("x.test", Resp(IMAGE_BYTES + b"\n"))
        self.assertEqual(self.sc._curl("https://x.test/", is_binary=True), IMAGE_BYTES + b"\n")

    def test_http_500_transient(self):
        self.curl.on("x.test", Resp(b"oops", status=500))
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://x.test/")
        self.assertTrue(cm.exception.transient)
        self.assertEqual(cm.exception.http_status, 500)

    def test_http_429_transient_403_not(self):
        self.curl.on("a.test", Resp(b"", status=429))
        self.curl.on("b.test", Resp(b"", status=403))
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://a.test/")
        self.assertTrue(cm.exception.transient)
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://b.test/")
        self.assertFalse(cm.exception.transient)
        self.assertEqual(cm.exception.http_status, 403)

    def test_returncode_28_transient_other_not(self):
        self.curl.on("a.test", Resp(returncode=28))
        self.curl.on("b.test", Resp(returncode=2))
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://a.test/")
        self.assertTrue(cm.exception.transient)
        self.assertEqual(cm.exception.returncode, 28)
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://b.test/")
        self.assertFalse(cm.exception.transient)

    def test_timeout_expired_transient(self):
        self.curl.on("x.test", Resp(exc=subprocess.TimeoutExpired("curl", 75)))
        with self.assertRaises(sm.ScraperHTTPError) as cm:
            self.sc._curl("https://x.test/")
        self.assertTrue(cm.exception.transient)

    def test_subprocess_timeout_passed(self):
        seen = {}

        def run(cmd, *a, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"x\n200", stderr=b"")

        with mock.patch.object(sm.subprocess, "run", run):
            self.sc._curl("https://x.test/")
        self.assertEqual(seen.get("timeout"), 75)


class TestMigration(unittest.TestCase):
    LEGACY_DDL = [
        """CREATE TABLE IF NOT EXISTS cases (
            cnr_number TEXT PRIMARY KEY, case_type TEXT, case_number TEXT, filing_number TEXT,
            filing_date TEXT, registration_number TEXT, registration_date TEXT, efiling_number TEXT,
            efiling_date TEXT, first_hearing_date TEXT, decision_date TEXT, case_status TEXT,
            nature_of_disposal TEXT, court_number_judge TEXT, court_name TEXT, state_code TEXT,
            dist_code TEXT, court_complex_code TEXT, est_code TEXT, updated_at TEXT NOT NULL);""",
        """CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, type TEXT NOT NULL,
            name TEXT NOT NULL, advocate TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS acts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, act TEXT NOT NULL, section TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, process_id TEXT NOT NULL,
            process_title TEXT, process_date TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS main_matters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, main_case_number TEXT,
            main_cnr_number TEXT, main_filing_number TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS hearings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, hearing_index INTEGER NOT NULL,
            judge TEXT, business_date TEXT, hearing_date TEXT, purpose TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_cnr TEXT NOT NULL, order_number TEXT,
            order_date TEXT, order_details TEXT, pdf_params TEXT,
            FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE);""",
    ]

    def test_legacy_db_migrates_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.db")
            conn = sqlite3.connect(path)
            for ddl in self.LEGACY_DDL:
                conn.execute(ddl)
            conn.execute("INSERT INTO cases (cnr_number, case_type, case_status, court_name, updated_at) VALUES ('LEG1','EX','Pending','Court','2020-01-01')")
            conn.execute("INSERT INTO parties (case_cnr, type, name, advocate) VALUES ('LEG1','petitioner','A','Adv')")
            conn.execute("INSERT INTO hearings (case_cnr, hearing_index, judge, purpose) VALUES ('LEG1',1,'J','P')")
            conn.commit()
            conn.close()

            db = DeeperDatabase(path)
            case = db.get_case("LEG1")
            self.assertEqual((case.case_type, case.case_status, case.court_name), ("EX", "Pending", "Court"))
            self.assertEqual([(p.type, p.name, p.advocate) for p in case.parties], [("petitioner", "A", "Adv")])
            self.assertEqual(len(case.hearings), 1)
            self.assertFalse(case.status_assumed)
            self.assertEqual(case.view_params_json, "")
            self.assertIsNone(case.source_response_id)

            raw = sqlite3.connect(path)
            cols = {r[1] for r in raw.execute("PRAGMA table_info(cases)")}
            self.assertTrue({"view_params_json", "source_response_id", "status_assumed"} <= cols)
            row = raw.execute("SELECT view_params_json, source_response_id, status_assumed FROM cases").fetchone()
            self.assertEqual(row, (None, None, 0))
            tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"raw_responses", "fetch_errors", "search_runs", "search_results", "schema_version"} <= tables)
            self.assertEqual(raw.execute("SELECT version FROM schema_version").fetchall(), [(2,), (3,)])
            raw.close()

            DeeperDatabase(path)  # reopen: no-op
            raw = sqlite3.connect(path)
            self.assertEqual(raw.execute("SELECT version FROM schema_version").fetchall(), [(2,), (3,)])
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)
            raw.close()


class TestRawStore(Base):
    def test_ok_response_stored_redacted_and_round_trips(self):
        body = _syn("viewhistory_ok.json")
        self.curl.on("home/viewHistory", Resp(body))
        self.sc.app_token = "SECRET"
        html = self.sc.fetch_view_history({"cino": TARGET_CNR, "case_no": "1"})
        self.assertEqual(html, _read(TARGET))
        rid = self.sc.last_raw_response_id
        row = self.db.get_raw_response(rid)
        self.assertEqual(row["body"], body)
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["cnr_number"], TARGET_CNR)
        params = json.loads(row["request_params_json"])
        self.assertEqual(params["app_token"], "<redacted>")
        self.assertNotIn("SECRET", row["request_params_json"])
        self.assertTrue(self.db.has_raw_response(TARGET_CNR))
        self.assertFalse(self.db.has_raw_response("NOPE"))

    def test_error_json_returns_empty_with_raw_and_error_rows(self):
        self.curl.on("home/viewHistory", Resp(_syn("viewhistory_error.json")))
        self.assertEqual(self.sc.fetch_view_history({"cino": "C1"}), "")
        self.assertIsNotNone(self.sc.last_raw_response_id)
        errs = self.db.list_errors()
        self.assertEqual(len(errs), 1)
        self.assertIn(errs[0]["error_class"], ("MissingDataList", "ParseError"))
        self.assertEqual(errs[0]["raw_response_id"], self.sc.last_raw_response_id)

    def test_non_json_is_parse_error(self):
        self.curl.on("home/viewHistory", Resp("<html>nope</html>"))
        self.assertEqual(self.sc.fetch_view_history({"cino": "C1"}), "")
        self.assertEqual(self.db.list_errors()[0]["error_class"], "ParseError")

    def test_transient_then_ok_retries(self):
        self.curl.on("home/viewHistory", Resp(b"", status=500), Resp(_syn("viewhistory_ok.json")))
        html = self.sc.fetch_view_history({"cino": TARGET_CNR})
        self.assertEqual(html, _read(TARGET))
        self.assertEqual(self.curl.count("home/viewHistory"), 2)
        self.assertEqual(self.db.list_errors(), [])

    def test_three_failures_gives_empty_and_error_row(self):
        self.curl.on("home/viewHistory", Resp(b"", status=500))
        self.assertEqual(self.sc.fetch_view_history({"cino": "C1"}), "")
        self.assertEqual(self.curl.count("home/viewHistory"), 3)
        errs = self.db.list_errors()
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["error_class"], "ScraperHTTPError")
        self.assertEqual(errs[0]["http_status"], 500)

    def test_non_transient_not_retried(self):
        self.curl.on("home/viewHistory", Resp(b"", status=403))
        self.assertEqual(self.sc.fetch_view_history({"cino": "C1"}), "")
        self.assertEqual(self.curl.count("home/viewHistory"), 1)

    def test_post_api_non_json_records_error(self):
        self.curl.on("casestatus/set_data", Resp("<html>not json</html>"))
        self.assertEqual(self.sc._post_api("casestatus/set_data", {}), {})
        errs = self.db.list_errors(self.sc.run_id)
        self.assertEqual(errs[0]["error_class"], "UnexpectedResponse")


class TestCnrGuard(Base):
    def test_mismatch_raises_and_writes_nothing(self):
        html = _syn("no_cnr_row_with_main_cnr.html")
        self.curl.on("home/viewHistory", Resp(self.vh_json(html)))
        with self.assertRaises(sm.CnrMismatchError):
            self.sc.scrape_and_save_case({"cino": TARGET_CNR})
        self.assertEqual(self.db.list_cnrs(), [])
        errs = self.db.list_errors()
        self.assertEqual([e["error_class"] for e in errs], ["CnrMismatch"])
        self.assertIsNotNone(errs[0]["raw_response_id"])

    def test_match_saves_with_provenance(self):
        self.curl.on("home/viewHistory", Resp(_syn("viewhistory_ok.json")))
        vp = {"cino": TARGET_CNR, "case_no": "1", "state_code": "3", "dist_code": "20", "court_complex_code": "1030135"}
        case = self.sc.scrape_and_save_case(vp, meta={"state_code": "9", "est_code": "3"})
        got = self.db.get_case(TARGET_CNR)
        self.assertEqual(json.loads(got.view_params_json), vp)
        self.assertEqual(got.source_response_id, self.sc.last_raw_response_id)
        self.assertEqual(got.state_code, "9")  # meta wins
        self.assertEqual(got.dist_code, "20")  # fallback to view_params
        self.assertEqual(got.court_complex_code, "1030135")
        self.assertEqual(got.est_code, "3")
        self.assertEqual(case.case_status, got.case_status)


class TestSearch(Base):
    def setUp(self):
        super().setUp()
        self.curl.on("casestatus/set_data", Resp(json.dumps({"status": 1})))
        self.curl.on("casestatus/getCaptcha", Resp(CAPTCHA_JSON))
        self.curl.on("securimage_show.php", Resp(IMAGE_BYTES))

    def _rows(self, table):
        raw = sqlite3.connect(self.db_path)
        raw.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in raw.execute("SELECT * FROM %s" % table)]
        finally:
            raw.close()

    def test_captcha_rejected_then_three_rows(self):
        self.curl.on("submit_case_type", Resp(_syn("search_errormsg.json")), Resp(_syn("search_three_rows.json")))
        rows = self.sc.search_cases("12^3", "2024", case_status="Pending")
        self.assertEqual(len(rows), 3)
        self.assertEqual([e["error_class"] for e in self.db.list_errors()], ["CaptchaRejected"])
        runs = self._rows("search_runs")
        self.assertEqual((runs[0]["outcome"], runs[0]["row_count"], runs[0]["attempts"]), ("ok", 3, 2))
        self.assertEqual(len(self._rows("search_results")), 3)
        raw = self._rows("raw_responses")
        self.assertEqual(len(raw), 2)
        self.assertIn("<redacted>", raw[0]["request_params_json"])
        self.assertNotIn("abc123", raw[0]["request_params_json"])

    def test_record_not_found_is_empty(self):
        self.curl.on("submit_case_type", Resp(_syn("search_record_not_found.json")))
        self.assertEqual(self.sc.search_cases("12^3", "2024"), [])
        runs = self._rows("search_runs")
        self.assertEqual((runs[0]["outcome"], runs[0]["row_count"]), ("empty", 0))
        self.assertEqual(self.db.list_errors(), [])

    def test_invalid_captcha_in_case_data_retries(self):
        bad = json.dumps({"case_data": "<div>INVALID CAPTCHA</div>", "status": 1})
        self.curl.on("submit_case_type", Resp(bad), Resp(_syn("search_three_rows.json")))
        self.assertEqual(len(self.sc.search_cases("12^3", "2024")), 3)
        self.assertEqual([e["error_class"] for e in self.db.list_errors()], ["CaptchaRejected"])

    def test_http_error_during_attempt_is_retried(self):
        self.curl.on("submit_case_type", Resp(b"", status=500), Resp(_syn("search_three_rows.json")))
        self.assertEqual(len(self.sc.search_cases("12^3", "2024")), 3)
        self.assertEqual([e["error_class"] for e in self.db.list_errors()], ["ScraperHTTPError"])

    def test_all_attempts_rejected(self):
        self.curl.on("submit_case_type", Resp(_syn("search_errormsg.json")))
        self.assertEqual(self.sc.search_cases("12^3", "2024", max_retries=2), [])
        runs = self._rows("search_runs")
        self.assertEqual((runs[0]["outcome"], runs[0]["attempts"]), ("captcha_failed", 2))

    def test_non_captcha_errormsg_is_search_error(self):
        self.curl.on("submit_case_type", Resp(json.dumps({"errormsg": "Server busy", "status": 0})))
        self.assertEqual(self.sc.search_cases("12^3", "2024"), [])
        self.assertEqual([e["error_class"] for e in self.db.list_errors()], ["SearchError"])
        self.assertEqual(self._rows("search_runs")[0]["outcome"], "error")


class TestBatch(Base):
    def _cases(self, n=3):
        return [
            {"case_number": "EX/%d/2022" % i, "cnr_number": "KABC01034224%04d" % i,
             "view_params": {"cino": "KABC01034224%04d" % i, "case_no": str(i)}}
            for i in range(1, n + 1)
        ]

    def _ok_html(self, cnr):
        return _read(TARGET).replace(TARGET_CNR, cnr)

    def test_one_permanent_failure(self):
        # The fake routes by URL only, so dispatch on the posted form body instead.
        def run(cmd, *a, **kw):
            argv = list(cmd)
            self.curl.calls.append(argv)
            form = argv[argv.index("-d") + 1] if "-d" in argv else ""
            if "cino=KABC010342240002" in form:
                return subprocess.CompletedProcess(argv, 0, stdout=b"boom\n500", stderr=b"")
            cnr = "KABC010342240001" if "cino=KABC010342240001" in form else "KABC010342240003"
            body = self.vh_json(self._ok_html(cnr)).encode()
            return subprocess.CompletedProcess(argv, 0, stdout=body + b"\n200", stderr=b"")

        with mock.patch.object(sm.subprocess, "run", run):
            out = self.sc.batch_extract(self._cases())
        self.assertEqual([c.cnr_number for c in out], ["KABC010342240001", "KABC010342240003"])
        s = self.sc.last_run_summary
        self.assertEqual((s["total"], s["ok"], s["failed"], s["skipped"]), (3, 2, 1, 0))
        self.assertEqual(s["errors_by_class"], {"ValueError": 1})
        errs = [e for e in self.db.list_errors(s["run_id"]) if e["error_class"] == "ValueError"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["cnr_number"], "KABC010342240002")

    def test_skip_existing(self):
        def run(cmd, *a, **kw):
            argv = list(cmd)
            form = argv[argv.index("-d") + 1]
            cnr = "KABC010342240001" if "cino=KABC010342240001" in form else "KABC010342240002"
            body = self.vh_json(self._ok_html(cnr)).encode()
            return subprocess.CompletedProcess(argv, 0, stdout=body + b"\n200", stderr=b"")

        with mock.patch.object(sm.subprocess, "run", run):
            first = self.sc.batch_extract(self._cases(2))
            self.assertEqual(len(first), 2)
            second = self.sc.batch_extract(self._cases(2), skip_existing=True)
        self.assertEqual(second, [])
        s = self.sc.last_run_summary
        self.assertEqual((s["ok"], s["failed"], s["skipped"]), (0, 0, 2))

    def test_no_view_params_skipped_and_recorded(self):
        out = self.sc.batch_extract([{"case_number": "X", "cnr_number": "C9", "view_params": None}])
        self.assertEqual(out, [])
        s = self.sc.last_run_summary
        self.assertEqual((s["ok"], s["failed"], s["skipped"]), (0, 0, 1))
        errs = self.db.list_errors(s["run_id"])
        self.assertEqual(errs[0]["error_class"], "NoViewParams")
        self.assertEqual(errs[0]["cnr_number"], "C9")


class TestStatusAssumed(unittest.TestCase):
    def test_present_status_not_assumed(self):
        c = parse_deeper_case_detail(_read(TARGET))
        self.assertFalse(c.status_assumed)
        self.assertEqual(c.case_status, "Case disposed")

    def test_missing_status_defaults_pending_and_flags(self):
        html = _read(TARGET)
        self.assertIn("<strong>Case Status </strong>", html)
        html = html.replace("<strong>Case Status </strong>", "<strong>Zzz </strong>")
        c = parse_deeper_case_detail(html)
        self.assertEqual(c.case_status, "Pending")
        self.assertTrue(c.status_assumed)
        self.assertTrue(c.to_dict()["status_assumed"])

    def test_flag_persists_in_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DeeperDatabase(os.path.join(tmp, "a.db"))
            c = parse_deeper_case_detail(_read(TARGET).replace("<strong>Case Status </strong>", "<strong>Zzz </strong>"))
            db.save_case(c)
            self.assertTrue(db.get_case(c.cnr_number).status_assumed)


class TestExporterEmptyList(unittest.TestCase):
    def test_empty_list_vs_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DeeperDatabase(os.path.join(tmp, "e.db"))
            db.save_case(parse_deeper_case_detail(_read(TARGET)))
            jp, cp = os.path.join(tmp, "a.json"), os.path.join(tmp, "a.csv")
            export_to_json(db, jp, cnr_list=[])
            export_to_csv_flat(db, cp, cnr_list=[])
            self.assertEqual(json.loads(_read(jp)), [])
            with open(cp, newline="", encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.reader(f))), 1)  # header only
            export_to_json(db, jp, cnr_list=None)
            export_to_csv_flat(db, cp)
            self.assertEqual(len(json.loads(_read(jp))), 1)
            with open(cp, newline="", encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.reader(f))), 2)


if __name__ == "__main__":
    unittest.main()
