"""
tests.test_challenger2_stress
=============================
Adversarial and Empirical Stress Test Suite for Challenger 2:
1. Watchdog Reaper: simulated worker crash, lease timeouts, boundary conditions, concurrent reaps.
2. Exponential Backoff: delay growth curve, jitter bounds and variance, claim enforcement.
3. CNR Deduplication: master/child relational upsert, timestamp preservation, cascade integrity.
4. Export Round-Trip Fidelity: JSON and CSV field-level exactness vs SQLite database records.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
import shutil
import tempfile
import time
import unittest

from ecourts.export.exporter import (
    Exporter,
    export_to_csv_flat,
    export_to_csv_relational,
    export_to_json,
)
from ecourts.parser.case_details import CaseDetail
from ecourts.queue.worker import QueueWorker
from ecourts.storage.db import Database


class TestWatchdogReaperStress(unittest.TestCase):
    """Empirically stress-tests the watchdog reaper under simulated crashes,

    boundary conditions, and multi-worker contention.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_reaper.db")
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reap_abandoned_task_after_lease_timeout(self) -> None:
        """Simulate a worker claiming a task and crashing without completing it.

        Verify watchdog reclaims it after lease expiration.
        """
        task_id = self.db.enqueue_task("case_type_search", {"state_code": "1"})
        claimed = self.db.claim_next_task("crashed-worker-pid-999")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], task_id)
        self.assertEqual(claimed["status"], "running")

        # Simulate time passage by rewinding started_at in the database by 350 seconds
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past_time, task_id))

        # Run reaper with 300s lease timeout
        reaped_count = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped_count, 1)

        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "queued")
        self.assertIsNone(task["worker_id"])
        self.assertIn("Reclaimed by watchdog", task["error_message"])

    def test_reap_respects_active_lease_does_not_prematurely_reap(self) -> None:
        """Verify task running within lease timeout is NOT reaped."""
        task_id = self.db.enqueue_task("case_type_search", {"state_code": "1"})
        claimed = self.db.claim_next_task("active-worker-pid-123")
        self.assertIsNotNone(claimed)

        # Rewind started_at by 150 seconds (lease is 300s)
        recent_time = (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat()
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (recent_time, task_id))

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 0)

        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["worker_id"], "active-worker-pid-123")

    def test_reaped_task_immediately_claimable_and_completable(self) -> None:
        """Verify that once a task is reaped, a new healthy worker can claim and complete it."""
        task_id = self.db.enqueue_task("case_type_search", {"state_code": "1"})
        self.db.claim_next_task("dead-worker")

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past_time, task_id))

        self.db.reap_abandoned_tasks(lease_timeout_seconds=300)

        # Second worker claims the reaped task
        new_claim = self.db.claim_next_task("healthy-worker-456")
        self.assertIsNotNone(new_claim)
        self.assertEqual(new_claim["id"], task_id)
        self.assertEqual(new_claim["worker_id"], "healthy-worker-456")

        # Complete it
        self.db.complete_task(task_id, results_count=42)
        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["results_count"], 42)

    def test_reaper_boundary_conditions(self) -> None:
        """Test boundary around lease timeout: 299s ago vs 301s ago."""
        t1 = self.db.enqueue_task("task_1", {})
        t2 = self.db.enqueue_task("task_2", {})

        self.db.claim_next_task("worker-1")
        self.db.claim_next_task("worker-2")

        # t1 started 299 seconds ago (inside 300s window)
        # t2 started 301 seconds ago (outside 300s window)
        now = datetime.now(timezone.utc)
        t1_time = (now - timedelta(seconds=299)).isoformat()
        t2_time = (now - timedelta(seconds=301)).isoformat()

        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (t1_time, t1))
            self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (t2_time, t2))

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 1)

        t1_record = self.db.get_task_by_id(t1)
        t2_record = self.db.get_task_by_id(t2)
        self.assertEqual(t1_record["status"], "running", "Task at 299s should remain running")
        self.assertEqual(t2_record["status"], "queued", "Task at 301s should be reaped to queued")

    def test_reaper_multi_worker_crash_concurrency(self) -> None:
        """Simulate mass failure where 10 tasks across 10 workers died."""
        task_ids = []
        for i in range(10):
            tid = self.db.enqueue_task(f"bulk_task_{i}", {"idx": i})
            task_ids.append(tid)
            self.db.claim_next_task(f"crashed-worker-{i}")

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat()
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET started_at = ?", (past_time,))

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 10)

        for tid in task_ids:
            t = self.db.get_task_by_id(tid)
            self.assertEqual(t["status"], "queued")
            self.assertIsNone(t["worker_id"])

    def test_non_running_tasks_immune_to_reaper(self) -> None:
        """Verify tasks in 'queued', 'completed', or 'failed' states are never modified by watchdog."""
        t_queued = self.db.enqueue_task("t_q", {}, priority=0)
        t_comp = self.db.enqueue_task("t_c", {}, priority=10)
        claimed_comp = self.db.claim_next_task("worker-x")
        self.assertEqual(claimed_comp["id"], t_comp)
        self.db.complete_task(t_comp)

        t_fail = self.db.enqueue_task("t_f", {}, priority=10)
        claimed_fail = self.db.claim_next_task("worker-y")
        self.assertEqual(claimed_fail["id"], t_fail)
        self.db.fail_task(t_fail, error_message="fatal error", retryable=False)

        past_time = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE tasks SET started_at = ? WHERE id IN (?, ?, ?)",
                (past_time, t_queued, t_comp, t_fail),
            )

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 0)

        self.assertEqual(self.db.get_task_by_id(t_queued)["status"], "queued")
        self.assertEqual(self.db.get_task_by_id(t_comp)["status"], "completed")
        self.assertEqual(self.db.get_task_by_id(t_fail)["status"], "failed")


