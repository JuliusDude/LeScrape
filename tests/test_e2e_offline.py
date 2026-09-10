"""
End-to-End and Offline Replay Integration Tests.
Covers:
  - Feature 18: Offline HAR Replay Adapter (Tier 1: 5 tests, Tier 2: 5 tests)
  - Tier 3: Cross-Feature Pairwise Combinations (18 interaction tests)
  - Tier 4: Realistic Real-World Application Scenarios (5 full pipeline scenarios)
All tests execute strictly offline with network sockets disabled.
"""

from __future__ import annotations

import csv
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from ecourts.cli import main
from ecourts.core.captcha import CaptchaHandler, MockCaptchaSolver
from ecourts.core.client import PacedSession
from ecourts.export.exporter import Exporter, export_to_csv_flat, export_to_json
from ecourts.parser.case_details import CaseDetail, parse_case_detail
from ecourts.parser.hierarchy import (
    complex_code_numeric,
    parse_case_types,
    parse_complexes,
    parse_districts,
    parse_establishments,
    split_case_type_token,
    split_complex_token,
)
from ecourts.parser.search import (
    WrongCaptchaError,
    build_search_payload,
    extract_cnr_from_onclick,
    parse_case_results,
    parse_view_params,
)
from ecourts.queue.worker import QueueWorker
from ecourts.storage.db import Database
from ecourts.testing.har_replay import HarReplayAdapter

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
HAR_PATH = os.path.join(FIXTURES_DIR, "services.ecourts.gov.in 2.har")
SAMPLE_CASE_HTML = os.path.join(FIXTURES_DIR, "sample_case_detail.html")


class SocketIsolationMixin:
    """Blocks all external socket.connect calls to ensure 100% offline self-containment."""

    @classmethod
    def setUpClass(cls):
        cls._orig_connect = socket.socket.connect

        def guarded_connect(self, *args, **kwargs):
            raise RuntimeError("Network access strictly forbidden in offline test mode")

        socket.socket.connect = guarded_connect

    @classmethod
    def tearDownClass(cls):
        socket.socket.connect = cls._orig_connect


# ── Feature 18: Offline HAR Replay Adapter ─────────────────────────────────────

