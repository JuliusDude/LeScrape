"""Local browser interface for V2. Start from the repository with python -m webapp.server."""
import base64
import logging
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from V2.config import Config, Location, SearchSpec
from V2.export import export_all
from V2.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / "V2" / "output"
app = Flask(__name__, static_folder=None)
app.config.update(MAX_CONTENT_LENGTH=8192, TRUSTED_HOSTS=["localhost", "127.0.0.1"])
from .results import library
app.register_blueprint(library)
from .catalog import catalog
app.add_url_rule("/api/options/<level>", view_func=catalog)
lock = threading.Condition()
job = {"state": "Ready"}
active = {"Starting", "Running", "Awaiting CAPTCHA", "Choose limit", "Exporting"}


@app.before_request
def local_requests():
    if request.method == "POST" and (not request.is_json or request.headers.get("Origin") != request.host_url.rstrip("/")):
        abort(403)


def display_reason(message):
    # Upstream exceptions may contain raw portal responses, URLs and session credentials.
    text = str(message).split(": {", 1)[0]
    text = re.sub(r"https?://[^\s]+", "[portal URL]", text)
    text = re.sub(r"(?i)(token|cookie|captcha_code)\s*[:=]\s*[^\s,]+", r"\1=[hidden]", text)
    return " ".join(text.split())[:400]


def issue(level, stage, message, cnr=None):
    with lock:
        events = job.setdefault("events", [])
        job["eventCount"] = job.get("eventCount", 0) + 1
        events.append(dict(id=job["eventCount"], at=time.time(), level=level, stage=stage,
                           message=display_reason(message), cnr=cnr))
        del events[:-100]


class RunLog(logging.Handler):
    stages = {"http":"Portal connection", "ratelimit":"Portal request limit",
              "captcha":"CAPTCHA", "pipeline":"Case capture"}

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        message = record.getMessage()
        cnr = re.search(r"\b[A-Z]{4}[0-9]{12}\b", message)
        issue("Error" if record.levelno >= logging.ERROR else "Warning",
              self.stages.get(record.name.split(".")[-1], "Scraper"), message,
              cnr.group() if cnr else None)


class BrowserSolver:
    name = "manual"

    def solve(self, image):
        with lock:
            if job.get("challengeAnswered"):
                issue("Warning", "CAPTCHA", "The search requires another CAPTCHA. Enter the new challenge.")
            job.update(state="Awaiting CAPTCHA", image=base64.b64encode(image).decode(), challenge=secrets.token_hex(8), answer=None)
            lock.wait_for(lambda: job["answer"] is not None)
            answer = job.pop("answer")
            job.update(state="Running", image=None, challenge=None)
            return answer


class BrowserPipeline(Pipeline):
    def search(self, session):
        with lock:
            job["stage"] = "Portal search"
        result = super().search(session)
        available = len({row["cnr"] for row in result["rows"]
                         if row.get("cnr") and row.get("view_params")})
        with lock:
            job.update(available=available)
            if available:
                job.update(state="Choose limit", selection=secrets.token_hex(8))
                lock.wait_for(lambda: job.get("limit") is not None)
                self.cfg.limit = job["limit"]
        return result


    def _save(self, bundles):
        complete = super()._save(bundles)
        for bundle in bundles:
            case = bundle["case"]
            cnr = case["cnr"]
            for warning in case.get("warnings", []):
                issue("Warning", "Case details", warning, cnr)
            for key, stage in (("hearings", "Hearing"), ("orders", "Order PDF"), ("interim_applications", "Interim application")):
                for item in case.get(key, []):
                    reason = item.get("error")
                    if not reason and key == "hearings" and item.get("fetched") and not (item.get("business") or {}).get("business_text"):
                        reason = "The hearing detail returned no extracted proceedings text. Review the source record."
                    if reason:
                        issue("Warning", stage, f"Entry {item.get('seq')}: {reason}", cnr)
            ack = bundle.get("ack") or {}
            if ack.get("error"):
                issue("Warning", "Acknowledgement", ack["error"], cnr)
            if self.outcome.get(cnr) in ("incomplete","failed") and bundle["meta"]["complete"]:
                issue("Recovered", "Case capture", "Case saved successfully on retry.", cnr)
        return complete


