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
            case_type           TEXT,
            filing_number       TEXT,
            filing_date         TEXT,
            registration_number TEXT,
            registration_date   TEXT,
            petitioner          TEXT,
            respondent          TEXT,
            petitioner_advocate TEXT,
            respondent_advocate TEXT,
            first_hearing_date  TEXT,
            next_hearing_date   TEXT,
            case_stage          TEXT,
            court_number_judge  TEXT,
            court_name          TEXT,
            acts                TEXT,
            case_history        TEXT,
            state_code          TEXT,
            dist_code           TEXT,
            raw_json            TEXT,
            first_scraped_at    TEXT NOT NULL,
            last_scraped_at     TEXT NOT NULL,
            UNIQUE(case_number, court_name, state_code, dist_code)
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
    """
    `case` is a dict as returned by parse_case_results(), optionally with
    a merged "detail" key from get_case_detail()/search_with_details().
    """
    now = datetime.utcnow().isoformat()
    detail = case.get("detail") or {}

    petitioners = detail.get("petitioners", [])
    respondents = detail.get("respondents", [])

    conn = get_conn(db_path)
    conn.execute("""
        INSERT INTO cases (
            cnr_number, case_number, case_type, filing_number, filing_date,
            registration_number, registration_date,
            petitioner, respondent, petitioner_advocate, respondent_advocate,
            first_hearing_date, next_hearing_date, case_stage, court_number_judge,
            court_name, acts, case_history,
            state_code, dist_code, raw_json, first_scraped_at, last_scraped_at
        ) VALUES (
            :cnr_number, :case_number, :case_type, :filing_number, :filing_date,
            :registration_number, :registration_date,
            :petitioner, :respondent, :petitioner_advocate, :respondent_advocate,
            :first_hearing_date, :next_hearing_date, :case_stage, :court_number_judge,
            :court_name, :acts, :case_history,
            :state_code, :dist_code, :raw_json, :now, :now
        )
        ON CONFLICT(case_number, court_name, state_code, dist_code) DO UPDATE SET
            cnr_number          = excluded.cnr_number,
            case_type           = excluded.case_type,
            filing_number       = excluded.filing_number,
            filing_date         = excluded.filing_date,
            registration_number = excluded.registration_number,
            registration_date   = excluded.registration_date,
            petitioner          = excluded.petitioner,
            respondent          = excluded.respondent,
            petitioner_advocate = excluded.petitioner_advocate,
            respondent_advocate = excluded.respondent_advocate,
            first_hearing_date  = excluded.first_hearing_date,
            next_hearing_date   = excluded.next_hearing_date,
            case_stage          = excluded.case_stage,
            court_number_judge  = excluded.court_number_judge,
            acts                = excluded.acts,
            case_history        = excluded.case_history,
            raw_json            = excluded.raw_json,
            last_scraped_at     = excluded.last_scraped_at
    """, {
        "cnr_number": detail.get("cnr_number", case.get("cnr_number", "")),
        "case_number": case.get("case_number", ""),
        "case_type": detail.get("case_type", ""),
        "filing_number": detail.get("filing_number", ""),
        "filing_date": detail.get("filing_date", ""),
        "registration_number": detail.get("registration_number", ""),
        "registration_date": detail.get("registration_date", ""),
        "petitioner": case.get("petitioner", ""),
        "respondent": case.get("respondent", ""),
        "petitioner_advocate": json.dumps(petitioners),
        "respondent_advocate": json.dumps(respondents),
        "first_hearing_date": detail.get("first_hearing_date", ""),
        "next_hearing_date": detail.get("next_hearing_date", ""),
        "case_stage": detail.get("case_stage", ""),
        "court_number_judge": detail.get("court_number_judge", ""),
        "court_name": case.get("court_name", ""),
        "acts": json.dumps(detail.get("acts", [])),
        "case_history": json.dumps(detail.get("case_history", [])),
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
