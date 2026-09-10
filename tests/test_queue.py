"""
Unit and boundary tests for ecourts.storage.db and ecourts.queue.worker (Queue Mechanics).
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases) for:
  - Feature 11: Resilient Task Queue & Atomic Claim (10 tests)
  - Feature 12: Retry with Backoff & Watchdog (10 tests)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
import threading
import unittest

from ecourts.storage.db import Database


# ── Feature 11: Resilient Queue & Atomic Claim ─────────────────────────────────

class TestFeature11Tier1(unittest.TestCase):
    """Tier 1: Feature 11 - Resilient Task Queue & Atomic Claim (>= 5 tests)."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_f11_enqueue_and_atomic_claim(self):
        """Verify task enqueues with status='queued' and transitions to 'running' on claim."""
        task_id = self.db.enqueue_task(
            task_type="case_type_search",
            params={"state_code": "3", "year": "2025"},
            priority=10,
        )
        self.assertGreater(task_id, 0)

        task = self.db.claim_next_task(worker_id="worker_1")
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["task_type"], "case_type_search")
        self.assertEqual(task["params"]["state_code"], "3")

        # Second claim attempt when queue empty returns None
        second_claim = self.db.claim_next_task(worker_id="worker_2")
        self.assertIsNone(second_claim)

    def test_f11_claim_priority_ordering(self):
        """Verify higher priority tasks are claimed before lower priority ones."""
        t_low = self.db.enqueue_task("low_priority", {}, priority=0)
        t_high = self.db.enqueue_task("high_priority", {}, priority=100)
        t_mid = self.db.enqueue_task("mid_priority", {}, priority=50)

        c1 = self.db.claim_next_task("w1")
        self.assertEqual(c1["id"], t_high)

        c2 = self.db.claim_next_task("w1")
        self.assertEqual(c2["id"], t_mid)

        c3 = self.db.claim_next_task("w1")
        self.assertEqual(c3["id"], t_low)

    def test_f11_task_completion_lifecycle(self):
        """Verify task transitions from queued -> running -> completed."""
        tid = self.db.enqueue_task("cnr_search", {"cnr_number": "KABC0E0001992025"})
        task = self.db.claim_next_task("worker_1")
        self.assertEqual(task["id"], tid)

        self.db.complete_task(tid, results_count=5)
        t_record = self.db.get_task_by_id(tid)
        self.assertEqual(t_record["status"], "completed")
        self.assertEqual(t_record["results_count"], 5)
        self.assertIsNotNone(t_record["completed_at"])

    def test_f11_queue_stats_reporting(self):
        """Verify get_queue_stats accurately reflects task counts per status."""
        self.db.enqueue_task("t1", {})
        self.db.enqueue_task("t2", {})
        self.db.enqueue_task("t3", {})

        c1 = self.db.claim_next_task("w")
        self.db.complete_task(c1["id"])

        c2 = self.db.claim_next_task("w")
        self.db.fail_task(c2["id"], "err", retryable=False)

        # 1 completed, 1 failed, 1 queued
        stats = self.db.get_queue_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["queued"], 1)

    def test_f11_get_task_by_id_parsed_params(self):
        """Verify get_task_by_id returns deserialized params dictionary."""
        tid = self.db.enqueue_task("custom", {"key1": "val1", "count": 42})
        task = self.db.get_task_by_id(tid)
        self.assertIsNotNone(task)
        self.assertEqual(task["params"]["key1"], "val1")
        self.assertEqual(task["params"]["count"], 42)