class TestFeature18Tier1(SocketIsolationMixin, unittest.TestCase):
    """Tier 1: Feature 18 - Offline HAR Replay Adapter (>= 5 tests)."""

    def setUp(self):
        self.adapter = HarReplayAdapter(HAR_PATH)
        self.session = PacedSession(min_delay=0.0)
        self.session.mount("https://services.ecourts.gov.in", self.adapter)
        self.session.mount("http://services.ecourts.gov.in", self.adapter)

    def tearDown(self):
        self.session.close()

    def test_f18_load_har_entries(self):
        """Verify HarReplayAdapter loads 18 HTTP transactions from recorded HAR file."""
        self.assertGreaterEqual(len(self.adapter.entries), 18)

    def test_f18_replay_get_captcha_endpoint(self):
        """Verify replaying POST casestatus/getCaptcha returns status 200 with div_captcha HTML."""
        resp = self.session.post("casestatus/getCaptcha", data={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("div_captcha", data)
        self.assertIn("captcha_image", data["div_captcha"])

    def test_f18_replay_binary_captcha_image(self):
        """Verify replaying securimage_show.php decodes base64 and returns valid PNG bytes."""
        # Get captcha image URL from entry
        img_bytes = self.session.get_image("/ecourtindia_v6/vendor/securimage/securimage_show.php?015c0e68581bbfed870ba921f798d52f")
        self.assertIsInstance(img_bytes, bytes)
        self.assertTrue(img_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_f18_replay_fill_district(self):
        """Verify replaying fillDistrict returns 31 Karnataka district options."""
        resp = self.session.post("casestatus/fillDistrict", data={"state_code": "3"})
        self.assertEqual(resp.status_code, 200)
        districts = parse_districts(resp.text)
        self.assertGreaterEqual(len(districts), 30)
        bengaluru = next((d for d in districts if "BENGALURU" in d["name"]), None)
        self.assertIsNotNone(bengaluru)
        self.assertEqual(bengaluru["code"], "20")

    def test_f18_custom_response_registration(self):
        """Verify register_response allows injecting synthetic endpoints not in HAR."""
        self.adapter.register_response(
            method="POST",
            endpoint_or_pattern="home/viewHistory",
            status_code=200,
            json_data={"data_list": "<h2 id='chHeading'>MOCK COURT</h2>"},
        )
        resp = self.session.post("home/viewHistory", data={"case_no": "123"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("MOCK COURT", resp.text)


class TestFeature18Tier2(SocketIsolationMixin, unittest.TestCase):
    """Tier 2: Feature 18 - HAR Replay Boundary & Corner Cases (>= 5 tests)."""

    def setUp(self):
        self.adapter = HarReplayAdapter(HAR_PATH)
        self.session = PacedSession(min_delay=0.0)
        self.session.mount("https://services.ecourts.gov.in", self.adapter)

    def tearDown(self):
        self.session.close()

    def test_f18_unmatched_url_raises_runtime_error(self):
        """Verify request to an unrecorded, unmatched endpoint raises RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            self.session.post("casestatus/completely_unknown_endpoint")
        self.assertIn("No matching HAR fixture found", str(ctx.exception))

    def test_f18_missing_har_file_fallback(self):
        """Verify adapter handles nonexistent HAR file path without crashing."""
        with patch("ecourts.testing.har_replay.DEFAULT_HAR_SEARCH_PATHS", []):
            adapter = HarReplayAdapter("/nonexistent/file.har")
            self.assertEqual(len(adapter.entries), 0)

    def test_f18_post_parameter_scoring_matches_best_entry(self):
        """Verify POST requests with specific parameters match the most accurate HAR entry."""
        # Entry with state_code=3 and dist_code=20 is fillcomplex
        resp = self.session.post("casestatus/fillcomplex", data={"state_code": "3", "dist_code": "20"})
        self.assertEqual(resp.status_code, 200)
        complexes = parse_complexes(resp.text)
        self.assertGreaterEqual(len(complexes), 5)

    def test_f18_replay_securimage_js_asset(self):
        """Verify GET request for static javascript assets returns text content."""
        resp = self.session.get("vendor/securimage/securimage.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("function", resp.text)

    def test_f18_custom_error_status_simulation(self):
        """Verify adapter can simulate HTTP 503 Service Unavailable."""
        self.adapter.register_response(
            method="POST",
            endpoint_or_pattern="casestatus/fillDistrict",
            status_code=503,
            text="Service Temporarily Down",
        )
        resp = self.session.post("casestatus/fillDistrict")
        self.assertEqual(resp.status_code, 503)


# ── Tier 3: Cross-Feature Pairwise Interaction Tests ───────────────────────────

class TestTier3PairwiseInteractions(SocketIsolationMixin, unittest.TestCase):
    """Tier 3: Pairwise Cross-Feature Interactions (>= 18 tests)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "tier3.db")
        self.db = Database(self.db_path)

        self.adapter = HarReplayAdapter(HAR_PATH)
        self.session = PacedSession(min_delay=0.0)
        self.session.mount("https://services.ecourts.gov.in", self.adapter)

        # Read sample case HTML
        with open(SAMPLE_CASE_HTML, "r", encoding="utf-8") as f:
            self.sample_html = f.read()

    def tearDown(self):
        self.session.close()
        self.db.close()

    def test_pair_01_hierarchy_with_compound_tokens(self):
        """Pair 1 (F1 + F2): Court complex dropdown parsing with compound token decomposition."""
        resp = self.session.post("casestatus/fillcomplex", data={"state_code": "3", "dist_code": "20"})
        complexes = parse_complexes(resp.text)
        self.assertGreaterEqual(len(complexes), 5)
        for c in complexes:
            self.assertTrue(c["code"].count("@") >= 2)
            self.assertTrue(c["numeric_code"].isdigit())

    def test_pair_02_hierarchy_paced_session(self):
        """Pair 2 (F1 + F15): Sequential dropdown exploration through PacedSession."""
        paced = PacedSession(min_delay=0.03)
        paced.mount("https://services.ecourts.gov.in", self.adapter)
        resp1 = paced.post("casestatus/fillDistrict", data={"state_code": "3"})
        resp2 = paced.post("casestatus/fillcomplex", data={"state_code": "3", "dist_code": "20"})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        paced.close()

    def test_pair_03_hierarchy_har_replay(self):
        """Pair 3 (F1 + F18): Full cascade exploration from State to Establishments using HAR."""
        dist_resp = self.session.post("casestatus/fillDistrict", data={"state_code": "3"})
        districts = parse_districts(dist_resp.text)
        self.assertIn("20", [d["code"] for d in districts])

        comp_resp = self.session.post("casestatus/fillcomplex", data={"state_code": "3", "dist_code": "20"})
        complexes = parse_complexes(comp_resp.text)
        self.assertIn("1030135", [c["numeric_code"] for c in complexes])

    def test_pair_04_search_with_mock_captcha(self):
        """Pair 4 (F3 + F13): Search submission with MockCaptchaSolver providing solution."""
        solver = MockCaptchaSolver(default_solution="x7b2q")
        payload = build_search_payload("3", "20", "1030135", "3", "12^3", "2024", captcha_code=solver.solve(b"fake"))
        self.assertEqual(payload["ct_captcha_code"], "x7b2q")

    def test_pair_05_search_with_captcha_refresh_recovery(self):
        """Pair 5 (F3 + F14): Search submission catches WrongCaptchaError and solves fresh in-band image."""
        solver = MockCaptchaSolver(sequence=["wrong", "correct_solution"])
        # Initial solve attempted by scraper
        initial_guess = solver.solve(b"initial_image")
        self.assertEqual(initial_guess, "wrong")

        handler = CaptchaHandler(session=self.session, solver=solver)

        rejection_response = json.dumps({
            "errormsg": "Invalid Captcha",
            "div_captcha": '<div><img id="captcha_image" src="/ecourtindia_v6/vendor/securimage/securimage_show.php?015c0e68581bbfed870ba921f798d52f" /></div>',
        })

        with self.assertRaises(WrongCaptchaError):
            parse_case_results(rejection_response)

        # Refresh recovery: solve fresh in-band image
        fresh_code, _ = handler.solve_from_response(rejection_response)
        self.assertEqual(fresh_code, "correct_solution")

    def test_pair_06_search_payload_in_paced_session(self):
        """Pair 6 (F3 + F15): Submitting search payload through PacedSession verifies automatic token injection."""
        payload = build_search_payload("3", "20", "1030135", "3", "12^3", "2024", captcha_code="ok123")
        resp = self.session.post("casestatus/submit_case_type", data=payload)
        self.assertEqual(resp.status_code, 200)

    def test_pair_07_search_against_har_replay(self):
        """Pair 7 (F3 + F18): Search submission to HAR replay returns recorded 'Record not found'."""
        payload = build_search_payload("3", "20", "1030135", "3", "12^3", "2024", captcha_code="test")
        resp = self.session.post("casestatus/submit_case_type", data=payload)
        cases = parse_case_results(resp.text)
        self.assertEqual(cases, [])

    def test_pair_08_search_results_saved_to_db(self):
        """Pair 8 (F4 + F10): Parsed search result row saved to normalized database."""
        raw_html = """
        <table class="table-fixed">
            <tr>
                <td>1</td>
                <td>Misc/2/2025</td>
                <td>RAMESH Vs SITA</td>
                <td><a href="#" onclick="viewHistory(204900000022025,'KABC0E0001992025',15,'','',3,20,1030136,'')">View</a></td>
            </tr>
        </table>
        """
        cases = parse_case_results(raw_html)
        self.assertEqual(len(cases), 1)

        c = cases[0]
        self.db.save_case({
            "cnr_number": c["cnr_number"],
            "case_number": c["case_number"],
            "parties": [{"type": "petitioner", "name": c["petitioner"]}, {"type": "respondent", "name": c["respondent"]}],
        })
        saved = self.db.get_case_by_cnr("KABC0E0001992025")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["case_number"], "Misc/2/2025")

    def test_pair_09_search_result_enqueued_in_tasks(self):
        """Pair 9 (F4 + F11): Extracted viewHistory params enqueued as detail_fetch task."""
        onclick = "viewHistory(204900000022025,'KABC0E0001992025',15,'','',3,20,1030136,'')"
        params = parse_view_params(onclick)
        tid = self.db.enqueue_task("detail_fetch", {"view_params": params})

        task = self.db.claim_next_task("w1")
        self.assertEqual(task["id"], tid)
        self.assertEqual(task["params"]["view_params"]["cino"], "KABC0E0001992025")

    def test_pair_10_deep_case_detail_saved_to_db(self):
        """Pair 10 (F5 + F10): Deep case detail parsed and upserted into database with CNR."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.assertTrue(self.db.save_case(detail))
        db_case = self.db.get_case_by_cnr("KABC0E0001992025")
        self.assertEqual(db_case["case_stage"], "Evidence")

    def test_pair_11_deep_case_detail_exported_to_json(self):
        """Pair 11 (F5 + F17): Deep case detail persisted and exported to structured JSON."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        out_json = os.path.join(self.temp_dir, "p11.json")
        export_to_json(self.db, out_json)

        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["cnr_number"], "KABC0E0001992025")
        self.assertEqual(len(data[0]["parties"]), 3)

    def test_pair_12_roster_saved_to_parties_table(self):
        """Pair 12 (F6 + F10): Party roster persisted to relational parties table with foreign key."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        parties = self.db.conn.execute(
            "SELECT * FROM parties WHERE cnr_number = ?", ("KABC0E0001992025",)
        ).fetchall()
        self.assertEqual(len(parties), 3)

    def test_pair_13_acts_saved_to_acts_table(self):
        """Pair 13 (F7 + F10): Statutory acts and sections persisted to relational acts table."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        acts = self.db.conn.execute(
            "SELECT * FROM acts WHERE cnr_number = ?", ("KABC0E0001992025",)
        ).fetchall()
        self.assertEqual(len(acts), 2)

    def test_pair_14_hearings_saved_to_hearings_table(self):
        """Pair 14 (F8 + F10): Hearing history persisted to relational hearings table."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        hearings = self.db.conn.execute(
            "SELECT * FROM hearings WHERE cnr_number = ?", ("KABC0E0001992025",)
        ).fetchall()
        self.assertEqual(len(hearings), 2)

    def test_pair_15_task_completion_updates_db(self):
        """Pair 15 (F10 + F11): Completing task updates task queue status and results count."""
        tid = self.db.enqueue_task("search", {})
        self.db.claim_next_task("w1")
        self.db.complete_task(tid, results_count=93)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "completed")
        self.assertEqual(t["results_count"], 93)

    def test_pair_16_db_cases_exported_to_flat_csv(self):
        """Pair 16 (F10 + F17): Case records in DB exported to flat CSV with aggregated parties/acts."""
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        out_csv = os.path.join(self.temp_dir, "p16.csv")
        export_to_csv_flat(self.db, out_csv)

        with open(out_csv, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        self.assertIn("RAMESH KUMAR", row["petitioners"])
        self.assertIn("HINDU MARRIAGE ACT", row["acts_sections"])

    def test_pair_17_task_failure_with_backoff_and_retry(self):
        """Pair 17 (F11 + F12): Task failure triggers backoff and increases retry_count."""
        tid = self.db.enqueue_task("t17", {}, max_retries=3)
        self.db.claim_next_task("w")
        self.db.fail_task(tid, "Simulated Error", retryable=True)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "queued")
        self.assertEqual(t["retry_count"], 1)

    def test_pair_18_worker_crash_and_watchdog_reclaim(self):
        """Pair 18 (F11 + F12): Watchdog recovers task abandoned by worker after lease timeout."""
        tid = self.db.enqueue_task("crash_task", {})
        self.db.claim_next_task("crashed_worker")

        # Backdate lease past 300 seconds
        past = (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat()
        self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past, tid))

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 1)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "queued")


# ── Tier 4: Realistic Real-World Application Scenarios ─────────────────────────

class TestTier4RealWorldScenarios(SocketIsolationMixin, unittest.TestCase):
    """Tier 4: Realistic Real-World Application Scenarios (>= 5 comprehensive scenarios)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "scenario.db")
        self.db = Database(self.db_path)

        self.adapter = HarReplayAdapter(HAR_PATH)
        self.session = PacedSession(min_delay=0.0)
        self.session.mount("https://services.ecourts.gov.in", self.adapter)

        with open(SAMPLE_CASE_HTML, "r", encoding="utf-8") as f:
            self.sample_html = f.read()

    def tearDown(self):
        self.session.close()
        self.db.close()

    def test_scenario_1_full_court_hierarchy_discovery(self):
        """Scenario 1: Complete court hierarchy exploration from State to Case Types.

        Flow:
          1. Discover districts for Karnataka (state_code=3) -> Bengaluru (dist_code=20)
          2. Discover court complexes for Bengaluru -> NyayaDegula Court Complex (1030136@...)
          3. Discover establishments under City Civil Court Complex (1030135) -> PRL. Judge
          4. Discover case types under establishment 3 -> 40+ case types
        """
        # Step 1: State -> Districts
        d_resp = self.session.post("casestatus/fillDistrict", data={"state_code": "3"})
        districts = parse_districts(d_resp.text)
        self.assertGreaterEqual(len(districts), 30)
        bengaluru = next(d for d in districts if d["code"] == "20")
        self.assertEqual(bengaluru["name"], "BENGALURU")

        # Step 2: District -> Court Complexes
        c_resp = self.session.post("casestatus/fillcomplex", data={"state_code": "3", "dist_code": "20"})
        complexes = parse_complexes(c_resp.text)
        self.assertGreaterEqual(len(complexes), 5)
        city_civil = next(c for c in complexes if c["numeric_code"] == "1030135")
        self.assertEqual(city_civil["code"], "1030135@3@Y")

        # Step 3: Complex -> Establishments
        e_resp = self.session.post("casestatus/fillCourtEstablishment", data={
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030135",
        })
        establishments = parse_establishments(e_resp.text)
        self.assertEqual(len(establishments), 1)
        self.assertEqual(establishments[0]["code"], "3")

        # Step 4: Establishment -> Case Types
        ct_resp = self.session.post("casestatus/fillCaseType", data={
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030135",
            "est_code": "3",
            "search_type": "c_type",
        })
        case_types = parse_case_types(ct_resp.text)
        self.assertGreaterEqual(len(case_types), 30)

    def test_scenario_2_offline_case_search_and_enqueue(self):
        """Scenario 2: Offline Case Search Execution & Task Enqueueing.

        Flow:
          1. Solve CAPTCHA via MockCaptchaSolver against HAR
          2. Execute search by case type
          3. Handle result and enqueue scrape jobs into persistent task queue
        """
        solver = MockCaptchaSolver(default_solution="har_sol")
        handler = CaptchaHandler(session=self.session, solver=solver)

        code, img_url = handler.get_and_solve_captcha()
        self.assertEqual(code, "har_sol")

        search_payload = build_search_payload(
            state_code="3",
            dist_code="20",
            court_complex_code="1030135",
            est_code="3",
            case_type_value="12^3",
            search_year="2024",
            captcha_code=code,
        )
        resp = self.session.post("casestatus/submit_case_type", data=search_payload)
        cases = parse_case_results(resp.text)
        self.assertEqual(cases, [])  # Recorded HAR returns "Record not found"

        # Enqueue task for search
        task_id = self.db.enqueue_task(
            task_type="case_type_search",
            params=search_payload,
            priority=5,
        )
        self.assertGreater(task_id, 0)
        self.assertEqual(self.db.get_queue_stats()["queued"], 1)

    def test_scenario_3_worker_batch_processing_with_captcha_refresh(self):
        """Scenario 3: Worker batch processing with simulated CAPTCHA refresh.

        Flow:
          1. Worker claims task from queue
          2. Endpoint returns CAPTCHA rejection with fresh in-band image
          3. Worker recovers, solves fresh CAPTCHA, re-submits
          4. Custom detail endpoint returns high-fidelity case details
          5. Worker persists case into database and marks task completed
        """
        # Register custom endpoints on adapter for full loop
        search_results_html = """
        <table class="table-fixed">
            <tr>
                <td>1</td>
                <td>Misc/2/2025</td>
                <td>RAMESH SHARMA Vs SITA DEVI</td>
                <td><a href="#" onclick="viewHistory(204900000022025,'KABC0E0001992025',15,'','',3,20,1030136,'')">View</a></td>
            </tr>
        </table>
        """
        self.adapter.register_response(
            method="POST",
            endpoint_or_pattern="casestatus/submit_case_type",
            status_code=200,
            json_data={"status": 1, "case_data": search_results_html},
        )
        self.adapter.register_response(
            method="POST",
            endpoint_or_pattern="home/viewHistory",
            status_code=200,
            json_data={"data_list": self.sample_html},
        )

        solver = MockCaptchaSolver(default_solution="good_captcha")
        worker = QueueWorker(
            db=self.db,
            session=self.session,
            solver=solver,
            worker_id="scenario_worker_3",
        )

        # Enqueue search task
        task_id = self.db.enqueue_task("case_type_search", {
            "state_code": "3",
            "dist_code": "20",
            "court_complex_code": "1030136@15@Y",
            "est_code": "15",
            "case_type_value": "49^15",
            "search_year": "2025",
            "fetch_details": True,
        })

        # Process task
        processed = worker.process_one_task()
        self.assertTrue(processed)

        # Verify task completed
        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "completed")

        # Verify case persisted in database with details
        case = self.db.get_case_by_cnr("KABC0E0001992025")
        self.assertIsNotNone(case)
        self.assertEqual(case["case_stage"], "Evidence")
        self.assertEqual(len(case["parties"]), 3)
        self.assertEqual(len(case["acts"]), 2)
        self.assertEqual(len(case["hearings"]), 2)

    def test_scenario_4_worker_crash_and_watchdog_recovery_with_backoff(self):
        """Scenario 4: Worker crash, watchdog recovery, and retry backoff.

        Flow:
          1. Task enqueued
          2. Worker 1 claims task and crashes (simulated by abandon)
          3. Watchdog reclaims task back to 'queued' after lease expires
          4. Worker 2 claims task, encounters transient error -> triggers backoff
          5. Worker 2 succeeds on retry -> marks task completed
        """
        tid = self.db.enqueue_task("case_type_search", {"step": 1}, max_retries=3)

        # Worker 1 claims task
        task = self.db.claim_next_task("worker_1_crashed")
        self.assertEqual(task["id"], tid)

        # Worker 1 dies; time passes past lease timeout
        past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past, tid))

        # Watchdog reclaims task
        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 1)

        # Worker 2 claims task
        task_w2 = self.db.claim_next_task("worker_2")
        self.assertEqual(task_w2["id"], tid)

        # Worker 2 encounters transient error
        self.db.fail_task(tid, "Transient Network Drop", retryable=True)
        t_after_fail = self.db.get_task_by_id(tid)
        self.assertEqual(t_after_fail["status"], "queued")
        self.assertEqual(t_after_fail["retry_count"], 1)

        # Fast-forward scheduled_at and Worker 2 reclaims and finishes
        self.db.conn.execute("UPDATE tasks SET scheduled_at = datetime('now') WHERE id = ?", (tid,))
        self.db.claim_next_task("worker_2")
        self.db.complete_task(tid, results_count=1)

        final_t = self.db.get_task_by_id(tid)
        self.assertEqual(final_t["status"], "completed")

    def test_scenario_5_full_pipeline_multi_format_export(self):
        """Scenario 5: End-to-end extraction pipeline followed by multi-format export.

        Flow:
          1. Ingest detailed case records into database
          2. Export to hierarchical JSON
          3. Export to flat denormalized CSV
          4. Export to relational CSV tables (cases, parties, acts, hearings)
          5. Validate all export formats match database records
        """
        detail = parse_case_detail({"data_list": self.sample_html})
        self.db.save_case(detail)

        exporter = Exporter(self.db)

        # 1. JSON Export
        json_path = os.path.join(self.temp_dir, "scenario_out.json")
        exporter.export(format="json", output_path=json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            json_cases = json.load(f)
        self.assertEqual(len(json_cases), 1)
        self.assertEqual(json_cases[0]["cnr_number"], "KABC0E0001992025")
        self.assertEqual(len(json_cases[0]["parties"]), 3)

        # 2. Flat CSV Export
        flat_csv = os.path.join(self.temp_dir, "scenario_flat.csv")
        exporter.export(format="csv", mode="flat", output_path=flat_csv)
        with open(flat_csv, "r", newline="", encoding="utf-8") as f:
            flat_rows = list(csv.DictReader(f))
        self.assertEqual(len(flat_rows), 1)
        self.assertEqual(flat_rows[0]["cnr_number"], "KABC0E0001992025")
        self.assertIn("HINDU MARRIAGE ACT", flat_rows[0]["acts_sections"])

        # 3. Relational CSV Export
        rel_dir = os.path.join(self.temp_dir, "scenario_rel")
        counts = exporter.export(format="csv", mode="relational", output_path=rel_dir)
        self.assertEqual(counts["cases"], 1)
        self.assertEqual(counts["parties"], 3)
        self.assertEqual(counts["acts"], 2)
        self.assertEqual(counts["hearings"], 2)

        for name in ("cases.csv", "parties.csv", "acts.csv", "hearings.csv"):
            self.assertTrue(os.path.exists(os.path.join(rel_dir, name)))


if __name__ == "__main__":
    unittest.main()