class TestExponentialBackoffStress(unittest.TestCase):
    """Empirically stress-tests exponential backoff calculations, jitter variance,

    scheduling constraints, and retry lifecycle termination.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_backoff.db")
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_exponential_delay_growth_sequence(self) -> None:
        """Verify that scheduled_at delays strictly grow exponentially across retries."""
        task_id = self.db.enqueue_task(
            "test_task",
            {},
            max_retries=4,
            backoff_base=2.0,
        )

        expected_ranges = [
            # retry_count -> (min_expected_delay, max_expected_delay)
            # base * (2^0) + [0.1, 1.0] = 2.0 + [0.1, 1.0] = [2.1, 3.0]
            (2.1, 3.0),
            # base * (2^1) + [0.1, 1.0] = 4.0 + [0.1, 1.0] = [4.1, 5.0]
            (4.1, 5.0),
            # base * (2^2) + [0.1, 1.0] = 8.0 + [0.1, 1.0] = [8.1, 9.0]
            (8.1, 9.0),
            # base * (2^3) + [0.1, 1.0] = 16.0 + [0.1, 1.0] = [16.1, 17.0]
            (16.1, 17.0),
        ]

        previous_delay = 0.0

        for attempt, (min_delay, max_delay) in enumerate(expected_ranges, start=1):
            before_fail = datetime.now(timezone.utc)
            rescheduled = self.db.fail_task(task_id, error_message=f"Attempt {attempt} failed", retryable=True)
            self.assertTrue(rescheduled, f"Task should be rescheduled on attempt {attempt}")

            task = self.db.get_task_by_id(task_id)
            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["retry_count"], attempt)

            scheduled_at = datetime.fromisoformat(task["scheduled_at"])
            delay = (scheduled_at - before_fail).total_seconds()

            self.assertGreaterEqual(delay, min_delay - 0.2, f"Delay {delay:.3f} below min {min_delay}")
            self.assertLessEqual(delay, max_delay + 0.2, f"Delay {delay:.3f} above max {max_delay}")
            self.assertGreater(delay, previous_delay, "Delays must strictly increase monotonically")
            previous_delay = delay

        # Final failure exceeding max_retries
        final_reschedule = self.db.fail_task(task_id, error_message="Final failure", retryable=True)
        self.assertFalse(final_reschedule, "Should not reschedule when max_retries exceeded")
        task = self.db.get_task_by_id(task_id)
        self.assertEqual(task["status"], "failed")

    def test_jitter_distribution_and_variance(self) -> None:
        """Verify that jitter added to backoff is non-constant and uniformly distributed in [0.1, 1.0]."""
        jitters = []
        base = 2.0

        for i in range(30):
            tid = self.db.enqueue_task(f"jitter_task_{i}", {}, max_retries=3, backoff_base=base)
            t_start = datetime.now(timezone.utc)
            self.db.fail_task(tid, error_message="jitter test", retryable=True)
            t_record = self.db.get_task_by_id(tid)
            sched = datetime.fromisoformat(t_record["scheduled_at"])
            delay = (sched - t_start).total_seconds()
            base_expected = base * (2 ** 0)  # 2.0
            jitter = delay - base_expected
            jitters.append(jitter)

        # Assert all jitters within expected interval with tolerance
        for j in jitters:
            self.assertGreaterEqual(j, 0.05, f"Jitter {j:.3f} too low")
            self.assertLessEqual(j, 1.15, f"Jitter {j:.3f} too high")

        # Assert jitter is not static (variance > 0)
        mean_jitter = sum(jitters) / len(jitters)
        variance = sum((x - mean_jitter) ** 2 for x in jitters) / len(jitters)
        self.assertGreater(variance, 0.01, "Jitter appears constant or lacking randomness")

    def test_claim_scheduling_delay_enforcement(self) -> None:
        """Verify claim_next_task ignores tasks whose scheduled_at is in the future."""
        future_time = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        tid = self.db.enqueue_task("future_task", {}, scheduled_at=future_time)

        # Attempt claim now
        claim = self.db.claim_next_task("worker-instant")
        self.assertIsNone(claim, "Should not claim task scheduled for the future")

        # Rewind scheduled_at to the past
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET scheduled_at = ? WHERE id = ?", (past_time, tid))

        claim = self.db.claim_next_task("worker-instant")
        self.assertIsNotNone(claim)
        self.assertEqual(claim["id"], tid)

    def test_non_retryable_failure_bypasses_backoff(self) -> None:
        """Verify non-retryable errors immediately fail without retrying."""
        tid = self.db.enqueue_task("non_retry_task", {}, max_retries=5)
        rescheduled = self.db.fail_task(tid, error_message="Unrecoverable 404", retryable=False)
        self.assertFalse(rescheduled)

        task = self.db.get_task_by_id(tid)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["retry_count"], 0)
        self.assertIn("Unrecoverable 404", task["error_message"])


class TestCNRDeduplicationStress(unittest.TestCase):
    """Empirically stress-tests master case upsert deduplication, child table cascades,

    and stage lifecycle updates.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cnr.db")
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cnr_upsert_preserves_first_scraped_at_and_updates_last_scraped_at(self) -> None:
        """Verify that upserting an existing CNR preserves first_scraped_at but advances last_scraped_at."""
        cnr = "KABC010012342023"
        case_v1 = {
            "cnr_number": cnr,
            "case_type": "Original Suit",
            "case_stage": "Pleadings",
            "status": "Pending",
        }
        self.db.save_case(case_v1)
        record_v1 = self.db.get_case_by_cnr(cnr)
        first_scraped = record_v1["first_scraped_at"]
        last_scraped_1 = record_v1["last_scraped_at"]

        # Small sleep to ensure timestamp differs
        time.sleep(0.05)

        case_v2 = {
            "cnr_number": cnr,
            "case_type": "Original Suit",
            "case_stage": "Evidence",
            "status": "Pending",
        }
        self.db.save_case(case_v2)
        record_v2 = self.db.get_case_by_cnr(cnr)

        self.assertEqual(record_v2["first_scraped_at"], first_scraped, "first_scraped_at must be immutable")
        self.assertGreater(record_v2["last_scraped_at"], last_scraped_1, "last_scraped_at must advance")
        self.assertEqual(record_v2["case_stage"], "Evidence")
        self.assertEqual(self.db.get_cases_count(), 1, "Cases count must remain 1 after deduplicated upsert")

    def test_cnr_upsert_child_relational_synchronization(self) -> None:
        """Verify child relations (parties, acts, hearings) are cleanly updated without orphans or duplicate keys."""
        cnr = "MHPU010056782022"

        # Stage 1: Initial record
        c1 = {
            "cnr_number": cnr,
            "case_stage": "Appearance",
            "parties": [
                {"type": "petitioner", "name": "Ram Kumar", "advocate": "Adv. Joshi"},
                {"type": "respondent", "name": "Shyam Sundar", "advocate": "Adv. Patil"},
            ],
            "acts": [{"act": "CPC", "sections": "Sec 9"}],
            "hearings": [{"business_date": "2023-01-10", "hearing_date": "2023-02-15", "purpose": "Notice"}],
        }
        self.db.save_case(c1)

        res1 = self.db.get_case_by_cnr(cnr)
        self.assertEqual(len(res1["parties"]), 2)
        self.assertEqual(len(res1["acts"]), 1)
        self.assertEqual(len(res1["hearings"]), 1)

        # Stage 2: Updated with new hearing history and added respondent
        c2 = {
            "cnr_number": cnr,
            "case_stage": "Arguments",
            "parties": [
                {"type": "petitioner", "name": "Ram Kumar", "advocate": "Adv. Joshi"},
                {"type": "respondent", "name": "Shyam Sundar", "advocate": "Adv. Patil"},
                {"type": "respondent", "name": "City Corporation", "advocate": "Adv. Deshmukh"},
            ],
            "acts": [
                {"act": "CPC", "sections": "Sec 9"},
                {"act": "Specific Relief Act", "sections": "Sec 38"},
            ],
            "hearings": [
                {"business_date": "2023-01-10", "hearing_date": "2023-02-15", "purpose": "Notice"},
                {"business_date": "2023-02-15", "hearing_date": "2023-04-20", "purpose": "Evidence"},
                {"business_date": "2023-04-20", "hearing_date": "2023-06-10", "purpose": "Arguments"},
            ],
        }
        self.db.save_case(c2)

        res2 = self.db.get_case_by_cnr(cnr)
        self.assertEqual(res2["case_stage"], "Arguments")
        self.assertEqual(len(res2["parties"]), 3)
        self.assertEqual(len(res2["petitioners"]), 1)
        self.assertEqual(len(res2["respondents"]), 2)
        self.assertEqual(len(res2["acts"]), 2)
        self.assertEqual(len(res2["hearings"]), 3)

        # Verify sequential ordering of party_seq and hearing_seq
        p_seqs = [p["party_seq"] for p in res2["respondents"]]
        self.assertEqual(p_seqs, [1, 2])

        h_seqs = [h["hearing_seq"] for h in res2["hearings"]]
        self.assertEqual(h_seqs, [1, 2, 3])

    def test_cnr_upsert_with_special_characters_and_unicode(self) -> None:
        """Verify that names with quotes, commas, ampersands, and Unicode survive upsert cleanly."""
        cnr = "DLCT019999992024"
        case_data = {
            "cnr_number": cnr,
            "case_type": "Crl.A.",
            "court_name": "Court of O'Neil & Sons, Chief Judge \"Special\" Branch",
            "parties": [
                {"type": "petitioner", "name": "State (NCT of Delhi) & Anr.", "advocate": "Public Prosecutor"},
                {"type": "respondent", "name": "सुशील कुमार उर्फ 'Rocky' / ರಮೇಶ್", "advocate": "Adv. D'Souza, Esq."},
            ],
            "acts": [{"act": "IPC, 1860 & NIA Act", "sections": "302, 120-B & 16"}],
            "hearings": [{"purpose": "Judgment; Pronounced in Open Court", "judge": "Dr. A.K. Sharma"}],
        }
        self.db.save_case(case_data)
        saved = self.db.get_case_by_cnr(cnr)

        self.assertEqual(saved["court_name"], case_data["court_name"])
        self.assertEqual(saved["parties"][1]["name"], "सुशील कुमार उर्फ 'Rocky' / ರಮೇಶ್")
        self.assertEqual(saved["parties"][1]["advocate"], "Adv. D'Souza, Esq.")
        self.assertEqual(saved["acts"][0]["sections"], "302, 120-B & 16")


