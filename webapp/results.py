"""Browse completed JSON exports without writing to the source run."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Blueprint, abort, jsonify, send_from_directory, url_for
from V2.view import render
from V2.store import Store

library = Blueprint("library", __name__)
OUTPUT = Path(__file__).resolve().parents[1] / "V2" / "output"


def runs():
    indexes = [*OUTPUT.glob("*/json/index.json"), *OUTPUT.glob("*/*/json/index.json")]
    return {"~".join(p.parent.parent.relative_to(OUTPUT).parts): p.parent
            for p in sorted(indexes, key=lambda path: path.stat().st_mtime, reverse=True)}


def folder(run):
    directory = runs().get(run)
    if directory is None:
        abort(404)
    return directory


@library.get("/api/results")
def list_runs():
    return jsonify([{"id": key, "name": key.replace("~", " / ")} for key in runs()])


@library.get("/api/results/<run>")
def list_cases(run):
    return jsonify(json.loads((folder(run) / "index.json").read_text(encoding="utf-8")))


@library.post("/api/results/<run>/save")
def save_run(run):
    source = folder(run).parent / "ecourts.db"
    if not source.is_file():
        return jsonify(error="The run database is missing."), 404
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
            finished = conn.execute("SELECT run_id FROM runs WHERE finished_at IS NOT NULL ORDER BY started_at, run_id").fetchall()
            if not finished or conn.execute("SELECT 1 FROM runs WHERE finished_at IS NULL LIMIT 1").fetchone():
                return jsonify(error="This run has not finished."), 409
        with closing(Store(str(OUTPUT / "case_library.sqlite3"))) as store:
            written = sum(store.publish_run(str(source), run_id) for (run_id,) in finished)
    except (sqlite3.Error, OSError):
        return jsonify(error="Could not finish saving to the shared database. You can retry this run."), 503
    return jsonify(written=written)


@library.get("/api/results/<run>/sheet")
def case_sheet(run):
    # A current web capture has a database before its export appears in runs().
    if re.fullmatch(r"web_\d{8}_\d{6}_[0-9a-f]{6}", run):
        directory = OUTPUT / run
    else:
        directory = folder(run).parent
    db = directory / "ecourts.db"
    if not db.is_file():
        if directory.is_dir() and not (directory / "json/index.json").exists():
            return jsonify([])
        abort(404)
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='cases'").fetchone():
            return jsonify([])
        rows = conn.execute("""
            WITH names AS (
                SELECT cnr, group_concat(CASE WHEN role='petitioner' THEN name END, ' | ') AS petitioners,
                       group_concat(CASE WHEN role='respondent' THEN name END, ' | ') AS respondents
                FROM (SELECT cnr, role, name FROM parties ORDER BY cnr, role, seq)
                GROUP BY cnr
            )
            SELECT c.cnr, c.case_number, c.case_type, c.case_status, n.petitioners, n.respondents,
                   c.court_name, c.court_number_and_judge, c.filing_number, c.filing_date_iso,
                   c.registration_number, c.registration_date_iso, c.next_hearing_date_iso,
                   date(c.scraped_at) AS capture_date, c.decision_date_iso, c.case_stage, c.nature_of_disposal,
                   (SELECT count(*) FROM hearings h WHERE h.cnr=c.cnr) AS hearings,
                   (SELECT count(*) FROM orders o WHERE o.cnr=c.cnr) AS orders, c.complete
            FROM cases c LEFT JOIN names n ON n.cnr=c.cnr ORDER BY c.cnr
        """)
        return jsonify([dict(row) for row in rows])


@library.get("/results/<run>/case/<cnr>")
def case(run, cnr):
    if not re.fullmatch(r"[A-Za-z0-9]{16}", cnr):
        abort(404)
    directory = folder(run)
    source = directory / (cnr + ".json")
    if not source.is_file():
        abort(404)
    data = json.loads(source.read_text(encoding="utf-8"))
    def pdf_url(order):
        digest = order.get("sha256") or ""
        if re.fullmatch(r"[a-fA-F0-9]{64}", digest) and (directory.parent / "pdfs" / digest[:2] / (digest + ".pdf")).is_file():
            return url_for("library.pdf", run=run, digest=digest)
        return ""
    return render(data, pdf_url=pdf_url)


@library.get("/results/<run>/pdf/<digest>")
def pdf(run, digest):
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        abort(404)
    return send_from_directory(folder(run).parent / "pdfs" / digest[:2], digest + ".pdf")
