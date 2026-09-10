"""
tests.test_challenger3_reverification
=====================================
Adversarial Stress Re-Verification Test Suite by Challenger 3.
Author: Challenger 3 (Adversarial Stress Re-Verifier)

Scope:
  1. Multi-threaded PacedSession pacing reservation lock:
     - High concurrent thread contention (10 threads, barrier synchronized)
     - Variable request latency (latency > min_delay)
     - Reservation schedule immunity to request duration
  2. Word-bounded regex party splitting in search results:
     - Battery of Indian legal corporate & personal names containing 'vs' substring
     - Multiple delimiters, malformed tags, edge case whitespace
  3. Fallback CNR extraction:
     - Bare HTML structures without CSS classes
     - Loose CNR extraction resilience
     - Edge case labels and embedded text
  4. Shared Database connection thread safety:
     - High concurrency with mixed transactions (enqueue, claim, complete, save, query)
     - SQLite database integrity check after concurrent contention
"""

from __future__ import annotations

import os
import random
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional

from requests.adapters import BaseAdapter
from requests.models import Response

from ecourts.core.client import PacedSession
from ecourts.parser.case_details import CaseDetail, extract_cnr, parse_case_detail
from ecourts.parser.search import parse_case_results
from ecourts.storage.db import Database


class TimestampRecordingAdapter(BaseAdapter):
    """Records precise monotonic start and finish times for every HTTP dispatch."""

    def __init__(self, response_text: str = '{"status": 1}', min_latency: float = 0.0, max_latency: float = 0.0):
        super().__init__()
        self.response_text = response_text
        self.min_latency = min_latency
        self.max_latency = max_latency
        self.send_start_times: List[float] = []
        self.send_end_times: List[float] = []
        self._lock = threading.Lock()

    def send(self, request, **kwargs):
        t_start = time.monotonic()
        with self._lock:
            self.send_start_times.append(t_start)

        latency = random.uniform(self.min_latency, self.max_latency) if self.max_latency > self.min_latency else self.min_latency
        if latency > 0:
            time.sleep(latency)

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
# 1. PACED SESSION CONCURRENCY ADVERSARIAL STRESS
# ==============================================================================