@app.post("/api/limit")
def select_limit():
    data = request.get_json()
    with lock:
        if not isinstance(data, dict) or job["state"] != "Choose limit" or data.get("selection") != job["selection"]:
            abort(409)
        value = data.get("limit")
        if type(value) is not int or not 1 <= value <= job["available"]:
            return jsonify(error=f"Choose between 1 and {job['available']} cases."), 400
        job.update(limit=value, state="Running", stage="Case capture")
        lock.notify_all()
    return jsonify(accepted=True)


def run(config, location, search, directory):
    pipeline = None
    logger = logging.getLogger("V2")
    handler = RunLog()
    logger.addHandler(handler)
    try:
        pipeline = BrowserPipeline(config, location, search, str(directory), solver=BrowserSolver())
        summary = pipeline.run()
        with lock:
            job.update(state="Exporting", stage="Export", summary=summary)
        if summary.get("aborted"):
            issue("Error", "Case capture", f"Capture stopped: {summary['aborted']}.")
        for cnr in summary.get("failed_cnrs", []):
            issue("Error", "Case capture", "Case still incomplete or pending at the end of capture.", cnr)
        export_all(pipeline.store, str(directory))
        state = "Complete" if not any(summary.get(k) for k in ("aborted", "failed_final", "incomplete_final")) else "Partial"
        with lock:
            job.update(state=state, ended=time.time())
    except Exception as exc:
        with lock:
            stage = job.get("stage", "Starting capture")
            reason = f"{stage} failed ({type(exc).__name__}): {display_reason(exc)}"
            issue("Error", stage, reason)
            job.update(state="Failed", error=reason, ended=time.time())
    finally:
        logger.removeHandler(handler)
        if pipeline:
            pipeline.store.close()


@app.post("/api/run")
def start():
    data = request.get_json()
    patterns = {"state": r"[0-9]{1,3}", "district": r"[0-9]{1,4}", "complex": r"[0-9]{1,12}",
                "establishment": r"[0-9]{0,5}", "type": r"[0-9]{1,5}(\^[0-9]{1,5})?",
                "year": r"[0-9]{4}", "status": r"Disposed|Pending|Both"}
    if not isinstance(data, dict) or any(not re.fullmatch(pattern, str(data.get(key, ""))) for key, pattern in patterns.items()):
        return jsonify(error="Check the court codes, year, status, and case limit."), 400
    with lock:
        if job["state"] in active:
            return jsonify(error="A run is already in progress."), 409
        directory = OUTPUT / ("web_" + time.strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3))
        config = Config(max_block_min=0)
        location = Location(*(str(data[k]) for k in ("state", "district", "complex", "establishment")))
        location.differ_mast_est = "N" if data.get("flag") == "N" else "Y"
        search = SearchSpec(data["type"], str(data["year"]), data["status"])
        job.clear()
        job.update(state="Starting", started=time.time(), directory=str(directory), limit=config.limit)
        threading.Thread(target=run, args=(config, location, search, directory), daemon=True).start()
    return jsonify(started=True), 202


@app.post("/api/captcha")
def answer():
    data = request.get_json()
    if not isinstance(data, dict) or not re.fullmatch(r"[a-zA-Z0-9]{6}", str(data.get("text", ""))):
        abort(400)
    with lock:
        if job["state"] != "Awaiting CAPTCHA" or data.get("challenge") != job["challenge"] or job.get("answer") is not None:
            abort(409)
        job["challengeAnswered"] = True
        job["answer"] = data["text"].lower()
        lock.notify_all()
    return jsonify(accepted=True)


@app.get("/api/status")
def status():
    with lock:
        result = {k: v for k, v in job.items() if k != "answer"}
    result["events"] = list(result.get("events", []))
    result.update(saved=0, complete=0)
    if result.get("directory"):
        db = Path(result["directory"]) / "ecourts.db"
        if db.exists():
            try:
                conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
                try:
                    result["saved"], result["complete"] = conn.execute("select count(*),coalesce(sum(complete),0) from cases").fetchone()
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                result["progressError"] = "Saved-case counts are temporarily unavailable. Retrying."
    return jsonify(result)


@app.get("/download/<name>")
def download(name):
    with lock:
        if name not in {"cases.csv", "cases.xlsx", "hearings.csv", "orders.csv"} or job["state"] not in {"Complete", "Partial"}:
            abort(404)
        directory = job["directory"]
    return send_from_directory(directory, name, as_attachment=True)


@app.get("/")
@app.get("/<path:asset>")
def frontend(asset="index.html"):
    return send_from_directory(ROOT / "dist", asset)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, threaded=True)
