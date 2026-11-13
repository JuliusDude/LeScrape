"""SQLite persistence (WAL, thread-safe via per-thread connections, idempotent upserts)."""
from __future__ import annotations

import contextlib
import gzip
import json
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, args_json TEXT, summary_json TEXT);
CREATE TABLE IF NOT EXISTS search_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, state_code TEXT, dist_code TEXT, complex_code TEXT,
  est_code TEXT, case_type TEXT, search_year TEXT, case_status TEXT, started_at TEXT, finished_at TEXT,
  attempts INTEGER, row_count INTEGER, total_reported INTEGER, outcome TEXT);
CREATE TABLE IF NOT EXISTS search_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, search_run_id INTEGER, sr_no TEXT, case_number TEXT, case_type TEXT,
  case_year TEXT, petitioner TEXT, respondent TEXT, court_name TEXT, cnr TEXT, view_args_json TEXT,
  UNIQUE(search_run_id, sr_no, cnr));
CREATE TABLE IF NOT EXISTS cases(
  cnr TEXT PRIMARY KEY, run_id TEXT, kind TEXT DEFAULT 'searched', parent_cnr TEXT,
  court_name TEXT, case_type_raw TEXT, case_type TEXT, case_number TEXT,
  filing_number TEXT, filing_date TEXT, filing_date_iso TEXT,
  registration_number TEXT, registration_date TEXT, registration_date_iso TEXT,
  efiling_number TEXT, efiling_date TEXT, efiling_date_iso TEXT,
  first_hearing_date TEXT, first_hearing_date_iso TEXT, decision_date TEXT, decision_date_iso TEXT,
  next_hearing_date TEXT, next_hearing_date_iso TEXT, case_stage TEXT,
  case_status TEXT, case_status_raw TEXT, case_sub_stage TEXT, nature_of_disposal TEXT,
  court_number_and_judge TEXT, national_court_code TEXT, ack_url TEXT,
  search_case_number TEXT, search_parties TEXT,
  details_extra_json TEXT, status_extra_json TEXT, extra_sections_json TEXT, warnings_json TEXT,
  vb_links INTEGER, vb_fetched INTEGER, pdf_links INTEGER, pdf_saved INTEGER,
  ack_expected INTEGER, ack_fetched INTEGER, complete INTEGER DEFAULT 0, missing_json TEXT, flags_json TEXT,
  elapsed_s REAL, requests INTEGER, scraped_at TEXT);
CREATE TABLE IF NOT EXISTS parties(
  cnr TEXT, role TEXT, seq INTEGER, number TEXT, name TEXT, advocate TEXT, advocates_json TEXT,
  extra_lines_json TEXT, PRIMARY KEY(cnr, role, seq));
