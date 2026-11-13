"""
V1.db
==============
Relational SQLite database manager for deep eCourts case records.
Maintains atomic upserts, relational integrity, and foreign key cascades
across cases, parties, statutory acts, court processes, connected main matters,
chronological hearing logs, and judicial orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from V1.models import (
    ActItem,
    DeeperCaseDetail,
    HearingItem,
    MainMatterItem,
    OrderItem,
    PartyItem,
    ProcessItem,
)

log = logging.getLogger("V1.db")

# Mirrors parser.VB_ARG_FIELDS (kept here so db.py does not import the parser).
VB_FIELDS = (
    "court_code", "dist_code", "nextdate", "cino", "state_code", "disposal_flag",
    "business_date", "court_no", "national_court_code", "search_by", "srno",
)

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

REDACTED_PARAM_KEYS = ("app_token", "ct_captcha_code")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Ordered, idempotent migrations: (version, [DDL...], [(table, column, decl)...]).
# Version 3 (live-test fixes) is additive and guarded by PRAGMA table_info.
MIGRATIONS = [
    (2, [
        """
        CREATE TABLE IF NOT EXISTS raw_responses (
            id INTEGER PRIMARY KEY,
            endpoint TEXT NOT NULL,
            request_params_json TEXT NOT NULL,
            http_status INTEGER,
            fetched_at TEXT NOT NULL,
            body_sha256 TEXT NOT NULL,
            body BLOB NOT NULL,
            cnr_number TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_raw_cnr_ep ON raw_responses(cnr_number, endpoint, fetched_at);",
        """
        CREATE TABLE IF NOT EXISTS fetch_errors (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            endpoint TEXT,
            cnr_number TEXT,
            error_class TEXT,
            message TEXT,
            http_status INTEGER,
            raw_response_id INTEGER,
            occurred_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS search_runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            state_code TEXT,
            dist_code TEXT,
            court_complex_code TEXT,
            est_code TEXT,
            case_type TEXT,
            search_year TEXT,
            case_status TEXT,
            started_at TEXT,
            finished_at TEXT,
            attempts INTEGER,
            row_count INTEGER,
            outcome TEXT,
            raw_response_id INTEGER
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS search_results (
            id INTEGER PRIMARY KEY,
            search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
            sr_no TEXT,
            case_number TEXT,
            petitioner TEXT,
            respondent TEXT,
            court_name TEXT,
            cnr_number TEXT,
            view_params_json TEXT,
            raw_cells_json TEXT
        );
        """,
    ], [
        ("cases", "view_params_json", "TEXT"),
        ("cases", "source_response_id", "INTEGER"),
        ("cases", "status_assumed", "INTEGER DEFAULT 0"),
    ]),
    (3, [], [
        ("parties", "party_seq", "INTEGER"),
        ("cases", "case_status_raw", "TEXT"),
        ("cases", "case_sub_stage", "TEXT"),
        ("cases", "case_stage", "TEXT"),
        ("cases", "next_hearing_date", "TEXT"),
        ("cases", "national_court_code", "TEXT"),
        ("hearings", "hearing_seq", "INTEGER"),
        ("hearings", "vb_court_code", "TEXT"),
        ("hearings", "vb_dist_code", "TEXT"),
        ("hearings", "vb_nextdate", "TEXT"),
        ("hearings", "vb_cino", "TEXT"),
        ("hearings", "vb_state_code", "TEXT"),
        ("hearings", "vb_disposal_flag", "TEXT"),
        ("hearings", "vb_business_date", "TEXT"),
        ("hearings", "vb_court_no", "TEXT"),
        ("hearings", "vb_national_court_code", "TEXT"),
        ("hearings", "vb_search_by", "TEXT"),
        ("hearings", "vb_srno", "TEXT"),
        ("hearings", "vb_args_json", "TEXT"),
        ("orders", "order_section", "TEXT"),
        ("orders", "order_category", "TEXT"),
        ("orders", "order_description", "TEXT"),
        ("orders", "pdf_args_json", "TEXT"),
    ]),
]

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
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);")
        applied = {r[0] for r in cur.execute("SELECT version FROM schema_version;").fetchall()}
        for version, ddls, columns in MIGRATIONS:
            for ddl in ddls:
                cur.execute(ddl)
            for table, col, decl in columns:
                existing = {r[1] for r in cur.execute(f"PRAGMA table_info({table});").fetchall()}
                if col not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")
            if version not in applied:
                cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?);", (version, _now()))
        conn.commit()

    # ------------------------------------------------------------ raw / audit
    def save_raw_response(self, endpoint: str, request_params: Dict[str, Any], body: Any,
                          http_status: Optional[int], cnr_number: Optional[str] = None) -> int:
        raw = bytes(body) if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
        params = {k: ("<redacted>" if k in REDACTED_PARAM_KEYS else v) for k, v in (request_params or {}).items()}
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO raw_responses (endpoint, request_params_json, http_status, fetched_at, body_sha256, body, cnr_number) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                (endpoint, json.dumps(params, sort_keys=True), http_status, _now(),
                 hashlib.sha256(raw).hexdigest(), gzip.compress(raw), cnr_number),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_raw_response(self, raw_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM raw_responses WHERE id = ?;", (raw_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["body"] = gzip.decompress(d["body"]).decode("utf-8", errors="replace")
        return d

    def has_raw_response(self, cnr_number: str, endpoint: str = "home/viewHistory") -> bool:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT 1 FROM raw_responses WHERE cnr_number = ? AND endpoint = ? LIMIT 1;",
                (cnr_number, endpoint),
            ).fetchone() is not None

    def record_error(self, run_id: Optional[str], endpoint: Optional[str], cnr_number: Optional[str],
                     error_class: str, message: str, http_status: Optional[int] = None,
                     raw_response_id: Optional[int] = None) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO fetch_errors (run_id, endpoint, cnr_number, error_class, message, http_status, raw_response_id, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                (run_id, endpoint, cnr_number, error_class, message, http_status, raw_response_id, _now()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_errors(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if run_id is None:
                rows = conn.execute("SELECT * FROM fetch_errors ORDER BY id ASC;").fetchall()
            else:
                rows = conn.execute("SELECT * FROM fetch_errors WHERE run_id = ? ORDER BY id ASC;", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def create_search_run(self, **fields: Any) -> int:
        cols = list(fields)
        with self._get_conn() as conn:
            cur = conn.execute(
                f"INSERT INTO search_runs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)});",
                [fields[c] for c in cols],
            )
            conn.commit()
            return int(cur.lastrowid)

    def finish_search_run(self, search_run_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE search_runs SET {', '.join(c + ' = ?' for c in fields)} WHERE id = ?;",
                [*fields.values(), search_run_id],
            )
            conn.commit()

    def add_search_results(self, run_id: int, rows: List[Dict[str, Any]]) -> None:
        with self._get_conn() as conn:
            for r in rows:
                conn.execute(
                    "INSERT INTO search_results (search_run_id, sr_no, case_number, petitioner, respondent, court_name, cnr_number, view_params_json, raw_cells_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (run_id, r.get("sr_no"), r.get("case_number"), r.get("petitioner"), r.get("respondent"),
                     r.get("court_name"), r.get("cnr_number"),
                     json.dumps(r.get("view_params"), sort_keys=True), json.dumps(r.get("raw_cells"))),
                )
            conn.commit()

    def get_latest_search_row(self, cnr_number: str) -> Optional[Dict[str, Any]]:
        """Latest persisted search_results row for a CNR (None if the case was never searched)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM search_results WHERE cnr_number = ? ORDER BY id DESC LIMIT 1;", (cnr_number,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        for src, dst in (("view_params_json", "view_params"), ("raw_cells_json", "raw_cells")):
            raw = d.pop(src, None)
            try:
                d[dst] = json.loads(raw) if raw is not None else None
            except (TypeError, ValueError):
                d[dst] = raw
        return d

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
                    court_complex_code, est_code, updated_at,
                    view_params_json, source_response_id, status_assumed,
                    case_status_raw, case_sub_stage, case_stage, next_hearing_date, national_court_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at = excluded.updated_at,
                    view_params_json = excluded.view_params_json,
                    source_response_id = excluded.source_response_id,
                    status_assumed = excluded.status_assumed,
                    case_status_raw = excluded.case_status_raw,
                    case_sub_stage = excluded.case_sub_stage,
                    case_stage = excluded.case_stage,
                    next_hearing_date = excluded.next_hearing_date,
                    national_court_code = excluded.national_court_code;
                """,
                (
                    case.cnr_number, case.case_type, case.case_number,
                    case.filing_number, case.filing_date, case.registration_number,
                    case.registration_date, case.efiling_number, case.efiling_date,
                    case.first_hearing_date, case.decision_date, case.case_status,
                    case.nature_of_disposal, case.court_number_judge, case.court_name,
                    case.state_code, case.dist_code, case.court_complex_code,
                    case.est_code, now,
                    case.view_params_json or None, case.source_response_id, 1 if case.status_assumed else 0,
                    case.case_status_raw, case.case_sub_stage, case.case_stage, case.next_hearing_date,
                    case.national_court_code
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
                    "INSERT INTO parties (case_cnr, type, name, advocate, party_seq) VALUES (?, ?, ?, ?, ?);",
                    (case.cnr_number, p.type, p.name, p.advocate, p.party_seq)
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
                    "INSERT INTO hearings (case_cnr, hearing_index, judge, business_date, hearing_date, purpose, "
                    "hearing_seq, vb_court_code, vb_dist_code, vb_nextdate, vb_cino, vb_state_code, vb_disposal_flag, vb_business_date, vb_court_no, vb_national_court_code, vb_search_by, vb_srno, vb_args_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (case.cnr_number, h.hearing_index, h.judge, h.business_date, h.hearing_date, h.purpose,
                     h.hearing_seq, *[getattr(h, "vb_" + k) for k in VB_FIELDS], h.vb_args_json)
                )

            # 8. Insert Orders
            for o in case.orders:
                cur.execute(
                    "INSERT INTO orders (case_cnr, order_number, order_date, order_details, pdf_params, "
                    "order_section, order_category, order_description, pdf_args_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (case.cnr_number, o.order_number, o.order_date, o.order_details, o.pdf_params,
                     o.order_section, o.order_category, o.order_description, o.pdf_args_json)
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
                PartyItem(type=r["type"], name=r["name"], advocate=r["advocate"] or "",
                          party_seq=r["party_seq"] or 0)
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
                HearingItem(hearing_index=r["hearing_index"], judge=r["judge"] or "", business_date=r["business_date"] or "", hearing_date=r["hearing_date"] or "", purpose=r["purpose"] or "",
                            hearing_seq=r["hearing_seq"] or 0, vb_args_json=r["vb_args_json"] or "",
                            **{"vb_" + k: r["vb_" + k] or "" for k in VB_FIELDS})
                for r in cur.execute("SELECT * FROM hearings WHERE case_cnr = ? ORDER BY hearing_index ASC;", (cnr_number,)).fetchall()
            ]
            orders = [
                OrderItem(order_number=r["order_number"] or "", order_date=r["order_date"] or "", order_details=r["order_details"] or "", pdf_params=r["pdf_params"] or "",
                          order_section=r["order_section"] or "", order_category=r["order_category"] or "",
                          order_description=r["order_description"] or "", pdf_args_json=r["pdf_args_json"] or "")
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
                status_assumed=bool(row["status_assumed"]),
                view_params_json=row["view_params_json"] or "",
                source_response_id=row["source_response_id"],
                case_status_raw=row["case_status_raw"] or "",
                case_sub_stage=row["case_sub_stage"],
                case_stage=row["case_stage"] or "",
                next_hearing_date=row["next_hearing_date"] or "",
                national_court_code=row["national_court_code"] or "",
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
