"""
ecourts.queue.worker
====================
Resilient task queue worker with atomic 4-state lifecycle transitions
(queued -> running -> completed / failed), in-band CAPTCHA rejection recovery,
exponential backoff with jitter, and watchdog reaper for abandoned leases.
"""

from __future__ import annotations

import logging
import os
import signal
import time
import traceback
from typing import Any, Dict, List, Optional
import uuid

from ecourts.core.captcha import BaseCaptchaSolver, CaptchaHandler, LocalOcrSolver
from ecourts.core.client import PacedSession
from ecourts.parser.case_details import CaseDetail, parse_case_detail
from ecourts.parser.hierarchy import complex_code_numeric
from ecourts.parser.search import (
    WrongCaptchaError,
    build_search_payload,
    parse_case_results,
)
from ecourts.storage.db import Database

log = logging.getLogger("ecourts.queue.worker")


class QueueWorker:
    """Worker process that continuously claims tasks from the database queue,

    executes court exploration, search, and deep case extraction workflows,
    and updates task states resiliently.
    """

    def __init__(
        self,
        db: Database,
        session: Optional[PacedSession] = None,
        solver: Optional[BaseCaptchaSolver] = None,
        worker_id: Optional[str] = None,
        max_captcha_retries: int = 3,
        lease_timeout_seconds: int = 300,
    ) -> None:
        self.db = db
        self.session = session or PacedSession(min_delay=2.0)
        self.solver = solver or LocalOcrSolver()
        self.captcha_handler = CaptchaHandler(self.session, self.solver)
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.max_captcha_retries = max_captcha_retries
        self.lease_timeout_seconds = lease_timeout_seconds
        self._stop_requested = False
        self._last_watchdog_run = 0.0

    def request_stop(self) -> None:
        """Signal the worker to gracefully complete current task and exit."""
        log.info("Stop requested for worker %s", self.worker_id)
        self._stop_requested = True

    def _setup_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGINT, lambda s, f: self.request_stop())
            signal.signal(signal.SIGTERM, lambda s, f: self.request_stop())
        except (ValueError, AttributeError):
            # Not in main thread or unsupported platform
            pass

    def _maybe_run_watchdog(self) -> None:
        now = time.monotonic()
        if now - self._last_watchdog_run > 60.0:
            self._last_watchdog_run = now
            try:
                self.db.reap_abandoned_tasks(self.lease_timeout_seconds)
            except Exception as exc:
                log.warning("Watchdog reaper encountered error: %s", exc)

    def process_one_task(self) -> bool:
        """Claims and executes a single task.

        Returns True if a task was claimed and processed, False if queue was empty.
        """
        self._maybe_run_watchdog()
        task = self.db.claim_next_task(self.worker_id)
        if not task:
            return False

        task_id = task["id"]
        task_type = task["task_type"]
        params = task.get("params", {})
        if not self.session.app_token:
            self.session.init_session()
        log.info("Claimed task #%d (%s) for worker %s", task_id, task_type, self.worker_id)

        try:
            results_count = self._execute_task(task_type, params)
            self.db.complete_task(task_id, results_count=results_count)
            log.info("Task #%d completed successfully (%d results)", task_id, results_count)
            return True
        except Exception as exc:
            tb = traceback.format_exc()
            log.error("Task #%d failed with error: %s", task_id, exc, exc_info=True)
            self.db.fail_task(task_id, error_message=str(exc), error_traceback=tb, retryable=True)
            return True

    def _execute_task(self, task_type: str, params: Dict[str, Any]) -> int:
        if task_type == "case_type_search":
            return self._handle_case_type_search(params)
        elif task_type == "detail_fetch":
            return self._handle_detail_fetch(params)
        elif task_type == "cnr_search":
            return self._handle_cnr_search(params)
        else:
            raise ValueError(f"Unknown task type: '{task_type}'")

    def _handle_case_type_search(self, params: Dict[str, Any]) -> int:
        """Executes full search by case type flow with in-band CAPTCHA recovery,

        persists cases, and optionally fetches case details.
        """
        state_code = str(params["state_code"])
        dist_code = str(params["dist_code"])
        full_complex_code = str(params["court_complex_code"])
        court_complex_code = complex_code_numeric(full_complex_code)
        est_code = str(params["est_code"])
        case_type_value = str(params["case_type_value"])
        search_year = str(params["search_year"])
        case_status = params.get("case_status", "Pending")
        fetch_details = params.get("fetch_details", True)
        max_details = params.get("max_details", 25)

        # 1. Synchronize complex selection
        log.debug("Synchronizing complex selection (%s)...", full_complex_code)
        self.session.post("casestatus/set_data", data={
            "complex_code": full_complex_code,
            "selected_state_code": state_code,
            "selected_dist_code": dist_code,
            "selected_est_code": "",
        })

        # 2. Synchronize establishment selection
        log.debug("Synchronizing establishment selection (%s)...", est_code)
        self.session.post("casestatus/set_data", data={
            "complex_code": full_complex_code,
            "selected_state_code": state_code,
            "selected_dist_code": dist_code,
            "selected_est_code": est_code,
        })

        # 3. Submit search with in-band CAPTCHA retry loop
        cases: List[Dict[str, Any]] = []
        captcha_code, img_url = self.captcha_handler.get_and_solve_captcha()

        for attempt in range(1, self.max_captcha_retries + 1):
            search_payload = build_search_payload(
                state_code=state_code,
                dist_code=dist_code,
                court_complex_code=court_complex_code,
                est_code=est_code,
                case_type_value=case_type_value,
                search_year=search_year,
                case_status=case_status,
                captcha_code=captcha_code,
            )

            log.info(
                "Submitting search_by_case_type (attempt %d/%d, captcha='%s')",
                attempt,
                self.max_captcha_retries,
                captcha_code,
            )
            resp = self.session.post("casestatus/submit_case_type", data=search_payload)

            try:
                cases = parse_case_results(resp.text)
                log.info("Found %d case rows from search", len(cases))
                break
            except WrongCaptchaError as wce:
                log.warning(
                    "CAPTCHA '%s' rejected on attempt %d/%d: %s",
                    captcha_code,
                    attempt,
                    self.max_captcha_retries,
                    wce,
                )
                if attempt < self.max_captcha_retries:
                    # In-band refresh: extract fresh CAPTCHA directly from rejection response
                    try:
                        captcha_code, img_url = self.captcha_handler.solve_from_response(resp.text)
                        continue
                    except Exception:
                        captcha_code, img_url = self.captcha_handler.get_and_solve_captcha()
                        continue
                else:
                    raise

        # 4. Save cases and fetch details
        saved_count = 0
        for idx, item in enumerate(cases):
            if self._stop_requested:
                break

            view_params = item.get("view_params")
            cnr = item.get("cnr_number") or (view_params.get("cino") if view_params else None)

            # Deep detail fetch if requested and within max_details limit
            detail_obj: Optional[CaseDetail] = None
            if fetch_details and view_params and (max_details is None or idx < max_details):
                try:
                    detail_resp = self.session.post("home/viewHistory", data=view_params)
                    detail_obj = parse_case_detail(detail_resp.text)
                    if not cnr and detail_obj.cnr_number:
                        cnr = detail_obj.cnr_number
                except Exception as exc:
                    log.warning("Failed to fetch detail for case %s: %s", item.get("case_number"), exc)

            if not cnr:
                log.warning("Skipping case row #%s without CNR", item.get("sr_no"))
                continue

            # Build case dictionary to persist
            if detail_obj:
                detail_dict = detail_obj.to_dict()
                detail_dict.setdefault("court_name", item.get("court_name"))
                detail_dict.setdefault("state_code", state_code)
                detail_dict.setdefault("dist_code", dist_code)
                detail_dict.setdefault("court_complex_code", court_complex_code)
                detail_dict.setdefault("est_code", est_code)
                self.db.save_case(detail_dict)
            else:
                self.db.save_case({
                    "cnr_number": cnr,
                    "case_number": item.get("case_number", ""),
                    "court_name": item.get("court_name", ""),
                    "state_code": state_code,
                    "dist_code": dist_code,
                    "court_complex_code": court_complex_code,
                    "est_code": est_code,
                    "status": case_status,
                    "parties": [
                        {"type": "petitioner", "name": item.get("petitioner", "")},
                        {"type": "respondent", "name": item.get("respondent", "")},
                    ],
                })
            saved_count += 1

        return saved_count

    def _handle_detail_fetch(self, params: Dict[str, Any]) -> int:
        view_params = params.get("view_params", params)
        resp = self.session.post("home/viewHistory", data=view_params)
        detail = parse_case_detail(resp.text)
        if not detail.cnr_number:
            cnr = view_params.get("cino") or view_params.get("cnr_number")
            detail.cnr_number = cnr or ""

        if not detail.cnr_number:
            raise ValueError("home/viewHistory response did not contain a valid CNR number")

        self.db.save_case(detail)
        return 1

    def _handle_cnr_search(self, params: Dict[str, Any]) -> int:
        cnr_number = params.get("cnr_number") or params.get("cino")
        if not cnr_number:
            raise ValueError("cnr_search requires 'cnr_number' parameter")

        view_params = params.get("view_params")
        if view_params:
            return self._handle_detail_fetch(view_params)

        # Basic CNR record persistence if view_params not yet available
        self.db.save_case({
            "cnr_number": cnr_number,
            "case_number": params.get("case_number", ""),
            "court_name": params.get("court_name", ""),
            "state_code": params.get("state_code", ""),
            "dist_code": params.get("dist_code", ""),
        })
        return 1

    def run(
        self,
        once: bool = False,
        poll_interval: float = 1.0,
        max_tasks: Optional[int] = None,
    ) -> int:
        """Main worker loop.

        Runs until stop requested, or until queue is empty if once=True.
        Returns total number of tasks processed.
        """
        self._setup_signal_handlers()
        log.info("QueueWorker started (id: %s, once: %s)", self.worker_id, once)

        processed = 0
        while not self._stop_requested:
            claimed = self.process_one_task()
            if claimed:
                processed += 1
                if max_tasks and processed >= max_tasks:
                    log.info("Reached maximum tasks limit (%d)", max_tasks)
                    break
            else:
                if once:
                    log.info("Queue empty and once=True; exiting worker loop")
                    break
                time.sleep(poll_interval)

        log.info("QueueWorker stopped. Total tasks processed: %d", processed)
        return processed