class TestFeature11Tier2(unittest.TestCase):
    """Tier 2: Feature 11 - Resilient Queue Boundary & Concurrency (>= 5 tests)."""

    def test_f11_concurrent_claims_no_duplicates(self):
        """Verify concurrent workers each with their own connection never claim duplicate tasks."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "concurrent_queue.db")

        master_db = Database(db_path)
        num_tasks = 20
        for i in range(num_tasks):
            master_db.enqueue_task("concurrent_task", {"i": i})
        master_db.close()

        claimed_ids: list[int] = []
        lock = threading.Lock()

        def worker_func(worker_num: int):
            db = Database(db_path)
            for _ in range(5):
                t = db.claim_next_task(f"worker_{worker_num}")
                if t:
                    with lock:
                        claimed_ids.append(t["id"])
            db.close()

        threads = [threading.Thread(target=worker_func, args=(w,)) for w in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
        self.assertEqual(len(claimed_ids), num_tasks)

    def test_f11_future_scheduled_task_not_claimed(self):
        """Verify tasks scheduled in the future are not claimed until scheduled_at arrives."""
        db = Database(":memory:")
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        db.enqueue_task("future_task", {}, scheduled_at=future)

        claimed = db.claim_next_task("w1")
        self.assertIsNone(claimed)
        db.close()

    def test_f11_corrupted_params_json_graceful(self):
        """Verify task with corrupted params_json does not crash claim_next_task."""
        db = Database(":memory:")
        tid = db.enqueue_task("bad_json", {})
        db.conn.execute("UPDATE tasks SET params_json = 'corrupted {json' WHERE id = ?", (tid,))

        claimed = db.claim_next_task("w1")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["params"], {})
        db.close()

    def test_f11_empty_queue_claim_returns_none(self):
        """Verify claim on empty queue returns None without error."""
        db = Database(":memory:")
        self.assertIsNone(db.claim_next_task("w1"))
        db.close()

    def test_f11_complete_task_with_results_count(self):
        """Verify complete_task persists custom results count."""
        db = Database(":memory:")
        tid = db.enqueue_task("t", {})
        db.claim_next_task("w")
        db.complete_task(tid, results_count=93)

        t = db.get_task_by_id(tid)
        self.assertEqual(t["results_count"], 93)
        db.close()


# ── Feature 12: Retry with Backoff & Watchdog ──────────────────────────────────

class TestFeature12Tier1(unittest.TestCase):
    """Tier 1: Feature 12 - Retry with Backoff & Watchdog (>= 5 tests)."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_f12_retry_with_exponential_backoff(self):
        """Verify failed task increments retry_count and reschedules with future scheduled_at."""
        tid = self.db.enqueue_task("case_type_search", {}, max_retries=3, backoff_base=2.0)
        self.db.claim_next_task("w1")

        rescheduled = self.db.fail_task(tid, error_message="Network drop", retryable=True)
        self.assertTrue(rescheduled)

        t1 = self.db.get_task_by_id(tid)
        self.assertEqual(t1["status"], "queued")
        self.assertEqual(t1["retry_count"], 1)
        self.assertIsNone(t1["worker_id"])
        self.assertGreater(t1["scheduled_at"], t1["created_at"])

    def test_f12_terminal_failure_after_max_retries(self):
        """Verify task transitions to 'failed' when retry_count exceeds max_retries."""
        tid = self.db.enqueue_task("case_type_search", {}, max_retries=1)
        self.db.claim_next_task("w1")

        self.assertTrue(self.db.fail_task(tid, "Error 1", retryable=True))
        # Reset scheduled_at to claim again immediately
        self.db.conn.execute("UPDATE tasks SET scheduled_at = datetime('now') WHERE id = ?", (tid,))
        self.db.claim_next_task("w1")

        is_retry = self.db.fail_task(tid, "Fatal Error", retryable=True)
        self.assertFalse(is_retry)

        t_final = self.db.get_task_by_id(tid)
        self.assertEqual(t_final["status"], "failed")
        self.assertEqual(t_final["error_message"], "Fatal Error")

    def test_f12_watchdog_reaps_stale_running_task(self):
        """Verify watchdog resets tasks whose worker died past lease timeout."""
        tid = self.db.enqueue_task("job_crash", {})
        self.db.claim_next_task("dead_worker")

        # Backdate started_at by 400 seconds
        past = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past, tid))

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 1)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "queued")
        self.assertIsNone(t["worker_id"])
        self.assertIn("watchdog", t["error_message"].lower())

    def test_f12_active_running_task_not_reaped(self):
        """Verify freshly running task within lease window is not touched by watchdog."""
        tid = self.db.enqueue_task("active_task", {})
        self.db.claim_next_task("active_worker")

        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=300)
        self.assertEqual(reaped, 0)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "running")
        self.assertEqual(t["worker_id"], "active_worker")

    def test_f12_exponential_backoff_scales(self):
        """Verify backoff delay increases with successive failures."""
        tid = self.db.enqueue_task("scaling_task", {}, max_retries=5, backoff_base=3.0)

        delays = []
        for _ in range(3):
            self.db.claim_next_task("w")
            t_before = self.db.get_task_by_id(tid)
            now_iso = datetime.now(timezone.utc).isoformat()
            self.db.fail_task(tid, "fail", retryable=True)
            t_after = self.db.get_task_by_id(tid)

            dt_now = datetime.fromisoformat(now_iso)
            dt_after = datetime.fromisoformat(t_after["scheduled_at"])
            delays.append((dt_after - dt_now).total_seconds())

            # Reset scheduled_at to current UTC ISO timestamp
            self.db.conn.execute("UPDATE tasks SET scheduled_at = ? WHERE id = ?", (now_iso, tid))

        # Delays should be strictly increasing: delay 2 > delay 1
        self.assertGreater(delays[1], delays[0])


class TestFeature12Tier2(unittest.TestCase):
    """Tier 2: Feature 12 - Retry & Watchdog Boundary Cases (>= 5 tests)."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_f12_non_retryable_failure_fails_immediately(self):
        """Verify fail_task with retryable=False transitions directly to failed."""
        tid = self.db.enqueue_task("task", {}, max_retries=5)
        self.db.claim_next_task("w1")

        rescheduled = self.db.fail_task(tid, "Invalid Parameter", retryable=False)
        self.assertFalse(rescheduled)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "failed")

    def test_f12_watchdog_empty_when_no_running_tasks(self):
        """Verify watchdog returns 0 when no running tasks exist."""
        reaped = self.db.reap_abandoned_tasks(lease_timeout_seconds=60)
        self.assertEqual(reaped, 0)

    def test_f12_zero_max_retries_fails_first_attempt(self):
        """Verify task with max_retries=0 fails on its first failure."""
        tid = self.db.enqueue_task("zero_retry", {}, max_retries=0)
        self.db.claim_next_task("w")

        rescheduled = self.db.fail_task(tid, "One and done", retryable=True)
        self.assertFalse(rescheduled)

        t = self.db.get_task_by_id(tid)
        self.assertEqual(t["status"], "failed")

    def test_f12_fail_nonexistent_task_returns_false(self):
        """Verify fail_task on non-existent ID returns False gracefully."""
        self.assertFalse(self.db.fail_task(999999, "Error", retryable=True))

    def test_f12_watchdog_logs_reaper_message(self):
        """Verify reaped task has error message indicating lease timeout."""
        tid = self.db.enqueue_task("abandoned", {})
        self.db.claim_next_task("ghost_worker")

        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.db.conn.execute("UPDATE tasks SET started_at = ? WHERE id = ?", (past, tid))

        self.db.reap_abandoned_tasks(lease_timeout_seconds=60)
        t = self.db.get_task_by_id(tid)
        self.assertIn("lease timed out", t["error_message"].lower())


if __name__ == "__main__":
    unittest.main()