CREATE TABLE IF NOT EXISTS acts(cnr TEXT, seq INTEGER, act TEXT, section TEXT, section_raw TEXT,
  PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS processes(cnr TEXT, seq INTEGER, process_id TEXT, title TEXT, date TEXT,
  date_iso TEXT, PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS main_matters(cnr TEXT, seq INTEGER, case_no TEXT, case_no_raw TEXT, main_cnr TEXT,
  filing_no TEXT, filing_no_suspect INTEGER DEFAULT 0, extra_json TEXT, PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS hearings(
  cnr TEXT, seq INTEGER, judge TEXT, business_on_date TEXT, business_on_date_iso TEXT, hearing_date TEXT,
  hearing_date_iso TEXT, purpose TEXT, vb_args_json TEXT, business_text TEXT, next_purpose TEXT,
  next_hearing_date TEXT, next_hearing_date_iso TEXT, business_judge TEXT, business_extra_json TEXT,
  fetched INTEGER DEFAULT 0, error TEXT, PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS orders(
  cnr TEXT, seq INTEGER, section TEXT, category TEXT, number TEXT, order_date TEXT, order_date_iso TEXT,
  description TEXT, link_text TEXT, pdf_args_json TEXT, pdf_url TEXT, pdf_path TEXT, sha256 TEXT, size INTEGER,
  pages INTEGER, text TEXT, needs_ocr INTEGER, fetched INTEGER DEFAULT 0, error TEXT, PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS ack(cnr TEXT PRIMARY KEY, court_name TEXT, decree_holder TEXT, judgement_debtor TEXT,
  filing_number TEXT, fields_json TEXT, fetched INTEGER DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS interim_applications(
  cnr TEXT, seq INTEGER, ia_number TEXT, parties_json TEXT, filing_date TEXT, filing_date_iso TEXT,
  next_date TEXT, next_date_iso TEXT, next_purpose TEXT, ia_status TEXT, args_json TEXT, business_case_no TEXT,
  fetched INTEGER DEFAULT 0, error TEXT, PRIMARY KEY(cnr, seq));
CREATE TABLE IF NOT EXISTS ia_business(
  cnr TEXT, ia_seq INTEGER, seq INTEGER, ia_no TEXT, business_on_date TEXT, business_on_date_iso TEXT,
  hearing_date TEXT, hearing_date_iso TEXT, business TEXT, PRIMARY KEY(cnr, ia_seq, seq));
CREATE TABLE IF NOT EXISTS raw_responses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cnr TEXT, endpoint TEXT, params_json TEXT, status INTEGER,
  ts TEXT, body_gz BLOB);
CREATE INDEX IF NOT EXISTS ix_raw_cnr ON raw_responses(cnr);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cnr TEXT, endpoint TEXT, kind TEXT, message TEXT,
  http_status INTEGER, ts TEXT);
"""

CASE_COLS = [
    "court_name", "case_type_raw", "case_type", "case_number", "filing_number", "filing_date", "filing_date_iso",
    "registration_number", "registration_date", "registration_date_iso", "efiling_number", "efiling_date",
    "efiling_date_iso", "first_hearing_date", "first_hearing_date_iso", "decision_date", "decision_date_iso",
    "next_hearing_date", "next_hearing_date_iso", "case_stage", "case_status", "case_status_raw",
    "case_sub_stage", "nature_of_disposal", "court_number_and_judge", "national_court_code", "ack_url"]
CHILD_TABLES = ["parties", "acts", "processes", "main_matters", "hearings", "orders", "ack",
                "interim_applications", "ia_business"]
# additive columns on pre-existing DBs (guarded ALTER in Store.__init__)
CASES_EXTRA_COLS = {"ia_links": "INTEGER", "ia_fetched": "INTEGER"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._conns: List[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(cases)")}
        for col, typ in CASES_EXTRA_COLS.items():
            if col not in have:
                with contextlib.suppress(sqlite3.OperationalError):   # another process added it first
                    self.conn.execute(f"ALTER TABLE cases ADD COLUMN {col} {typ}")

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=60000")
            self._local.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        with self._conns_lock:
            for c in self._conns:
                with contextlib.suppress(Exception):
                    c.close()
            self._conns.clear()
        self._local = threading.local()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ runs / search
    def start_run(self, run_id: str, args: Dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO runs(run_id,started_at,args_json) VALUES(?,?,?)",
                      (run_id, now(), j(args)))

    def finish_run(self, run_id: str, summary: Dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("UPDATE runs SET finished_at=?, summary_json=? WHERE run_id=?", (now(), j(summary), run_id))

    def add_search(self, run_id: str, loc: Dict[str, Any], spec: Dict[str, Any], attempts: int, total: Optional[int],
                   rows: List[Dict[str, Any]], outcome: str = "ok") -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO search_runs(run_id,state_code,dist_code,complex_code,est_code,case_type,search_year,"
                "case_status,started_at,finished_at,attempts,row_count,total_reported,outcome) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, loc.get("state_code"), loc.get("dist_code"), loc.get("complex_code"), loc.get("est_code"),
                 spec.get("case_type"), spec.get("year"), spec.get("status"), now(), now(), attempts, len(rows),
                 total, outcome))
            sid = cur.lastrowid
            c.executemany(
                "INSERT OR REPLACE INTO search_results(search_run_id,sr_no,case_number,case_type,case_year,"
                "petitioner,respondent,court_name,cnr,view_args_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(sid, r.get("sr_no"), r.get("case_number"), r.get("case_type"), r.get("case_year"),
                  r.get("petitioner"), r.get("respondent"), r.get("court_name"), r.get("cnr"),
                  j(r.get("view_args"))) for r in rows])
        return sid

    def record_error(self, run_id: str, cnr: Optional[str], endpoint: str, kind: str, message: str,
                     http_status: Optional[int] = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO errors(run_id,cnr,endpoint,kind,message,http_status,ts) VALUES(?,?,?,?,?,?,?)",
                      (run_id, cnr, endpoint, kind, str(message)[:2000], http_status, now()))

    # ------------------------------------------------------------------ cases
    def completed_cnrs(self, kind: str = "searched") -> set:
        rows = self.conn.execute("SELECT cnr FROM cases WHERE complete=1 AND kind=?", (kind,)).fetchall()
        return {r[0] for r in rows}

    def has_case(self, cnr: str) -> bool:
        return self.conn.execute("SELECT 1 FROM cases WHERE cnr=?", (cnr,)).fetchone() is not None

    def save_case(self, run_id: str, bundle: Dict[str, Any]) -> None:
        """Replace everything stored for this CNR in ONE transaction (idempotent)."""
        case, meta = bundle["case"], bundle.get("meta", {})
        cnr = case["cnr"]
        hearings, orders = case["hearings"], case["orders"]
        ack = bundle.get("ack")
        ias = case.get("interim_applications") or []
        with self.tx() as c:
            for t in CHILD_TABLES:
                c.execute(f"DELETE FROM {t} WHERE cnr=?", (cnr,))
            c.execute("DELETE FROM raw_responses WHERE cnr=?", (cnr,))
            vals = {k: case.get(k) for k in CASE_COLS}
            vals.update(
                cnr=cnr, run_id=run_id, kind=meta.get("kind", "searched"), parent_cnr=meta.get("parent_cnr"),
                search_case_number=meta.get("search_case_number"), search_parties=meta.get("search_parties"),
                details_extra_json=j(case["details_extra"]), status_extra_json=j(case["status_extra"]),
                extra_sections_json=j(case["extra_sections"]), warnings_json=j(case["warnings"]),
                vb_links=sum(1 for h in hearings if h["vb_args"]),
                vb_fetched=sum(1 for h in hearings if h.get("fetched")),
                ia_links=sum(1 for i in ias if i["args"]), ia_fetched=sum(1 for i in ias if i.get("fetched")),
                pdf_links=sum(1 for o in orders if o["pdf_args"]),
                pdf_saved=sum(1 for o in orders if o.get("fetched")),
                ack_expected=1 if case.get("ack_url") else 0, ack_fetched=1 if ack and ack.get("fetched") else 0,
                complete=1 if meta.get("complete") else 0, missing_json=j(meta.get("missing", [])),
                flags_json=j(meta.get("flags", {})), elapsed_s=meta.get("elapsed_s"), requests=meta.get("requests"),
                scraped_at=now())
            cols = list(vals)
            c.execute(f"INSERT OR REPLACE INTO cases({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [vals[k] for k in cols])
            for role, key in (("petitioner", "petitioners"), ("respondent", "respondents")):
                for p in case[key]:
                    c.execute("INSERT OR REPLACE INTO parties VALUES(?,?,?,?,?,?,?,?)",
                              (cnr, role, p["seq"], p["number"], p["name"], p["advocate"], j(p["advocates"]),
                               j(p["extra_lines"])))
            for a in case["acts"]:
                c.execute("INSERT OR REPLACE INTO acts VALUES(?,?,?,?,?)",
                          (cnr, a["seq"], a["act"], a["section"], a["section_raw"]))
            for p in case["processes"]:
                c.execute("INSERT OR REPLACE INTO processes VALUES(?,?,?,?,?,?)",
                          (cnr, p["seq"], p["process_id"], p["title"], p["date"], p["date_iso"]))
            for m in case["main_matters"]:
                c.execute("INSERT OR REPLACE INTO main_matters VALUES(?,?,?,?,?,?,?,?)",
                          (cnr, m["seq"], m["case_no"], m["case_no_raw"], m["cnr"], m["filing_no"],
                           1 if m.get("filing_no_suspect") else 0, j(m["extra"])))
            for h in hearings:
                b = h.get("business") or {}
                c.execute("INSERT OR REPLACE INTO hearings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cnr, h["seq"], h["judge"], h["business_on_date"], h["business_on_date_iso"],
                           h["hearing_date"], h["hearing_date_iso"], h["purpose"], j(h["vb_args"]),
                           b.get("business_text"), b.get("next_purpose"), b.get("next_hearing_date"),
                           b.get("next_hearing_date_iso"), b.get("judge"), j(b.get("extra", {})) if b else None,
                           1 if h.get("fetched") else 0, h.get("error")))
            for ia in ias:
                b = ia.get("business") or {}
                c.execute("INSERT OR REPLACE INTO interim_applications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cnr, ia["seq"], ia["ia_number"], j(ia["parties"]), ia["filing_date"],
                           ia["filing_date_iso"], ia["next_date"], ia["next_date_iso"], ia["next_purpose"],
                           ia["ia_status"], j(ia["args"]), b.get("case_no"), 1 if ia.get("fetched") else 0,
                           ia.get("error")))
                for r in b.get("rows", []):
                    c.execute("INSERT OR REPLACE INTO ia_business VALUES(?,?,?,?,?,?,?,?,?)",
                              (cnr, ia["seq"], r["seq"], r["ia_no"], r["business_on_date"],
                               r["business_on_date_iso"], r["hearing_date"], r["hearing_date_iso"], r["business"]))
            for o in orders:
                p = o.get("pdf") or {}
                c.execute("INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cnr, o["seq"], o["section"], o["category"], o["number"], o["order_date"],
                           o["order_date_iso"], o["description"], o["link_text"], j(o["pdf_args"]),
                           o.get("pdf_url"), p.get("path"), p.get("sha256"), p.get("size"), p.get("pages"),
                           p.get("text"), p.get("needs_ocr"), 1 if o.get("fetched") else 0, o.get("error")))
            if ack:
                c.execute("INSERT OR REPLACE INTO ack VALUES(?,?,?,?,?,?,?,?)",
                          (cnr, ack.get("court_name"), ack.get("decree_holder"), ack.get("judgement_debtor"),
                           ack.get("filing_number"), j(ack.get("fields", {})), 1 if ack.get("fetched") else 0,
                           ack.get("error")))
            for r in bundle.get("raw", []):
                c.execute("INSERT INTO raw_responses(run_id,cnr,endpoint,params_json,status,ts,body_gz) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (run_id, cnr, r["endpoint"], j(r.get("params")), r.get("status"), r.get("ts") or now(),
                           gzip.compress((r.get("body") or "").encode("utf-8"), 3)))

    def flag_suspect_main_filing(self) -> List[str]:
        """'Main Filing No.' is a session-scoped site artefact: the same value across different main CNRs
        is flagged (kept verbatim, filing_no_suspect=1)."""
        with self.tx() as c:
            vals = [r[0] for r in c.execute(
                "SELECT filing_no FROM main_matters WHERE filing_no<>'' GROUP BY filing_no "
                "HAVING COUNT(DISTINCT main_cnr)>1 AND COUNT(DISTINCT cnr)>1").fetchall()]
            c.execute("UPDATE main_matters SET filing_no_suspect=0")
            for v in vals:
                c.execute("UPDATE main_matters SET filing_no_suspect=1 WHERE filing_no=?", (v,))
        return vals

    # ------------------------------------------------------------------ read back
    def counts(self) -> Dict[str, int]:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ["cases", "parties", "acts", "processes", "main_matters", "hearings", "orders", "ack",
                          "interim_applications", "ia_business", "raw_responses", "errors", "search_results", "search_runs", "runs"]}

    def case_cnrs(self, kind: Optional[str] = None) -> List[str]:
        if kind:
            rows = self.conn.execute("SELECT cnr FROM cases WHERE kind=? ORDER BY cnr", (kind,))
        else:
            rows = self.conn.execute("SELECT cnr FROM cases ORDER BY cnr")
        return [r[0] for r in rows.fetchall()]

    def get_raw(self, cnr: str, endpoint: Optional[str] = None) -> List[Dict[str, Any]]:
        q, a = "SELECT * FROM raw_responses WHERE cnr=?", [cnr]
        if endpoint:
            q += " AND endpoint=?"
            a.append(endpoint)
        return [dict(endpoint=r["endpoint"], params=json.loads(r["params_json"] or "null"), status=r["status"],
                     body=gzip.decompress(r["body_gz"]).decode("utf-8")) for r in self.conn.execute(q, a)]

    def get_case(self, cnr: str) -> Optional[Dict[str, Any]]:
        """Nested dict with every stored fact about the case (used by export and tests)."""
        c = self.conn
        row = c.execute("SELECT * FROM cases WHERE cnr=?", (cnr,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("details_extra_json", "status_extra_json", "extra_sections_json", "warnings_json",
                  "missing_json", "flags_json"):
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        parties = [dict(r) for r in c.execute("SELECT * FROM parties WHERE cnr=? ORDER BY role DESC, seq", (cnr,))]
        for p in parties:
            p["advocates"] = json.loads(p.pop("advocates_json") or "[]")
            p["extra_lines"] = json.loads(p.pop("extra_lines_json") or "[]")
        d["petitioners"] = [p for p in parties if p["role"] == "petitioner"]
        d["respondents"] = [p for p in parties if p["role"] == "respondent"]
        for t in ("acts", "processes"):
            d[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t} WHERE cnr=? ORDER BY seq", (cnr,))]
        d["main_matters"] = [dict(r) for r in c.execute("SELECT * FROM main_matters WHERE cnr=? ORDER BY seq", (cnr,))]
        for m in d["main_matters"]:
            m["extra"] = json.loads(m.pop("extra_json") or "{}")
        d["hearings"] = [dict(r) for r in c.execute("SELECT * FROM hearings WHERE cnr=? ORDER BY seq", (cnr,))]
        for h in d["hearings"]:
            h["vb_args"] = json.loads(h.pop("vb_args_json") or "null")
            h["business_extra"] = json.loads(h.pop("business_extra_json") or "null")
        d["interim_applications"] = [dict(r) for r in c.execute(
            "SELECT * FROM interim_applications WHERE cnr=? ORDER BY seq", (cnr,))]
        for ia in d["interim_applications"]:
            ia["parties"] = json.loads(ia.pop("parties_json") or "[]")
            ia["args"] = json.loads(ia.pop("args_json") or "null")
            ia["business"] = [dict(r) for r in c.execute(
                "SELECT * FROM ia_business WHERE cnr=? AND ia_seq=? ORDER BY seq", (cnr, ia["seq"]))]
        d["orders"] = [dict(r) for r in c.execute("SELECT * FROM orders WHERE cnr=? ORDER BY seq", (cnr,))]
        for o in d["orders"]:
            o["pdf_args"] = json.loads(o.pop("pdf_args_json") or "null")
        a = c.execute("SELECT * FROM ack WHERE cnr=?", (cnr,)).fetchone()
        if a:
            a = dict(a)
            a["fields"] = json.loads(a.pop("fields_json") or "{}")
        d["ack"] = a
        return d