class TestPacedSessionAdversarialStress(unittest.TestCase):
    """Stress-tests PacedSession pacing reservation lock under extreme concurrency."""

    def test_heavy_concurrent_threads_pacing_intervals(self):
        """Stress: 10 threads synchronized with Barrier hitting PacedSession simultaneously.
        Verifies that every pair of dispatched requests maintains >= min_delay * 0.75.
        """
        min_delay = 0.06  # 60ms
        adapter = TimestampRecordingAdapter(min_latency=0.005, max_latency=0.015)
        session = PacedSession(min_delay=min_delay)
        session.mount("https://", adapter)

        num_threads = 10
        barrier = threading.Barrier(num_threads)

        def worker(thread_id: int):
            barrier.wait()
            session.get(f"https://services.ecourts.gov.in/ecourtindia_v6/?p=test_{thread_id}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        start_times = sorted(adapter.send_start_times)
        session.close()

        self.assertEqual(len(start_times), num_threads)
        intervals = [start_times[i] - start_times[i - 1] for i in range(1, len(start_times))]
        min_interval = min(intervals)

        print(f"\n[CHALLENGER-3 EMPIRICAL] 10-Thread Concurrency Intervals: {[f'{x:.4f}s' for x in intervals]}")
        print(f"[CHALLENGER-3 EMPIRICAL] Min Interval: {min_interval:.6f}s (Configured: {min_delay:.4f}s)")

        self.assertGreaterEqual(
            min_interval,
            min_delay * 0.75,
            f"Concurrency burst violation under 10 threads: min interval was {min_interval:.4f}s, expected >= {min_delay * 0.75:.4f}s",
        )

    def test_variable_latency_does_not_collapse_reservation_schedule(self):
        """Stress: simulated network latency (20ms-50ms) exceeds or rivals min_delay (30ms).
        Verifies that even if requests take longer than min_delay, subsequent dispatches
        do NOT burst upon completion of the slow request.
        """
        min_delay = 0.04  # 40ms
        adapter = TimestampRecordingAdapter(min_latency=0.02, max_latency=0.06)
        session = PacedSession(min_delay=min_delay)
        session.mount("https://", adapter)

        num_threads = 6
        barrier = threading.Barrier(num_threads)

        def worker(thread_id: int):
            barrier.wait()
            session.post("https://services.ecourts.gov.in/ecourtindia_v6/?p=post_test", data={"id": thread_id})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        start_times = sorted(adapter.send_start_times)
        session.close()

        self.assertEqual(len(start_times), num_threads)
        intervals = [start_times[i] - start_times[i - 1] for i in range(1, len(start_times))]
        min_interval = min(intervals)

        print(f"\n[CHALLENGER-3 EMPIRICAL] Variable Latency Intervals: {[f'{x:.4f}s' for x in intervals]}")
        print(f"[CHALLENGER-3 EMPIRICAL] Min Interval: {min_interval:.6f}s (Configured: {min_delay:.4f}s)")

        self.assertGreaterEqual(
            min_interval,
            min_delay * 0.75,
            f"Variable latency burst violation: min interval was {min_interval:.4f}s, expected >= {min_delay * 0.75:.4f}s",
        )


# ==============================================================================
# 2. SEARCH PARTY SPLITTING ADVERSARIAL STRESS
# ==============================================================================


class TestSearchPartySplittingAdversarialStress(unittest.TestCase):
    """Stress-tests search result party parsing against corporate and personal names."""

    def test_battery_of_party_names_with_vs_substrings(self):
        """Test a comprehensive battery of entities containing 'vs' substring to ensure zero false splits."""
        entities = [
            "Shivsagar Enterprises",
            "Shivshankar Prasad",
            "John Devs and Sons Pvt Ltd",
            "Elvis Presley Memorial Foundation",
            "Sivakumar & Co",
            "Universal Services Group",
            "Visvesvaraya Technological Institute",
            "Advsar Legal Associates",
            "Devsena Devi",
            "Govs Engineering Works",
            "K. V. S. Associates",
            "Pravs Infotech",
            "Sarvshreshtha Logistics",
        ]

        for entity in entities:
            html = f"""
            <table class="table-fixed">
                <tr><td>1</td><td>CC/100/2024</td><td>{entity}</td><td></td></tr>
            </table>
            """
            results = parse_case_results({"case_data": html})
            self.assertEqual(len(results), 1, f"Failed to parse row for entity: {entity}")
            self.assertEqual(
                results[0]["petitioner"],
                entity,
                f"Entity '{entity}' was erroneously split into petitioner '{results[0]['petitioner']}' and respondent '{results[0]['respondent']}'!",
            )
            self.assertEqual(results[0]["respondent"], "")

    def test_legitimate_versus_variations(self):
        """Test legitimate legal 'vs' variations to ensure valid splitting."""
        test_cases = [
            ("Ramesh Sharma Vs Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma vs Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma VS Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma Versus Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma versus Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma v/s Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma V/S Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma vs. Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("Ramesh Sharma VS. Sita Sharma", "Ramesh Sharma", "Sita Sharma"),
            ("RAMESH SHARMA<br>Vs</br>SITA SHARMA", "RAMESH SHARMA", "SITA SHARMA"),
            ("RAMESH SHARMA<br/>vs<br/>SITA SHARMA", "RAMESH SHARMA", "SITA SHARMA"),
            ("John Devs and Sons vs. Shivsagar Enterprises", "John Devs and Sons", "Shivsagar Enterprises"),
        ]

        for raw_party, exp_pet, exp_resp in test_cases:
            html = f"""
            <table class="table-fixed">
                <tr><td>1</td><td>CC/200/2024</td><td>{raw_party}</td><td></td></tr>
            </table>
            """
            results = parse_case_results({"case_data": html})
            self.assertEqual(len(results), 1, f"Failed to parse row for: {raw_party}")
            self.assertEqual(
                results[0]["petitioner"],
                exp_pet,
                f"Incorrect petitioner for '{raw_party}': got '{results[0]['petitioner']}', expected '{exp_pet}'",
            )
            self.assertEqual(
                results[0]["respondent"],
                exp_resp,
                f"Incorrect respondent for '{raw_party}': got '{results[0]['respondent']}', expected '{exp_resp}'",
            )


# ==============================================================================
# 3. FALLBACK CNR EXTRACTION ADVERSARIAL STRESS
# ==============================================================================


class TestFallbackCNRExtractionAdversarialStress(unittest.TestCase):
    """Stress-tests CNR extraction under bare and malformed HTML tables."""

    def test_cnr_in_unclassed_table_with_adjacent_elements(self):
        """Verify fallback extracts CNR when table has no class, and th/td are tightly adjacent."""
        html = """
        <html><body>
            <div id="content">
                <table>
                    <tr><th>CNR Number</th><td>KABB010098762024 (Scan QR Code)</td></tr>
                    <tr><th>Case Type</th><td>Civil Suit</td></tr>
                </table>
            </div>
        </body></html>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "KABB010098762024")

    def test_cnr_in_nested_markup_and_whitespace_variations(self):
        """Verify CNR extraction when wrapped in bold, spans, or mixed-case headers."""
        html = """
        <html><body>
            <table>
                <tr>
                    <td><b>cnr number:</b></td>
                    <td><span>DLHC010055552022</span></td>
                </tr>
            </table>
        </body></html>
        """
        detail = parse_case_detail(html)
        self.assertEqual(detail.cnr_number, "DLHC010055552022")

    def test_extract_cnr_utility_loose_pattern(self):
        """Verify extract_cnr standalone function catches 16-char CNR preceded by arbitrary characters."""
        self.assertEqual(extract_cnr("CNRNumberKABC010099992024"), "KABC010099992024")
        self.assertEqual(extract_cnr("CaseDetails:MHPU010012342023End"), "MHPU010012342023")
        self.assertIsNone(extract_cnr("INVALID_CNR_12345"))
        self.assertIsNone(extract_cnr(""))


# ==============================================================================
# 4. SHARED DATABASE CONNECTION CONCURRENCY ADVERSARIAL STRESS
# ==============================================================================


class TestSharedDatabaseConnectionAdversarialStress(unittest.TestCase):
    """Stress-tests a single shared Database instance under concurrent mixed operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "shared_db_stress.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_heavy_mixed_transactions_on_single_db_instance(self):
        """Stress: 10 concurrent threads executing 20 operations each on a single Database instance.
        Mix of: enqueue_task, claim_next_task, complete_task, save_case, get_case_by_cnr, list_cases.
        Assert 0 SQLite errors and PRAGMA integrity_check == 'ok'.
        """
        errors: List[Exception] = []
        errors_lock = threading.Lock()

        # Seed initial tasks and cases
        for i in range(20):
            self.db.enqueue_task("seed_task", {"index": i})

        def worker(thread_id: int):
            try:
                for op_idx in range(15):
                    # 1. Enqueue task
                    t_id = self.db.enqueue_task(f"task_t{thread_id}", {"op": op_idx})

                    # 2. Claim task
                    claimed = self.db.claim_next_task(f"worker_{thread_id}")
                    if claimed:
                        time.sleep(0.001)
                        self.db.complete_task(claimed["id"], results_count=op_idx)

                    # 3. Save case
                    cnr = f"KABB{thread_id:02d}{op_idx:06d}2024"
                    case = CaseDetail(
                        cnr_number=cnr,
                        case_type="Civil Suit",
                        parties=[
                            {"type": "petitioner", "name": f"Petitioner_{thread_id}_{op_idx}", "advocate": "Adv A"},
                            {"type": "respondent", "name": f"Respondent_{thread_id}_{op_idx}", "advocate": "Adv B"},
                        ],
                    )
                    self.db.save_case(case)

                    # 4. Read case
                    retrieved = self.db.get_case_by_cnr(cnr)
                    if not retrieved or retrieved["cnr_number"] != cnr:
                        raise ValueError(f"Failed to retrieve case {cnr}")

                    # 5. List cases
                    cases_list = self.db.list_cases(limit=5)
                    self.assertGreater(len(cases_list), 0)

            except Exception as exc:
                with errors_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent database stress failed with errors: {errors}")

        # Check database integrity
        cur = self.db.conn.execute("PRAGMA integrity_check;")
        integrity = cur.fetchone()[0]
        self.assertEqual(integrity, "ok", f"Database corrupted after concurrent stress: {integrity}")

        stats = self.db.get_queue_stats()
        print(f"\n[CHALLENGER-3 EMPIRICAL] Shared Database Stress Queue Stats: {stats}")
        cases_count = self.db.get_cases_count()
        print(f"[CHALLENGER-3 EMPIRICAL] Total Cases Persisted: {cases_count}")
        self.assertEqual(cases_count, 150)  # 10 threads * 15 unique CNRs


if __name__ == "__main__":
    unittest.main()
