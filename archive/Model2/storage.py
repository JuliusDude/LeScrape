"""
Storage Layer
==============
SQLite-backed storage with:
  - cases table (keyed by case_number+est_code, since CNR isn't always parsed)
  - scrape_jobs queue (safe resume after crash)
  - Export to CSV / JSON
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("ecourts.storage")

DB_PATH = Path("ecourts_data.db")


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path = DB_PATH):
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cnr_number          TEXT,
            case_number         TEXT,
            petitioner          TEXT,
            respondent          TEXT,
            next_hearing_date   TEXT,
            case_stage          TEXT,
            court_number_judge  TEXT,
            state_code          TEXT,
            dist_code           TEXT,
            raw_json            TEXT,
            first_scraped_at    TEXT NOT NULL,
            last_scraped_at     TEXT NOT NULL,
            UNIQUE(case_number, state_code, dist_code)
        );

        CREATE TABLE IF NOT EXISTS scrape_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type        TEXT NOT NULL,
            params_json     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            results_count   INTEGER DEFAULT 0,
            error_message   TEXT,
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            completed_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_cases_hearing ON cases(next_hearing_date);
        CREATE INDEX IF NOT EXISTS idx_jobs_status   ON scrape_jobs(status);
    """)
    conn.commit()
    conn.close()
    log.info(f"Database initialised at {db_path}")


# ── Case Storage ───────────────────────────────────────────────────────────────
def upsert_case(case: dict, state_code: str = "", dist_code: str = "", db_path: Path = DB_PATH):
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute("""
        INSERT INTO cases (
            cnr_number, case_number, petitioner, respondent,
            next_hearing_date, case_stage, court_number_judge,
            state_code, dist_code, raw_json, first_scraped_at, last_scraped_at
        ) VALUES (
            :cnr_number, :case_number, :petitioner, :respondent,
            :next_hearing_date, :case_stage, :court_number_judge,
            :state_code, :dist_code, :raw_json, :now, :now
        )
        ON CONFLICT(case_number, state_code, dist_code) DO UPDATE SET
            cnr_number          = excluded.cnr_number,
            petitioner          = excluded.petitioner,
            respondent          = excluded.respondent,
            next_hearing_date   = excluded.next_hearing_date,
            case_stage          = excluded.case_stage,
            court_number_judge  = excluded.court_number_judge,
            raw_json            = excluded.raw_json,
            last_scraped_at     = excluded.last_scraped_at
    """, {
        "cnr_number": case.get("cnr_number", ""),
        "case_number": case.get("case_number", ""),
        "petitioner": case.get("petitioner", ""),
        "respondent": case.get("respondent", ""),
        "next_hearing_date": case.get("next_hearing_date", ""),
        "case_stage": case.get("case_stage", ""),
        "court_number_judge": case.get("court_number_judge", ""),
        "state_code": state_code,
        "dist_code": dist_code,
        "raw_json": json.dumps(case),
        "now": now,
    })
    conn.commit()
    conn.close()


# ── Job Queue ──────────────────────────────────────────────────────────────────
def enqueue_job(job_type: str, params: dict, db_path: Path = DB_PATH) -> int:
    conn = get_conn(db_path)
    cursor = conn.execute("""
        INSERT INTO scrape_jobs (job_type, params_json, status, created_at)
        VALUES (?, ?, 'pending', ?)
    """, (job_type, json.dumps(params), datetime.utcnow().isoformat()))
    job_id = cursor.lastrowid
    conn.commit()
    conn.close()
    log.info(f"Enqueued job #{job_id}: {job_type}")
    return job_id


def claim_next_job(db_path: Path = DB_PATH) -> Optional[dict]:
    conn = get_conn(db_path)
    row = conn.execute("""
        SELECT * FROM scrape_jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1
    """).fetchone()

    if not row:
        conn.close()
        return None

    conn.execute("""
        UPDATE scrape_jobs SET status = 'running', started_at = ? WHERE id = ?
    """, (datetime.utcnow().isoformat(), row["id"]))
    conn.commit()
    conn.close()

    job = dict(row)
    job["params"] = json.loads(job["params_json"])
    return job


def complete_job(job_id: int, results_count: int = 0, error: str = "", db_path: Path = DB_PATH):
    status = "failed" if error else "done"
    conn = get_conn(db_path)
    conn.execute("""
        UPDATE scrape_jobs
        SET status = ?, results_count = ?, error_message = ?, completed_at = ?
        WHERE id = ?
    """, (status, results_count, error or None, datetime.utcnow().isoformat(), job_id))
    conn.commit()
    conn.close()
    log.info(f"Job #{job_id} {status} ({results_count} results)")


# ── Export ─────────────────────────────────────────────────────────────────────
def export_csv(output_path: str = "ecourts_cases.csv", db_path: Path = DB_PATH):
    import csv
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM cases ORDER BY last_scraped_at DESC").fetchall()
    conn.close()

    if not rows:
        log.warning("No cases to export")
        return

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])

    log.info(f"Exported {len(rows)} cases to {output_path}")


def export_json(output_path: str = "ecourts_cases.json", db_path: Path = DB_PATH):
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM cases ORDER BY last_scraped_at DESC").fetchall()
    conn.close()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)

    log.info(f"Exported {len(rows)} cases to {output_path}")
