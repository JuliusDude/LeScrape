"""
ecourts.storage.db
==================
Normalized SQLite storage engine with WAL mode, foreign key cascade constraints,
CNR primary key deduplication, and atomic queue management operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import random
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Union

from ecourts.parser.case_details import CaseDetail

log = logging.getLogger("ecourts.storage.db")

SCHEMA_DDL = """
-- Enable WAL mode and foreign key integrity
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- 1. Master Cases Table (Deduplicated on 16-character CNR number)
CREATE TABLE IF NOT EXISTS cases (
    cnr_number              TEXT PRIMARY KEY NOT NULL,
    case_type               TEXT,
    case_number             TEXT,
    filing_number           TEXT,
    filing_date             TEXT,
    registration_number     TEXT,
    registration_date       TEXT,
    case_stage              TEXT,
    court_number_judge      TEXT,
    court_name              TEXT,
    state_code              TEXT,
    dist_code               TEXT,
    court_complex_code      TEXT,
    est_code                TEXT,
    first_hearing_date      TEXT,
    next_hearing_date       TEXT,
    status                  TEXT DEFAULT 'Pending',
    raw_json                TEXT,
    first_scraped_at        TEXT NOT NULL,
    last_scraped_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_stage ON cases(case_stage);
CREATE INDEX IF NOT EXISTS idx_cases_next_hearing ON cases(next_hearing_date);
CREATE INDEX IF NOT EXISTS idx_cases_court ON cases(state_code, dist_code, court_complex_code);

-- 2. Parties Table (Petitioners and Respondents)
CREATE TABLE IF NOT EXISTS parties (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cnr_number              TEXT NOT NULL,
    party_type              TEXT NOT NULL CHECK(party_type IN ('petitioner', 'respondent')),
    party_seq               INTEGER NOT NULL,
    name                    TEXT NOT NULL,
    advocate                TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY(cnr_number) REFERENCES cases(cnr_number) ON DELETE CASCADE,
    UNIQUE(cnr_number, party_type, party_seq)
);

CREATE INDEX IF NOT EXISTS idx_parties_cnr ON parties(cnr_number);
CREATE INDEX IF NOT EXISTS idx_parties_name ON parties(name);

-- 3. Statutory Acts & Sections Table
CREATE TABLE IF NOT EXISTS acts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cnr_number              TEXT NOT NULL,
    act_seq                 INTEGER NOT NULL,
    act_name                TEXT NOT NULL,
    sections                TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY(cnr_number) REFERENCES cases(cnr_number) ON DELETE CASCADE,
    UNIQUE(cnr_number, act_seq)
);

CREATE INDEX IF NOT EXISTS idx_acts_cnr ON acts(cnr_number);
CREATE INDEX IF NOT EXISTS idx_acts_name ON acts(act_name);

-- 4. Chronological Hearings Table
CREATE TABLE IF NOT EXISTS hearings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    cnr_number              TEXT NOT NULL,
    hearing_seq             INTEGER NOT NULL,
    business_date           TEXT,
    hearing_date            TEXT,
    purpose                 TEXT,
    judge                   TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY(cnr_number) REFERENCES cases(cnr_number) ON DELETE CASCADE,
    UNIQUE(cnr_number, hearing_seq)
);

CREATE INDEX IF NOT EXISTS idx_hearings_cnr ON hearings(cnr_number);
CREATE INDEX IF NOT EXISTS idx_hearings_date ON hearings(hearing_date);

