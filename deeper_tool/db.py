"""
deeper_tool.db
==============
Relational SQLite database manager for deep eCourts case records.
Maintains atomic upserts, relational integrity, and foreign key cascades
across cases, parties, statutory acts, court processes, connected main matters,
chronological hearing logs, and judicial orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from deeper_tool.models import (
    ActItem,
    DeeperCaseDetail,
    HearingItem,
    MainMatterItem,
    OrderItem,
    PartyItem,
    ProcessItem,
)

log = logging.getLogger("deeper_tool.db")

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS cases (
        cnr_number TEXT PRIMARY KEY,
        case_type TEXT,
        case_number TEXT,
        filing_number TEXT,
        filing_date TEXT,
        registration_number TEXT,
        registration_date TEXT,
        efiling_number TEXT,
        efiling_date TEXT,
        first_hearing_date TEXT,
        decision_date TEXT,
        case_status TEXT,
        nature_of_disposal TEXT,
        court_number_judge TEXT,
        court_name TEXT,
        state_code TEXT,
        dist_code TEXT,
        court_complex_code TEXT,
        est_code TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        advocate TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS acts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        act TEXT NOT NULL,
        section TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS processes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        process_id TEXT NOT NULL,
        process_title TEXT,
        process_date TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS main_matters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        main_case_number TEXT,
        main_cnr_number TEXT,
        main_filing_number TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS hearings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        hearing_index INTEGER NOT NULL,
        judge TEXT,
        business_date TEXT,
        hearing_date TEXT,
        purpose TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_cnr TEXT NOT NULL,
        order_number TEXT,
        order_date TEXT,
        order_details TEXT,
        pdf_params TEXT,
        FOREIGN KEY (case_cnr) REFERENCES cases(cnr_number) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_parties_cnr ON parties(case_cnr);",
    "CREATE INDEX IF NOT EXISTS idx_acts_cnr ON acts(case_cnr);",
    "CREATE INDEX IF NOT EXISTS idx_processes_cnr ON processes(case_cnr);",
    "CREATE INDEX IF NOT EXISTS idx_main_matters_cnr ON main_matters(case_cnr);",
    "CREATE INDEX IF NOT EXISTS idx_hearings_cnr ON hearings(case_cnr);",
    "CREATE INDEX IF NOT EXISTS idx_orders_cnr ON orders(case_cnr);",
]


from contextlib import contextmanager

class DeeperDatabase:
    """Manages SQLite storage for the deep eCourts model."""

    def __init__(self, db_path: str = "deeper_ecourts.db") -> None:
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            cur = conn.cursor()
            for ddl in DDL_STATEMENTS:
                cur.execute(ddl)
            conn.commit()

    def save_case(self, case: DeeperCaseDetail) -> str:
        """Atomically upsert a deep case record with all relational child tables."""
        if not case.cnr_number or not case.cnr_number.strip():
            raise ValueError("Cannot save case without a valid cnr_number")

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cur = conn.cursor()

            # 1. Upsert Case Master
            cur.execute(
                """
                INSERT INTO cases (
                    cnr_number, case_type, case_number, filing_number, filing_date,
                    registration_number, registration_date, efiling_number, efiling_date,
                    first_hearing_date, decision_date, case_status, nature_of_disposal,
                    court_number_judge, court_name, state_code, dist_code,
                    court_complex_code, est_code, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cnr_number) DO UPDATE SET
                    case_type = excluded.case_type,
                    case_number = excluded.case_number,
                    filing_number = excluded.filing_number,
                    filing_date = excluded.filing_date,
                    registration_number = excluded.registration_number,
                    registration_date = excluded.registration_date,
                    efiling_number = excluded.efiling_number,
                    efiling_date = excluded.efiling_date,
                    first_hearing_date = excluded.first_hearing_date,
                    decision_date = excluded.decision_date,
                    case_status = excluded.case_status,
                    nature_of_disposal = excluded.nature_of_disposal,
                    court_number_judge = excluded.court_number_judge,
                    court_name = excluded.court_name,
                    state_code = excluded.state_code,
                    dist_code = excluded.dist_code,
                    court_complex_code = excluded.court_complex_code,
                    est_code = excluded.est_code,
                    updated_at = excluded.updated_at;
                """,
                (
                    case.cnr_number, case.case_type, case.case_number,
                    case.filing_number, case.filing_date, case.registration_number,
                    case.registration_date, case.efiling_number, case.efiling_date,
                    case.first_hearing_date, case.decision_date, case.case_status,
                    case.nature_of_disposal, case.court_number_judge, case.court_name,
                    case.state_code, case.dist_code, case.court_complex_code,
                    case.est_code, now
                )
            )

            # 2. Clear old children for idempotent re-saving
            cur.execute("DELETE FROM parties WHERE case_cnr = ?;", (case.cnr_number,))
            cur.execute("DELETE FROM acts WHERE case_cnr = ?;", (case.cnr_number,))
            cur.execute("DELETE FROM processes WHERE case_cnr = ?;", (case.cnr_number,))
            cur.execute("DELETE FROM main_matters WHERE case_cnr = ?;", (case.cnr_number,))
            cur.execute("DELETE FROM hearings WHERE case_cnr = ?;", (case.cnr_number,))
            cur.execute("DELETE FROM orders WHERE case_cnr = ?;", (case.cnr_number,))

            # 3. Insert Parties
            for p in case.parties:
                cur.execute(
                    "INSERT INTO parties (case_cnr, type, name, advocate) VALUES (?, ?, ?, ?);",
                    (case.cnr_number, p.type, p.name, p.advocate)
                )

            # 4. Insert Acts
            for a in case.acts:
                cur.execute(
                    "INSERT INTO acts (case_cnr, act, section) VALUES (?, ?, ?);",
                    (case.cnr_number, a.act, a.section)
                )

            # 5. Insert Processes
            for pr in case.processes:
                cur.execute(
                    "INSERT INTO processes (case_cnr, process_id, process_title, process_date) VALUES (?, ?, ?, ?);",
                    (case.cnr_number, pr.process_id, pr.process_title, pr.process_date)
                )

            # 6. Insert Main Matters
            for m in case.main_matters:
                cur.execute(
                    "INSERT INTO main_matters (case_cnr, main_case_number, main_cnr_number, main_filing_number) VALUES (?, ?, ?, ?);",
                    (case.cnr_number, m.main_case_number, m.main_cnr_number, m.main_filing_number)
                )

            # 7. Insert Hearings
            for h in case.hearings:
                cur.execute(
                    "INSERT INTO hearings (case_cnr, hearing_index, judge, business_date, hearing_date, purpose) VALUES (?, ?, ?, ?, ?, ?);",
                    (case.cnr_number, h.hearing_index, h.judge, h.business_date, h.hearing_date, h.purpose)
                )

            # 8. Insert Orders
            for o in case.orders:
                cur.execute(
                    "INSERT INTO orders (case_cnr, order_number, order_date, order_details, pdf_params) VALUES (?, ?, ?, ?, ?);",
                    (case.cnr_number, o.order_number, o.order_date, o.order_details, o.pdf_params)
                )

            conn.commit()

        log.info("Saved deeper case %s to database", case.cnr_number)
        return case.cnr_number

    def get_case(self, cnr_number: str) -> Optional[DeeperCaseDetail]:
        """Fetch a full case record including all relational children."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM cases WHERE cnr_number = ?;", (cnr_number,)).fetchone()
            if not row:
                return None

            parties = [
                PartyItem(type=r["type"], name=r["name"], advocate=r["advocate"] or "")
                for r in cur.execute("SELECT * FROM parties WHERE case_cnr = ? ORDER BY id ASC;", (cnr_number,)).fetchall()
            ]
            acts = [
                ActItem(act=r["act"], section=r["section"] or "")
                for r in cur.execute("SELECT * FROM acts WHERE case_cnr = ? ORDER BY id ASC;", (cnr_number,)).fetchall()
            ]
            processes = [
                ProcessItem(process_id=r["process_id"], process_title=r["process_title"] or "", process_date=r["process_date"] or "")
                for r in cur.execute("SELECT * FROM processes WHERE case_cnr = ? ORDER BY id ASC;", (cnr_number,)).fetchall()
            ]
            main_matters = [
                MainMatterItem(main_case_number=r["main_case_number"] or "", main_cnr_number=r["main_cnr_number"] or "", main_filing_number=r["main_filing_number"] or "")
                for r in cur.execute("SELECT * FROM main_matters WHERE case_cnr = ? ORDER BY id ASC;", (cnr_number,)).fetchall()
            ]
            hearings = [
                HearingItem(hearing_index=r["hearing_index"], judge=r["judge"] or "", business_date=r["business_date"] or "", hearing_date=r["hearing_date"] or "", purpose=r["purpose"] or "")
                for r in cur.execute("SELECT * FROM hearings WHERE case_cnr = ? ORDER BY hearing_index ASC;", (cnr_number,)).fetchall()
            ]
            orders = [
                OrderItem(order_number=r["order_number"] or "", order_date=r["order_date"] or "", order_details=r["order_details"] or "", pdf_params=r["pdf_params"] or "")
                for r in cur.execute("SELECT * FROM orders WHERE case_cnr = ? ORDER BY id ASC;", (cnr_number,)).fetchall()
            ]

            return DeeperCaseDetail(
                cnr_number=row["cnr_number"],
                case_type=row["case_type"] or "",
                case_number=row["case_number"] or "",
                filing_number=row["filing_number"] or "",
                filing_date=row["filing_date"] or "",
                registration_number=row["registration_number"] or "",
                registration_date=row["registration_date"] or "",
                efiling_number=row["efiling_number"] or "",
                efiling_date=row["efiling_date"] or "",
                first_hearing_date=row["first_hearing_date"] or "",
                decision_date=row["decision_date"] or "",
                case_status=row["case_status"] or "",
                nature_of_disposal=row["nature_of_disposal"] or "",
                court_number_judge=row["court_number_judge"] or "",
                court_name=row["court_name"] or "",
                state_code=row["state_code"] or "",
                dist_code=row["dist_code"] or "",
                court_complex_code=row["court_complex_code"] or "",
                est_code=row["est_code"] or "",
                parties=parties,
                acts=acts,
                processes=processes,
                main_matters=main_matters,
                hearings=hearings,
                orders=orders,
            )

    def list_cnrs(self) -> List[str]:
        """Returns all CNR numbers currently in the database."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT cnr_number FROM cases ORDER BY rowid ASC;").fetchall()
            return [r["cnr_number"] for r in rows]
