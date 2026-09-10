"""
tests.test_empirical_challenger
================================
Adversarial Stress and Empirical Verification Test Suite by Challenger 1.
Author: Challenger 1 (Empirical Adversarial Verifier)

Scope:
  1. Extreme & malformed HTML parsing inputs (corrupted tags, massive rosters, missing columns).
  2. Multi-threaded PacedSession pacing verification (verifying minimum interval enforcement under concurrency).
  3. Repeated CAPTCHA failure handling (in-band refresh recovery without task crash).
  4. Concurrent worker task claim race conditions (verifying zero double-claiming).
  5. Multi-threaded Database connection concurrency.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from requests.adapters import BaseAdapter
from requests.models import Response

from ecourts.core.captcha import MockCaptchaSolver
from ecourts.core.client import PacedSession
from ecourts.parser.case_details import parse_case_detail
from ecourts.parser.hierarchy import (
    complex_code_numeric,
    split_case_type_token,
    split_complex_token,
)
from ecourts.parser.search import parse_case_results
from ecourts.queue.worker import QueueWorker
from ecourts.storage.db import Database


class TimestampRecordingAdapter(BaseAdapter):
    """Adapter that records high-resolution monotonic timestamps when requests are sent."""

    def __init__(self, response_text: str = "{}", simulate_latency: float = 0.0):
        super().__init__()
        self.response_text = response_text
        self.simulate_latency = simulate_latency
        self.send_start_times: List[float] = []
        self.send_end_times: List[float] = []
        self._lock = threading.Lock()

    def send(self, request, **kwargs):
        t_start = time.monotonic()
        with self._lock:
            self.send_start_times.append(t_start)

        if self.simulate_latency > 0:
            time.sleep(self.simulate_latency)

        t_end = time.monotonic()
        with self._lock:
            self.send_end_times.append(t_end)

        resp = Response()
        resp.status_code = 200
        resp.url = request.url
        resp.request = request
        resp._content = self.response_text.encode("utf-8")
        resp.headers["Content-Type"] = "application/json; charset=UTF-8"
        return resp

    def close(self):
        pass


# ==============================================================================
# 1. HTML PARSING ADVERSARIAL STRESS TESTS
# ==============================================================================


class TestStressHtmlParsing(unittest.TestCase):
    """Adversarial stress tests for HTML parsers under extreme and corrupted inputs."""

    def test_massive_party_roster_parsing(self):
        """Stress test: 1,000 petitioners and 1,000 respondents in roster tables.
        Verifies parser handles massive DOM without quadratic slowdown or memory crash.
        """
        pet_items = "".join(
            f"<li>{i}) PETITIONER CORP {i}<br/>Advocate- COUNSEL_{i} (Bar# {i*7})<br/></li>"
            for i in range(1, 1001)
        )
        resp_items = "".join(
            f"<li>{i}) RESPONDENT ORG {i}<br/>Advocate - ADVOCATE_{i} & ASSOCIATES<br/></li>"
            for i in range(1, 1001)
        )
        html = f"""
        <html>
        <body>
            <table class="case_details_table">
                <tr><th>CNR Number</th><td>KABC019999992023</td></tr>
            </table>
            <ul class="petitioner-advocate-list">{pet_items}</ul>
            <ul class="respondent-advocate-list">{resp_items}</ul>
        </body>
        </html>
        """
        t0 = time.monotonic()
        detail = parse_case_detail(html)
        elapsed = time.monotonic() - t0

        self.assertEqual(detail.cnr_number, "KABC019999992023")
        self.assertEqual(len(detail.petitioners), 1000)
        self.assertEqual(len(detail.respondents), 1000)
        self.assertEqual(detail.petitioners[0]["name"], "PETITIONER CORP 1")
        self.assertEqual(detail.petitioners[0]["advocate"], "COUNSEL_1 (Bar# 7)")
        self.assertEqual(detail.respondents[999]["name"], "RESPONDENT ORG 1000")
        self.assertLess(elapsed, 2.5, f"Massive roster parsing took {elapsed:.2f}s (expected < 2.5s)")

    def test_severely_corrupted_html_structure_recovery(self):
        """Stress test: unclosed tags, mismatched nesting, and malformed tables."""
        corrupted_html = (
            "<html><head><title>Corrupted</title></head><body>"
            "<table class='case_details_table'>"
            "<tr><th>CNR Number:</th><td>KABB010098762024 (Scan QR Code)</td></tr>"
            "<tr><th>Case Type:</th><td>OS - ORIGINAL SUIT</td>"
            "</table>"
            "<ul class='petitioner-advocate-list'>"
            "<li>1) Corrupted Name<br>Advocate- Counsel without closing li"
            "<li>2) Valid Party<br/>Advocate- Counsel 2</li>"
            "</ul>"
            "<table id='act_table'>"
            "<tr><td>Single Cell Row</td></tr>"
            "<tr><td>CPC</td><td>Section 9</td><td>Spurious Third Cell</td></tr>"
            "</table>"
            "<table class='history_table'>"
            "<tr><td>Judge</td></tr>"
            "<tr><td>Judge A</td><td>01-01-2024</td><td>02-02-2024</td><td>Hearing Purpose</td></tr>"
            "</table>"
            "</body>"
        )
        detail = parse_case_detail(corrupted_html)
        self.assertEqual(detail.cnr_number, "KABB010098762024")
        self.assertEqual(detail.case_type, "OS - ORIGINAL SUIT")
        self.assertGreaterEqual(len(detail.petitioners), 1)
        self.assertEqual(len(detail.acts), 1)
        self.assertEqual(detail.acts[0]["act"], "CPC")
        self.assertEqual(len(detail.hearings), 1)
        self.assertEqual(detail.hearings[0]["purpose"], "Hearing Purpose")

    def test_search_results_with_truncated_and_missing_cells(self):
        """Stress test: search table with empty rows, missing onclick attributes, and broken cells."""
        html = """
        <table class="table-fixed">
            <tr><th colspan="3">HONBLE COURT OF SESSIONS</th></tr>
            <tr></tr>
            <tr><td>1</td></tr>
            <tr><td>2</td><td>OS/1/2024</td><td>Party A Vs Party B</td><td></td></tr>
            <tr><td>3</td><td>OS/2/2024</td><td>Alpha Vs Beta</td><td><a href="#" onclick="brokenFunction()">View</a></td></tr>
            <tr><td>4</td><td>OS/3/2024</td><td>Gamma Vs Delta</td><td><a href="#" onclick="viewHistory(123,'KABC010011222024',1,'N','CScaseNumber',3,1,1030136,'CScaseType')">View</a></td></tr>
        </table>
        """
        results = parse_case_results({"case_data": html})
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["case_number"], "OS/1/2024")
        self.assertEqual(results[0]["petitioner"], "Party A")
        self.assertEqual(results[0]["respondent"], "Party B")
        self.assertIsNone(results[0]["view_params"])

        self.assertEqual(results[2]["cnr_number"], "KABC010011222024")
        self.assertIsNotNone(results[2]["view_params"])
        self.assertEqual(results[2]["view_params"]["cino"], "KABC010011222024")

    def test_hierarchy_tokens_extreme_malformed(self):
        """Stress test: compound tokens with excessive delimiters, missing tokens, and spaces."""
        self.assertEqual(complex_code_numeric("1030136@1,2,3@Y"), "1030136")
        self.assertEqual(complex_code_numeric("99999"), "99999")
        self.assertEqual(complex_code_numeric(""), "")
        self.assertEqual(complex_code_numeric("@@@"), "")

        c_code, ests, flag = split_complex_token("@@@")
        self.assertEqual(c_code, "")
        self.assertEqual(ests, [])

        c_code, ests, flag = split_complex_token("12345@10,  11 , , 12@N")
        self.assertEqual(c_code, "12345")
        self.assertEqual(ests, ["10", "11", "12"])
        self.assertEqual(flag, "N")

        type_code, est_code = split_case_type_token("^^^")
        self.assertEqual(type_code, "")
        self.assertEqual(est_code, "")


# ==============================================================================
# 2. ADVERSARIAL BUG FINDINGS (SPECIFICATION VIOLATIONS)
# ==============================================================================


class TestAdversarialBugs(unittest.TestCase):
    """Specific tests exposing and reproducing confirmed architectural bugs."""

    def test_bug_search_party_splitter_corrupts_names_with_substring_vs(self):
        """BUG REPORT: In ecourts/parser/search.py (lines 186-194),
        parse_case_results splits on any occurrence of 'vs' without word boundary checks.
        Legitimate company names or individual names containing 'vs' (e.g., 'Shivsagar Enterprises',
        'John Devs and Sons', 'Elvis Presley') are erroneously split across petitioner and respondent.
        """
        html = """
        <table class="table-fixed">
            <tr><td>1</td><td>OS/101/2024</td><td>Shivsagar Enterprises</td><td></td></tr>
        </table>
        """
        results = parse_case_results({"case_data": html})
        self.assertEqual(len(results), 1)

        # Expected correct behavior: Whole name is petitioner, respondent is empty.
        # Actual buggy behavior: 'Shi' is petitioner, 'agar Enterprises' is respondent!
        self.assertEqual(
            results[0]["petitioner"],
            "Shivsagar Enterprises",
            f"Party name was incorrectly split on substring 'vs'! Got: {results[0]}",
        )
        self.assertEqual(results[0]["respondent"], "")

    def test_bug_case_detail_fallback_cnr_extraction_word_boundary_failure(self):
        """BUG REPORT: In ecourts/parser/case_details.py (lines 208-214),
        when case_details_table class is missing, fallback iterates over rows using
        `text = row.get_text()` without separator.
        Cells like <th>CNR Number</th><td>KABB010098762024</td> become 'CNR NumberKABB010098762024'.
        The regex CNR_REGEX uses '\\b' (word boundary), which fails because 'Number' is alphanumeric.
        """
        html = """
        <html><body>
            <table>
                <tr><th>CNR Number</th><td>KABB010098762024 (Scan QR Code)</td></tr>
                <tr><th>Case Type</th><td>Civil Suit</td></tr>
            </table>
        </body></html>
        """
        detail = parse_case_detail(html)

        # Expected: CNR extracted successfully via fallback row search
        # Actual: Returns empty string '' due to lack of whitespace separator in row.get_text()
        self.assertEqual(
            detail.cnr_number,
            "KABB010098762024",
            f"Fallback CNR extraction failed due to missing whitespace separator in row.get_text(): got '{detail.cnr_number}'",
        )

    def test_bug_concurrent_requests_pacing_burst_violation(self):
        """BUG REPORT: In ecourts/core/client.py (lines 70-88),
        PacedSession._enforce_pacing() only sleeps based on `self._last_request_time`,
        which is only updated AFTER an HTTP request completes (in _mark_request_complete).
        Under multi-threaded concurrency, when the first thread completes its sleep,
        all subsequent waiting threads wake up, observe elapsed >= min_delay, and fire
        simultaneously in a burst (interval < 0.001s).
        This directly violates R5 (minimum 2.0s delay guardrails to prevent burst requests).
        """
        min_delay = 0.15  # 150 ms minimum delay guardrail
        adapter = TimestampRecordingAdapter(response_text='{"status": 1}', simulate_latency=0.01)
        session = PacedSession(min_delay=min_delay)
        session.mount("https://", adapter)

        # Warmup initial request
        session.get("https://services.ecourts.gov.in/ecourtindia_v6/?p=warmup")

        num_threads = 4
        barrier = threading.Barrier(num_threads)

        def thread_target(idx: int):
            barrier.wait()
            session.get(f"https://services.ecourts.gov.in/ecourtindia_v6/?p=endpoint_{idx}")

        threads = [threading.Thread(target=thread_target, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        start_times = sorted(adapter.send_start_times)
        session.close()

        self.assertEqual(len(start_times), 5)
        intervals = [start_times[i] - start_times[i - 1] for i in range(1, len(start_times))]
        min_observed_interval = min(intervals)

        # Print empirical measurement for forensic documentation
        print(f"\n[EMPIRICAL EVIDENCE] PacedSession concurrency intervals: {[f'{x:.4f}s' for x in intervals]}")
        print(f"[EMPIRICAL EVIDENCE] Minimum start interval: {min_observed_interval:.6f}s (Configured: {min_delay:.4f}s)")

        # Required: Every consecutive request must maintain >= min_delay (with 25% tolerance for scheduling jitter)
        # Actual: Burst interval is ~0.0001s (sub-millisecond burst), failing assertion
        self.assertGreaterEqual(
            min_observed_interval,
            min_delay * 0.75,
            f"Concurrency burst violation: minimum interval was {min_observed_interval:.4f}s, expected >= {min_delay * 0.75:.4f}s",
        )

    def test_bug_shared_database_instance_thread_safety(self):
        """BUG REPORT: In ecourts/storage/db.py,
        Database initializes sqlite3 with check_same_thread=False but does not protect
        its connection/cursor transactions with a threading.Lock.
        When multiple worker threads share a single Database instance, concurrent
        claim_next_task or save_case calls crash with:
        sqlite3.OperationalError: cannot commit transaction - SQL statements in progress
        or sqlite3.InterfaceError: bad parameter or other API misuse.
        """
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "shared_db_test.db")
        shared_db = Database(db_path)

        for i in range(30):
            shared_db.enqueue_task("task", {"idx": i})

        errors: List[Exception] = []
        lock = threading.Lock()

        def worker_loop():
            try:
                for _ in range(6):
                    shared_db.claim_next_task("worker_shared")
                    time.sleep(0.002)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker_loop) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        shared_db.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Expected: A thread-safe Database component should either support multi-threading cleanly or use an internal Lock.
        # Actual: Raises OperationalError or InterfaceError on concurrent access.
        self.assertEqual(
            len(errors),
            0,
            f"Shared Database instance failed under concurrency with errors: {errors}",
        )


# ==============================================================================
# 3. TASK QUEUE & WORKER CONCURRENCY (MULTI-CONNECTION / MULTI-PROCESS SAFE)
# ==============================================================================


class TestConcurrentTaskClaim(unittest.TestCase):
    """Adversarial stress tests for concurrent task claiming across separate DB connections."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "stress_queue.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_heavy_concurrent_worker_claims_zero_duplicates(self):
        """Stress test: 20 concurrent worker threads (each with its own Database connection)
        claiming from 100 queued tasks.
        Verifies:
          1. Exactly 100 tasks claimed.
          2. Exactly 100 unique IDs (zero double-claiming).
          3. Final DB status: 100 completed, 0 queued, 0 running, 0 failed.
          4. SQLite PRAGMA integrity_check passes.
        """
        master_db = Database(self.db_path)
        total_tasks = 100
        for i in range(total_tasks):
            master_db.enqueue_task("stress_task", {"task_index": i}, priority=i % 5)
        master_db.close()

        claimed_task_ids: List[int] = []
        lock = threading.Lock()
        worker_count = 20

        def worker_loop(w_idx: int):
            db = Database(self.db_path)
            while True:
                task = db.claim_next_task(f"worker_{w_idx}")
                if not task:
                    break
                with lock:
                    claimed_task_ids.append(task["id"])
                time.sleep(0.004)
                db.complete_task(task["id"], results_count=1)
            db.close()

        threads = [threading.Thread(target=worker_loop, args=(i,)) for i in range(worker_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(claimed_task_ids), total_tasks)
        self.assertEqual(len(set(claimed_task_ids)), total_tasks, "Double-claiming detected!")

        verify_db = Database(self.db_path)
        stats = verify_db.get_queue_stats()
        self.assertEqual(stats["completed"], total_tasks)
        self.assertEqual(stats["queued"], 0)
        self.assertEqual(stats["running"], 0)
        self.assertEqual(stats["failed"], 0)

        cur = verify_db.conn.execute("PRAGMA integrity_check;")
        self.assertEqual(cur.fetchone()[0], "ok")
        verify_db.close()


# ==============================================================================
# 4. CAPTCHA RESILIENCE & IN-BAND RECOVERY TESTS
# ==============================================================================


class TestCaptchaFailureResilience(unittest.TestCase):
    """Adversarial stress tests for repeated CAPTCHA failures and in-band recovery."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "captcha_test.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inband_captcha_recovery_success_on_attempt_2(self):
        """Verify task succeeds when Attempt 1 gets WrongCaptchaError and in-band refresh succeeds on Attempt 2."""
        session = PacedSession(min_delay=0.0)

        rejection_response = {
            "errormsg": "Invalid Captcha...",
            "div_captcha": '<div id="div_captcha"><img id="captcha_image" src="vendor/securimage/securimage_show.php?ref=2"/></div>'
        }
        success_response = {
            "case_data": """
            <table class="table-fixed">
                <tr><th colspan="3">CITY CIVIL COURT</th></tr>
                <tr><td>1</td><td>OS/100/2024</td><td>Petitioner Vs Respondent</td>
                <td><a href="#" onclick="viewHistory(1,'KABC010099992024',1,'N','CScaseNumber',3,1,1030136,'CScaseType')">View</a></td></tr>
            </table>
            """
        }

        call_counts = {"submit": 0}

        def mock_post(url, data=None, **kwargs):
            resp = Response()
            resp.status_code = 200
            if "getCaptcha" in url:
                resp._content = b'{"div_captcha": "<img id=\\"captcha_image\\" src=\\"vendor/securimage/securimage_show.php?ref=1\\"/>"}'
            elif "submit_case_type" in url:
                call_counts["submit"] += 1
                if call_counts["submit"] == 1:
                    resp._content = json.dumps(rejection_response).encode("utf-8")
                else:
                    resp._content = json.dumps(success_response).encode("utf-8")
            elif "viewHistory" in url:
                resp._content = b'{"data_list": "<table class=\\"case_details_table\\"><tr><th>CNR Number</th><td>KABC010099992024</td></tr></table>"}'
            else:
                resp._content = b'{"status": 1}'
            resp.headers["Content-Type"] = "application/json"
            return resp

        def mock_get_image(url, **kwargs):
            return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

        session.post = MagicMock(side_effect=mock_post)
        session.get_image = MagicMock(side_effect=mock_get_image)

        solver = MockCaptchaSolver(sequence=["wrong_code", "correct_code"])
        worker = QueueWorker(
            db=self.db,
            session=session,
            solver=solver,
            max_captcha_retries=3,
        )

        task_id = self.db.enqueue_task("case_type_search", {
            "state_code": "3",
            "dist_code": "1",
            "court_complex_code": "1030136@1@Y",
            "est_code": "1",
            "case_type_value": "1",
            "search_year": "2024",
            "fetch_details": False,
        })

        processed = worker.process_one_task()
        self.assertTrue(processed)
        self.assertEqual(call_counts["submit"], 2, "Should have submitted search twice (1 fail + 1 retry)")

        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["results_count"], 1)

        case = self.db.get_case_by_cnr("KABC010099992024")
        self.assertIsNotNone(case)

    def test_captcha_exhaustion_marks_task_retryable_without_crash(self):
        """Verify that when all CAPTCHA retries are exhausted, the worker does NOT crash,
        and marks the task for retry with exponential backoff.
        """
        session = PacedSession(min_delay=0.0)
        rejection_response = {
            "errormsg": "Invalid Captcha...",
            "div_captcha": '<div id="div_captcha"><img id="captcha_image" src="vendor/securimage/securimage_show.php?ref=2"/></div>'
        }

        resp = Response()
        resp.status_code = 200
        resp._content = json.dumps(rejection_response).encode("utf-8")
        resp.headers["Content-Type"] = "application/json"

        session.post = MagicMock(return_value=resp)
        session.get_image = MagicMock(return_value=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

        solver = MockCaptchaSolver(default_solution="always_wrong")
        worker = QueueWorker(
            db=self.db,
            session=session,
            solver=solver,
            max_captcha_retries=2,
        )

        task_id = self.db.enqueue_task("case_type_search", {
            "state_code": "3",
            "dist_code": "1",
            "court_complex_code": "1030136@1@Y",
            "est_code": "1",
            "case_type_value": "1",
            "search_year": "2024",
        }, max_retries=3)

        processed = worker.process_one_task()
        self.assertTrue(processed, "process_one_task should return True even on task failure")

        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["retry_count"], 1)
        self.assertIn("CAPTCHA rejected", task["error_message"])


if __name__ == "__main__":
    unittest.main()