-- 5. Persistent Queue Tasks Table
CREATE TABLE IF NOT EXISTS tasks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type               TEXT NOT NULL,
    params_json             TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'queued'
                            CHECK(status IN ('queued', 'running', 'completed', 'failed')),
    priority                INTEGER NOT NULL DEFAULT 0,
    retry_count             INTEGER NOT NULL DEFAULT 0,
    max_retries             INTEGER NOT NULL DEFAULT 3,
    backoff_base            REAL NOT NULL DEFAULT 2.0,
    scheduled_at            TEXT NOT NULL,
    worker_id               TEXT,
    results_count           INTEGER DEFAULT 0,
    error_message           TEXT,
    error_traceback         TEXT,
    created_at              TEXT NOT NULL,
    started_at              TEXT,
    completed_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(status, scheduled_at, priority DESC, id ASC);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Encapsulates the persistent SQLite database, providing atomic transactions,

    safe upsert deduplication on CNR, relational queries, and resilient task queue claims.
    """

    def __init__(self, db_path: str = "ecourts.db") -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Applies schema DDL and enforces WAL mode."""
        with self._lock:
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self.conn.execute("PRAGMA busy_timeout = 5000;")
            self.conn.executescript(SCHEMA_DDL)
            self.conn.commit()

    def save_case(self, case: Union[CaseDetail, Dict[str, Any]]) -> bool:
        """Atomically upserts a master case record deduplicated on CNR number,

        and synchronizes child parties, acts, and hearings within a single transaction.
        Returns True on success.
        """
        now = _iso_now()

        if isinstance(case, CaseDetail):
            case_dict = case.to_dict()
        else:
            case_dict = dict(case)

        cnr = case_dict.get("cnr_number", "").strip()
        if not cnr:
            raise ValueError("Cannot persist case record without valid CNR number")

        status = case_dict.get("status") or case_dict.get("case_status", "Pending")

        with self._lock, self.conn:
            # 1. Upsert master case
            self.conn.execute(
                """
                INSERT INTO cases (
                    cnr_number, case_type, case_number, filing_number, filing_date,
                    registration_number, registration_date, case_stage, court_number_judge,
                    court_name, state_code, dist_code, court_complex_code, est_code,
                    first_hearing_date, next_hearing_date, status, raw_json,
                    first_scraped_at, last_scraped_at
                ) VALUES (
                    :cnr_number, :case_type, :case_number, :filing_number, :filing_date,
                    :registration_number, :registration_date, :case_stage, :court_number_judge,
                    :court_name, :state_code, :dist_code, :court_complex_code, :est_code,
                    :first_hearing_date, :next_hearing_date, :status, :raw_json,
                    :now, :now
                )
                ON CONFLICT(cnr_number) DO UPDATE SET
                    case_type           = COALESCE(excluded.case_type, cases.case_type),
                    case_number         = COALESCE(excluded.case_number, cases.case_number),
                    filing_number       = COALESCE(excluded.filing_number, cases.filing_number),
                    filing_date         = COALESCE(excluded.filing_date, cases.filing_date),
                    registration_number = COALESCE(excluded.registration_number, cases.registration_number),
                    registration_date   = COALESCE(excluded.registration_date, cases.registration_date),
                    case_stage          = COALESCE(excluded.case_stage, cases.case_stage),
                    court_number_judge  = COALESCE(excluded.court_number_judge, cases.court_number_judge),
                    court_name          = COALESCE(excluded.court_name, cases.court_name),
                    first_hearing_date  = COALESCE(excluded.first_hearing_date, cases.first_hearing_date),
                    next_hearing_date   = COALESCE(excluded.next_hearing_date, cases.next_hearing_date),
                    status              = COALESCE(excluded.status, cases.status),
                    raw_json            = excluded.raw_json,
                    last_scraped_at     = excluded.last_scraped_at
                """,
                {
                    "cnr_number": cnr,
                    "case_type": case_dict.get("case_type", ""),
                    "case_number": case_dict.get("case_number", ""),
                    "filing_number": case_dict.get("filing_number", ""),
                    "filing_date": case_dict.get("filing_date", ""),
                    "registration_number": case_dict.get("registration_number", ""),
                    "registration_date": case_dict.get("registration_date", ""),
                    "case_stage": case_dict.get("case_stage") or case_dict.get("stage", ""),
                    "court_number_judge": case_dict.get("court_number_judge") or case_dict.get("coram", ""),
                    "court_name": case_dict.get("court_name", ""),
                    "state_code": case_dict.get("state_code", ""),
                    "dist_code": case_dict.get("dist_code", ""),
                    "court_complex_code": case_dict.get("court_complex_code", ""),
                    "est_code": case_dict.get("est_code", ""),
                    "first_hearing_date": case_dict.get("first_hearing_date", ""),
                    "next_hearing_date": case_dict.get("next_hearing_date", ""),
                    "status": status,
                    "raw_json": json.dumps(case_dict.get("raw_data") or case_dict),
                    "now": now,
                },
            )

            # 2. Sync Parties if provided
            parties_input = case_dict.get("parties", [])
            # Also support separate petitioners/respondents lists if provided
            if not parties_input:
                for p in case_dict.get("petitioners", []):
                    parties_input.append({"type": "petitioner", **p})
                for r in case_dict.get("respondents", []):
                    parties_input.append({"type": "respondent", **r})

            if parties_input:
                self.conn.execute("DELETE FROM parties WHERE cnr_number = ?", (cnr,))
                p_rows = []
                pet_seq, resp_seq = 1, 1
                for p in parties_input:
                    ptype = p.get("type") or p.get("party_type", "petitioner")
                    seq = pet_seq if ptype == "petitioner" else resp_seq
                    if ptype == "petitioner":
                        pet_seq += 1
                    else:
                        resp_seq += 1
                    p_rows.append((cnr, ptype, seq, p.get("name", ""), p.get("advocate", ""), now))

                self.conn.executemany(
                    """
                    INSERT INTO parties (cnr_number, party_type, party_seq, name, advocate, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    p_rows,
                )

            # 3. Sync Acts if provided
            acts_input = case_dict.get("acts", [])
            if acts_input:
                self.conn.execute("DELETE FROM acts WHERE cnr_number = ?", (cnr,))
                a_rows = [
                    (
                        cnr,
                        seq,
                        a.get("act") or a.get("act_name", ""),
                        a.get("sections") or a.get("section", ""),
                        now,
                    )
                    for seq, a in enumerate(acts_input, start=1)
                ]
                self.conn.executemany(
                    """
                    INSERT INTO acts (cnr_number, act_seq, act_name, sections, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    a_rows,
                )

            # 4. Sync Hearings if provided
            hearings_input = case_dict.get("hearings") or case_dict.get("case_history", [])
            if hearings_input:
                self.conn.execute("DELETE FROM hearings WHERE cnr_number = ?", (cnr,))
                h_rows = [
                    (
                        cnr,
                        seq,
                        h.get("business_date") or h.get("date", ""),
                        h.get("hearing_date") or h.get("next_date", ""),
                        h.get("purpose", ""),
                        h.get("judge", ""),
                        now,
                    )
                    for seq, h in enumerate(hearings_input, start=1)
                ]
                self.conn.executemany(
                    """
                    INSERT INTO hearings (cnr_number, hearing_seq, business_date, hearing_date, purpose, judge, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    h_rows,
                )

        return True

    def get_case_by_cnr(self, cnr_number: str) -> Optional[Dict[str, Any]]:
        """Retrieve complete case record with nested parties, acts, and hearings."""
        with self._lock:
            row = self.conn.execute("SELECT * FROM cases WHERE cnr_number = ?", (cnr_number,)).fetchone()
            if not row:
                return None

            case = dict(row)

            # Child relations
            parties = self.conn.execute(
                "SELECT * FROM parties WHERE cnr_number = ? ORDER BY party_type, party_seq",
                (cnr_number,),
            ).fetchall()
            acts = self.conn.execute(
                "SELECT * FROM acts WHERE cnr_number = ? ORDER BY act_seq",
                (cnr_number,),
            ).fetchall()
            hearings = self.conn.execute(
                "SELECT * FROM hearings WHERE cnr_number = ? ORDER BY hearing_seq",
                (cnr_number,),
            ).fetchall()

            case["parties"] = [dict(p) for p in parties]
            case["petitioners"] = [dict(p) for p in parties if p["party_type"] == "petitioner"]
            case["respondents"] = [dict(p) for p in parties if p["party_type"] == "respondent"]
            case["acts"] = [dict(a) for a in acts]
            case["hearings"] = [dict(h) for h in hearings]

            return case

    def list_cases(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List cases with nested relations, optionally filtered by status."""
        with self._lock:
            query = "SELECT cnr_number FROM cases"
            params: List[Any] = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY first_scraped_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = self.conn.execute(query, params).fetchall()
            return [self.get_case_by_cnr(r["cnr_number"]) for r in rows if r["cnr_number"]]

    def delete_case(self, cnr_number: str) -> bool:
        """Deletes a case record; cascading foreign keys automatically remove child records."""
        with self._lock, self.conn:
            cur = self.conn.execute("DELETE FROM cases WHERE cnr_number = ?", (cnr_number,))
            return cur.rowcount > 0

    def get_cases_count(self, status: Optional[str] = None) -> int:
        """Return total count of cases in database."""
        with self._lock:
            query = "SELECT COUNT(*) FROM cases"
            params: List[Any] = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            return self.conn.execute(query, params).fetchone()[0]

    # ── Queue Operations ─────────────────────────────────────────────────────────

    def enqueue_task(
        self,
        task_type: str,
        params: Dict[str, Any],
        priority: int = 0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        scheduled_at: Optional[str] = None,
    ) -> int:
        """Enqueue a new task with status='queued'."""
        now = _iso_now()
        sched = scheduled_at or now
        params_str = json.dumps(params)

        with self._lock, self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO tasks (
                    task_type, params_json, status, priority, retry_count,
                    max_retries, backoff_base, scheduled_at, created_at
                ) VALUES (?, ?, 'queued', ?, 0, ?, ?, ?, ?)
                """,
                (task_type, params_str, priority, max_retries, backoff_base, sched, now),
            )
            return int(cur.lastrowid)

    def claim_next_task(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Atomically claim the next eligible queued task using UPDATE ... RETURNING.

        Guarantees zero race conditions across concurrent workers.
        """
        now = _iso_now()
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    started_at = :now,
                    worker_id = :worker_id
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE status = 'queued'
                      AND scheduled_at <= :now
                    ORDER BY priority DESC, id ASC
                    LIMIT 1
                )
                RETURNING id, task_type, params_json, status, retry_count, max_retries, backoff_base, priority, created_at, started_at, worker_id;
                """,
                {"now": now, "worker_id": worker_id},
            )
            row = cursor.fetchone()
            if not row:
                return None

            task = dict(row)
            try:
                task["params"] = json.loads(task["params_json"])
            except json.JSONDecodeError:
                task["params"] = {}
            return task

    def complete_task(
        self,
        task_id: int,
        results_count: int = 0,
        result_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Transition task to completed status."""
        now = _iso_now()
        with self._lock, self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'completed',
                    completed_at = :now,
                    results_count = :results_count
                WHERE id = :id
                """,
                {"id": task_id, "now": now, "results_count": results_count},
            )

    def fail_task(
        self,
        task_id: int,
        error_message: str,
        error_traceback: str = "",
        retryable: bool = True,
        max_delay: float = 300.0,
    ) -> bool:
        """Handle task failure: if retryable and retry_count < max_retries,

        resets to queued with exponential backoff (capped at max_delay); otherwise transitions to failed.
        Returns True if task was rescheduled for retry, False if permanently failed.
        """
        now = datetime.now(timezone.utc)
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT retry_count, max_retries, backoff_base, error_message FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                return False

            retry_count = row["retry_count"]
            max_retries = row["max_retries"]
            backoff_base = row["backoff_base"]

            # If the current attempt's retry_count was already incremented by watchdog reap,
            # avoid double-incrementing retry_count for this retry cycle
            already_reaped = bool(
                row["error_message"] and "reclaimed by watchdog" in str(row["error_message"]).lower()
            )
            new_retry_count = retry_count if already_reaped else retry_count + 1

            if retryable and (new_retry_count <= max_retries):
                # Exponential backoff with jitter capped at max_delay (default 300.0s)
                delay = (backoff_base * (2 ** (new_retry_count - 1))) + random.uniform(0.1, 1.0)
                delay = min(delay, max_delay)
                next_sched = (now + timedelta(seconds=delay)).isoformat()
                self.conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'queued',
                        retry_count = :new_retry_count,
                        scheduled_at = :sched,
                        worker_id = NULL,
                        error_message = :err,
                        error_traceback = :tb
                    WHERE id = :id
                    """,
                    {
                        "id": task_id,
                        "new_retry_count": new_retry_count,
                        "sched": next_sched,
                        "err": str(error_message),
                        "tb": error_traceback,
                    },
                )
                log.info(
                    "Task #%d failed (attempt %d/%d). Rescheduled in %.2fs",
                    task_id,
                    new_retry_count,
                    max_retries,
                    delay,
                )
                return True
            else:
                self.conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        completed_at = :now,
                        error_message = :err,
                        error_traceback = :tb
                    WHERE id = :id
                    """,
                    {
                        "id": task_id,
                        "now": now.isoformat(),
                        "err": str(error_message),
                        "tb": error_traceback,
                    },
                )
                log.warning("Task #%d permanently failed: %s", task_id, error_message)
                return False

    def reap_abandoned_tasks(self, lease_timeout_seconds: int = 300) -> int:
        """Watchdog / reaper: reclaims stale 'running' tasks whose worker process
        died or timed out past lease_timeout_seconds.
        Increments retry_count; if retry_count + 1 >= max_retries, transitions to 'failed'
        to terminate poison-pill loops, otherwise resets to 'queued'.
        """
        threshold = (
            datetime.now(timezone.utc) - timedelta(seconds=lease_timeout_seconds)
        ).isoformat()
        now_iso = _iso_now()
        with self._lock, self.conn:
            # 1. Terminate poison-pill tasks that have reached or exceeded max_retries
            cur_fail = self.conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    completed_at = :now,
                    error_message = 'Reclaimed by watchdog: max retries exceeded'
                WHERE status = 'running'
                  AND started_at < :threshold
                  AND retry_count + 1 >= max_retries
                """,
                {"threshold": threshold, "now": now_iso},
            )
            # 2. Reset remaining abandoned tasks to 'queued' with incremented retry_count
            cur_requeue = self.conn.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    retry_count = retry_count + 1,
                    worker_id = NULL,
                    error_message = 'Reclaimed by watchdog: lease timed out'
                WHERE status = 'running'
                  AND started_at < :threshold
                """,
                {"threshold": threshold},
            )
            reaped = cur_fail.rowcount + cur_requeue.rowcount
            if reaped > 0:
                log.warning("Watchdog reaped %d abandoned task(s)", reaped)
            return reaped

    def get_task_by_id(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            t = dict(row)
            try:
                t["params"] = json.loads(t["params_json"])
            except json.JSONDecodeError:
                t["params"] = {}
            return t

    def get_queue_stats(self) -> Dict[str, int]:
        """Returns count of tasks by status."""
        with self._lock:
            stats = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "total": 0}
            rows = self.conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
            for r in rows:
                stats[r["status"]] = r["cnt"]
                stats["total"] += r["cnt"]
            return stats

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