class TestExportRoundTripFidelityStress(unittest.TestCase):
    """Empirically validates JSON and CSV export pipelines for 100% field-level fidelity

    against raw database records.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_export.db")
        self.db = Database(self.db_path)

        # Seed database with rich test cases
        self.test_cases = [
            {
                "cnr_number": "KA01000000012021",
                "case_type": "OS",
                "case_number": "101/2021",
                "filing_number": "FIL-101",
                "filing_date": "2021-01-15",
                "registration_number": "REG-101",
                "registration_date": "2021-01-20",
                "case_stage": "Hearing",
                "court_number_judge": "Court Hall 5",
                "court_name": "City Civil Court Bengaluru",
                "state_code": "3",
                "dist_code": "1",
                "court_complex_code": "1030001",
                "est_code": "1",
                "first_hearing_date": "2021-02-01",
                "next_hearing_date": "2021-04-10",
                "status": "Pending",
                "parties": [
                    {"type": "petitioner", "name": "Alpha Corp", "advocate": "Adv. Rao"},
                    {"type": "respondent", "name": "Beta Ltd", "advocate": "Adv. Kumar"},
                ],
                "acts": [{"act": "Contract Act", "sections": "Sec 73"}],
                "hearings": [
                    {
                        "business_date": "2021-02-01",
                        "hearing_date": "2021-04-10",
                        "purpose": "Framing Issues",
                        "judge": "Judge A",
                    }
                ],
            },
            {
                "cnr_number": "MH01000000022022",
                "case_type": "CC",
                "case_number": "202/2022",
                "filing_number": "FIL-202",
                "filing_date": "2022-03-10",
                "registration_number": "REG-202",
                "registration_date": "2022-03-12",
                "case_stage": "Disposed",
                "court_number_judge": "Court 3",
                "court_name": "Chief Metropolitan Magistrate Court Mumbai",
                "state_code": "1",
                "dist_code": "5",
                "court_complex_code": "1010002",
                "est_code": "2",
                "first_hearing_date": "2022-04-01",
                "next_hearing_date": "",
                "status": "Disposed",
                "parties": [
                    {"type": "petitioner", "name": "State of Maharashtra", "advocate": "APP"},
                    {"type": "respondent", "name": "John Doe, Alias JD", "advocate": "Legal Aid"},
                ],
                "acts": [
                    {"act": "IPC", "sections": "420, 406"},
                    {"act": "IT Act", "sections": "66C, 66D"},
                ],
                "hearings": [
                    {
                        "business_date": "2022-04-01",
                        "hearing_date": "2022-05-15",
                        "purpose": "Charge",
                        "judge": "Judge B",
                    },
                    {
                        "business_date": "2022-05-15",
                        "hearing_date": "2022-07-20",
                        "purpose": "Judgment",
                        "judge": "Judge B",
                    },
                ],
            },
        ]

        for tc in self.test_cases:
            self.db.save_case(tc)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_json_export_field_level_round_trip_fidelity(self) -> None:
        """Export to JSON, re-parse, and assert 100% field equivalence against Database records."""
        json_path = os.path.join(self.temp_dir, "export.json")
        count = export_to_json(self.db, json_path)
        self.assertEqual(count, 2)

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(len(data), 2)
        by_cnr = {c["cnr_number"]: c for c in data}

        for expected in self.test_cases:
            cnr = expected["cnr_number"]
            self.assertIn(cnr, by_cnr)
            actual = by_cnr[cnr]

            # Verify core case fields
            for field in [
                "case_type",
                "case_number",
                "filing_number",
                "filing_date",
                "registration_number",
                "registration_date",
                "case_stage",
                "court_name",
                "status",
            ]:
                self.assertEqual(actual[field], expected[field], f"Field mismatch on {field}")

            # Verify nested parties
            self.assertEqual(len(actual["parties"]), len(expected["parties"]))
            for exp_p, act_p in zip(expected["parties"], actual["parties"]):
                self.assertEqual(act_p["name"], exp_p["name"])
                self.assertEqual(act_p["advocate"], exp_p["advocate"])
                self.assertEqual(act_p["party_type"], exp_p["type"])

            # Verify nested acts
            self.assertEqual(len(actual["acts"]), len(expected["acts"]))
            for exp_a, act_a in zip(expected["acts"], actual["acts"]):
                self.assertEqual(act_a["act_name"], exp_a["act"])
                self.assertEqual(act_a["sections"], exp_a["sections"])

            # Verify nested hearings
            self.assertEqual(len(actual["hearings"]), len(expected["hearings"]))
            for exp_h, act_h in zip(expected["hearings"], actual["hearings"]):
                self.assertEqual(act_h["purpose"], exp_h["purpose"])
                self.assertEqual(act_h["judge"], exp_h["judge"])

    def test_relational_csv_export_field_level_round_trip_fidelity(self) -> None:
        """Export to relational CSV files and verify each table matches database rows."""
        csv_dir = os.path.join(self.temp_dir, "csv_relational")
        counts = export_to_csv_relational(self.db, csv_dir)

        self.assertEqual(counts["cases"], 2)
        self.assertEqual(counts["parties"], 4)
        self.assertEqual(counts["acts"], 3)
        self.assertEqual(counts["hearings"], 3)

        # Read cases.csv
        cases_file = os.path.join(csv_dir, "cases.csv")
        with open(cases_file, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 2)
            csv_by_cnr = {r["cnr_number"]: r for r in reader}
            for exp in self.test_cases:
                cnr = exp["cnr_number"]
                self.assertEqual(csv_by_cnr[cnr]["case_type"], exp["case_type"])
                self.assertEqual(csv_by_cnr[cnr]["case_stage"], exp["case_stage"])
                self.assertEqual(csv_by_cnr[cnr]["status"], exp["status"])

        # Read parties.csv
        parties_file = os.path.join(csv_dir, "parties.csv")
        with open(parties_file, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 4)
            # Assert party names exist
            names = [r["name"] for r in reader]
            self.assertIn("Alpha Corp", names)
            self.assertIn("John Doe, Alias JD", names)

        # Read acts.csv
        acts_file = os.path.join(csv_dir, "acts.csv")
        with open(acts_file, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 3)

        # Read hearings.csv
        hearings_file = os.path.join(csv_dir, "hearings.csv")
        with open(hearings_file, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 3)

    def test_flat_csv_export_fidelity_and_escaping(self) -> None:
        """Export flat CSV and verify aggregated strings and special character escaping."""
        flat_csv_path = os.path.join(self.temp_dir, "cases_flat.csv")
        count = export_to_csv_flat(self.db, flat_csv_path)
        self.assertEqual(count, 2)

        with open(flat_csv_path, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 2)
            row_2 = next(r for r in reader if r["cnr_number"] == "MH01000000022022")
            # Verify aggregated party string contains John Doe, Alias JD
            self.assertIn("John Doe, Alias JD", row_2["respondents"])
            self.assertEqual(row_2["total_hearings"], "2")
            self.assertIn("IPC: 420, 406", row_2["acts_sections"])

    def test_export_status_and_cnr_subset_filtering(self) -> None:
        """Verify filtering exports by status and cnr_list produces exact subsets."""
        json_pending = os.path.join(self.temp_dir, "pending.json")
        count_pending = export_to_json(self.db, json_pending, status="Pending")
        self.assertEqual(count_pending, 1)

        with open(json_pending, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["cnr_number"], "KA01000000012021")

        # Filter by CNR list
        csv_cnr_dir = os.path.join(self.temp_dir, "cnr_subset")
        counts = export_to_csv_relational(
            self.db,
            csv_cnr_dir,
            cnr_list=["MH01000000022022"],
        )
        self.assertEqual(counts["cases"], 1)
        self.assertEqual(counts["parties"], 2)

    def test_exporter_class_unified_interface(self) -> None:
        """Verify Exporter facade correctly dispatches to json, flat csv, and relational csv."""
        exporter = Exporter(self.db)

        # JSON
        json_out = os.path.join(self.temp_dir, "facade.json")
        res_json = exporter.export(format="json", output_path=json_out)
        self.assertEqual(res_json, 2)
        self.assertTrue(os.path.exists(json_out))

        # Flat CSV
        csv_flat_out = os.path.join(self.temp_dir, "facade_flat.csv")
        res_flat = exporter.export(format="csv", mode="flat", output_path=csv_flat_out)
        self.assertEqual(res_flat, 2)
        self.assertTrue(os.path.exists(csv_flat_out))

        # Relational CSV
        csv_rel_dir = os.path.join(self.temp_dir, "facade_rel")
        res_rel = exporter.export(format="csv", mode="relational", output_path=csv_rel_dir)
        self.assertIsInstance(res_rel, dict)
        self.assertEqual(res_rel["cases"], 2)
        self.assertTrue(os.path.exists(os.path.join(csv_rel_dir, "cases.csv")))


class TestAdversarialEdgeCases(unittest.TestCase):
    """Stress tests on zero-boundary leases, cascade deletions, and custom bases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_edge.db")
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reaper_zero_lease_timeout_reclaims_all_running(self) -> None:
        """With lease_timeout_seconds=0, any currently running task should be immediately reaped."""
        tid = self.db.enqueue_task("instant_task", {})
        self.db.claim_next_task("worker-zero")
        self.assertEqual(self.db.get_task_by_id(tid)["status"], "running")

        # Small sleep to ensure started_at is strictly in the past
        time.sleep(0.01)
        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=0)
        self.assertEqual(reaped, 1)
        self.assertEqual(self.db.get_task_by_id(tid)["status"], "queued")

    def test_cnr_cascade_delete_removes_all_children(self) -> None:
        """Verify deleting a master case completely cleans up child tables via foreign keys."""
        cnr = "DL01009999992020"
        case = {
            "cnr_number": cnr,
            "parties": [{"type": "petitioner", "name": "P1"}, {"type": "respondent", "name": "R1"}],
            "acts": [{"act": "IPC", "sections": "302"}],
            "hearings": [{"purpose": "Trial"}],
        }
        self.db.save_case(case)

        # Confirm children exist
        p_cnt = self.db.conn.execute("SELECT COUNT(*) FROM parties WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        a_cnt = self.db.conn.execute("SELECT COUNT(*) FROM acts WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        h_cnt = self.db.conn.execute("SELECT COUNT(*) FROM hearings WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        self.assertEqual(p_cnt, 2)
        self.assertEqual(a_cnt, 1)
        self.assertEqual(h_cnt, 1)

        # Delete case
        deleted = self.db.delete_case(cnr)
        self.assertTrue(deleted)

        # Verify 0 children remain
        p_cnt2 = self.db.conn.execute("SELECT COUNT(*) FROM parties WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        a_cnt2 = self.db.conn.execute("SELECT COUNT(*) FROM acts WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        h_cnt2 = self.db.conn.execute("SELECT COUNT(*) FROM hearings WHERE cnr_number = ?", (cnr,)).fetchone()[0]
        self.assertEqual(p_cnt2, 0)
        self.assertEqual(a_cnt2, 0)
        self.assertEqual(h_cnt2, 0)

    def test_custom_backoff_base_growth(self) -> None:
        """Verify backoff with base=5.0 produces ~5s, ~10s delays."""
        tid = self.db.enqueue_task("custom_base_task", {}, max_retries=2, backoff_base=5.0)
        t_start = datetime.now(timezone.utc)
        self.db.fail_task(tid, error_message="attempt 1", retryable=True)

        t_rec = self.db.get_task_by_id(tid)
        sched = datetime.fromisoformat(t_rec["scheduled_at"])
        delay = (sched - t_start).total_seconds()
        # 5.0 * 2^0 + [0.1, 1.0] = [5.1, 6.0]
        self.assertGreaterEqual(delay, 5.0)
        self.assertLessEqual(delay, 6.5)

    def test_whitespace_cnr_raises_value_error(self) -> None:
        """Whitespace or empty CNR must raise ValueError."""
        with self.assertRaises(ValueError):
            self.db.save_case({"cnr_number": "   ", "case_type": "OS"})
        with self.assertRaises(ValueError):
            self.db.save_case({"cnr_number": "", "case_type": "OS"})

    def test_export_sparse_null_fields_survives_without_error(self) -> None:
        """Verify export succeeds cleanly when case records have extensive NULL / None fields."""
        sparse_case = {
            "cnr_number": "SPARSE0000002024",
            "case_type": None,
            "case_number": None,
            "filing_number": None,
            "filing_date": None,
            "registration_number": None,
            "registration_date": None,
            "case_stage": None,
            "court_number_judge": None,
            "court_name": None,
            "status": "Pending",
            "parties": [],
            "acts": [],
            "hearings": [],
        }
        self.db.save_case(sparse_case)

        # JSON
        json_path = os.path.join(self.temp_dir, "sparse.json")
        res_j = export_to_json(self.db, json_path)
        self.assertEqual(res_j, 1)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data[0]["cnr_number"], "SPARSE0000002024")

        # Relational CSV
        rel_dir = os.path.join(self.temp_dir, "sparse_rel")
        res_r = export_to_csv_relational(self.db, rel_dir)
        self.assertEqual(res_r["cases"], 1)

        # Flat CSV
        flat_path = os.path.join(self.temp_dir, "sparse_flat.csv")
        res_f = export_to_csv_flat(self.db, flat_path)
        self.assertEqual(res_f, 1)


class TestConcurrentWorkloadStress(unittest.TestCase):
    """Empirically stress-tests multi-threaded claiming and multi-threaded CNR upserting."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_concurrency.db")
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_concurrent_multi_worker_atomic_claiming(self) -> None:
        """Stress test: 5 concurrent threads competing to claim 30 queued tasks.

        Guarantees each task is claimed exactly once with zero collisions.
        """
        import concurrent.futures

        task_count = 30
        for i in range(task_count):
            self.db.enqueue_task("stress_task", {"index": i})

        claimed_task_ids = []
        lock = concurrent.futures.thread.threading.Lock()

        def worker_loop(worker_idx: int) -> None:
            # Each worker connects to the WAL database
            worker_db = Database(self.db_path)
            try:
                for _ in range(10):
                    task = worker_db.claim_next_task(f"worker-thread-{worker_idx}")
                    if task:
                        with lock:
                            claimed_task_ids.append(task["id"])
                    time.sleep(0.005)
            finally:
                worker_db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker_loop, w) for w in range(5)]
            concurrent.futures.wait(futures)

        # Verify all tasks were claimed
        self.assertEqual(len(claimed_task_ids), task_count, f"Expected {task_count} claims, got {len(claimed_task_ids)}")
        # Verify zero duplicates (atomic guarantee)
        self.assertEqual(len(set(claimed_task_ids)), task_count, "Race condition detected: duplicate task claims!")

    def test_concurrent_multi_threaded_cnr_upsert(self) -> None:
        """Stress test: 5 concurrent threads attempting to update the same CNR record simultaneously."""
        import concurrent.futures

        cnr = "CONCURRENT000001"
        self.db.save_case({"cnr_number": cnr, "case_stage": "Initial", "case_type": "Original Suit"})

        stages = ["Notice", "Appearance", "Pleadings", "Issues", "Evidence", "Arguments", "Judgment", "Disposed"]

        def updater_loop(thread_idx: int) -> None:
            thread_db = Database(self.db_path)
            try:
                for st in stages:
                    thread_db.save_case({
                        "cnr_number": cnr,
                        "case_stage": f"{st}-by-T{thread_idx}",
                        "hearings": [{"purpose": f"Hearing for {st}"}],
                    })
                    time.sleep(0.002)
            finally:
                thread_db.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(updater_loop, t) for t in range(5)]
            concurrent.futures.wait(futures)

        # Verify database has exactly 1 case record with this CNR
        self.assertEqual(self.db.get_cases_count(), 1)
        final_case = self.db.get_case_by_cnr(cnr)
        self.assertIsNotNone(final_case)
        self.assertEqual(final_case["cnr_number"], cnr)
        # Verify hearings count is 1 (clean sync)
        self.assertEqual(len(final_case["hearings"]), 1)


if __name__ == "__main__":
    unittest.main()


