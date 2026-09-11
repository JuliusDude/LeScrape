"""
ecourts.cli
===========
Command Line Interface for eCourts Scraper System:
  - explore: Discover court hierarchy (districts, complexes, establishments, case types)
  - enqueue: Add scraping tasks to persistent queue
  - run: Execute worker loop to process tasks
  - export: Generate structured JSON or CSV exports
  - status: Inspect database cases and task queue statistics
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from ecourts.core.captcha import (
    BaseCaptchaSolver,
    LocalOcrSolver,
    ManualInteractiveSolver,
    MockCaptchaSolver,
    ThirdPartyApiSolver,
)
from ecourts.core.client import PacedSession
from ecourts.export.exporter import Exporter
from ecourts.parser.hierarchy import (
    complex_code_numeric,
    parse_case_types,
    parse_complexes,
    parse_districts,
    parse_establishments,
)
from ecourts.queue.worker import QueueWorker
from ecourts.storage.db import Database
from ecourts.testing.har_replay import HarReplayAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ecourts.cli")


def _get_session(har_path: Optional[str] = None, pacing: float = 2.0) -> PacedSession:
    session = PacedSession(min_delay=pacing)
    if har_path:
        adapter = HarReplayAdapter(har_path)
        session.mount("https://services.ecourts.gov.in", adapter)
        session.mount("http://services.ecourts.gov.in", adapter)
        log.info("Mounted HarReplayAdapter using %s", har_path)
    else:
        session.init_session()
    return session


def _get_solver(mode: str, api_key: Optional[str] = None) -> BaseCaptchaSolver:
    m = mode.lower()
    if m == "manual":
        return ManualInteractiveSolver()
    elif m in ("2captcha", "anticaptcha", "thirdparty"):
        if not api_key:
            raise ValueError(f"CAPTCHA solver '{mode}' requires --api-key")
        return ThirdPartyApiSolver(api_key=api_key)
    elif m == "mock":
        return MockCaptchaSolver()
    else:
        return LocalOcrSolver()


# ── Command: explore ──────────────────────────────────────────────────────────


def cmd_explore(args: argparse.Namespace) -> int:
    session = _get_session(args.har, pacing=args.pacing)
    fmt = args.format.lower()

    if not args.state:
        print("Error: --state <state_code> is required for exploration.")
        return 1

    # 1. State -> District
    if not args.district:
        resp = session.post("casestatus/fillDistrict", data={"state_code": args.state})
        districts = parse_districts(resp.text)
        if fmt == "json":
            print(json.dumps(districts, indent=2))
        else:
            print(f"\nDistricts for State {args.state} ({len(districts)} found):")
            print(f"{'CODE':<10} {'NAME'}")
            print("-" * 40)
            for d in districts:
                print(f"{d['code']:<10} {d['name']}")
        return 0

    # 2. District -> Court Complex
    if not args.complex:
        resp = session.post("casestatus/fillcomplex", data={"state_code": args.state, "dist_code": args.district})
        complexes = parse_complexes(resp.text)
        if fmt == "json":
            print(json.dumps(complexes, indent=2))
        else:
            print(f"\nCourt Complexes for District {args.district} ({len(complexes)} found):")
            print(f"{'CODE':<35} {'NUMERIC':<10} {'NAME'}")
            print("-" * 65)
            for c in complexes:
                print(f"{c['code']:<35} {c['numeric_code']:<10} {c['name']}")
        return 0

    # 3. Court Complex -> Establishment
    numeric_complex = complex_code_numeric(args.complex)
    if not args.establishment:
        resp = session.post("casestatus/fillCourtEstablishment", data={
            "state_code": args.state,
            "dist_code": args.district,
            "court_complex_code": numeric_complex,
        })
        establishments = parse_establishments(resp.text)
        if fmt == "json":
            print(json.dumps(establishments, indent=2))
        else:
            print(f"\nEstablishments for Complex {numeric_complex} ({len(establishments)} found):")
            print(f"{'CODE':<10} {'NAME'}")
            print("-" * 45)
            for e in establishments:
                print(f"{e['code']:<10} {e['name']}")
        return 0

    # 4. Establishment -> Case Types
    resp = session.post("casestatus/fillCaseType", data={
        "state_code": args.state,
        "dist_code": args.district,
        "court_complex_code": numeric_complex,
        "est_code": args.establishment,
        "search_type": "c_type",
    })
    case_types = parse_case_types(resp.text)
    if fmt == "json":
        print(json.dumps(case_types, indent=2))
    else:
        print(f"\nCase Types for Establishment {args.establishment} ({len(case_types)} found):")
        print(f"{'TOKEN':<12} {'TYPE':<8} {'EST':<6} {'NAME'}")
        print("-" * 60)
        for ct in case_types:
            print(f"{ct['code']:<12} {ct['type_code']:<8} {ct['est_code']:<6} {ct['name']}")
    return 0


# ── Command: enqueue ──────────────────────────────────────────────────────────


def cmd_enqueue(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        # CNR lookup task
        if args.cnr:
            params = {"cnr_number": args.cnr}
            task_id = db.enqueue_task(
                task_type="cnr_search",
                params=params,
                priority=args.priority,
                max_retries=args.max_retries,
            )
            print(f"Successfully enqueued CNR lookup task #{task_id} for {args.cnr}")
            return 0

        # Case-type search task
        required = ["state", "district", "complex", "establishment", "case_type", "year"]
        missing = [r for r in required if not getattr(args, r, None)]
        if missing:
            print(f"Error: Missing required arguments for case search: {', '.join(missing)}")
            print("Required: --state, --district, --complex, --establishment, --case-type, --year (or --cnr)")
            return 1

        params = {
            "state_code": args.state,
            "dist_code": args.district,
            "court_complex_code": args.complex,
            "est_code": args.establishment,
            "case_type_value": args.case_type,
            "search_year": args.year,
            "case_status": args.status or "Pending",
            "fetch_details": not args.no_details,
            "max_details": args.max_details,
        }

        task_id = db.enqueue_task(
            task_type="case_type_search",
            params=params,
            priority=args.priority,
            max_retries=args.max_retries,
        )
        print(f"Successfully enqueued case_type_search task #{task_id}")
        return 0


# ── Command: run ──────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        session = _get_session(args.har, pacing=args.pacing)
        solver = _get_solver(args.captcha, api_key=args.api_key)

        worker = QueueWorker(
            db=db,
            session=session,
            solver=solver,
            max_captcha_retries=args.max_captcha_retries,
        )

        processed = worker.run(
            once=args.once,
            poll_interval=args.poll_interval,
            max_tasks=args.max_tasks,
        )
        print(f"Worker completed. Processed {processed} task(s).")
        return 0


# ── Command: export ───────────────────────────────────────────────────────────


def cmd_export(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        exporter = Exporter(db)

        output = args.output
        if not output:
            output = "export.csv" if args.format == "csv" else "export.json"

        res = exporter.export(
            format=args.format,
            output_path=output,
            mode=args.mode,
            status=args.status,
        )
        print(f"Export finished: {res}")
        return 0


# ── Command: status ───────────────────────────────────────────────────────────


def cmd_status(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        total_cases = db.get_cases_count()
        pending_cases = db.get_cases_count(status="Pending")
        disposed_cases = db.get_cases_count(status="Disposed")

        q_stats = db.get_queue_stats()

        print("\n=== eCourts Scraper System Status ===")
        print(f"Database: {os.path.abspath(args.db)}")
        print(f"Total Cases:     {total_cases}")
        print(f"  - Pending:     {pending_cases}")
        print(f"  - Disposed:    {disposed_cases}")
        print("\nQueue Tasks:")
        print(f"  - Total:       {q_stats['total']}")
        print(f"  - Queued:      {q_stats['queued']}")
        print(f"  - Running:     {q_stats['running']}")
        print(f"  - Completed:   {q_stats['completed']}")
        print(f"  - Failed:      {q_stats['failed']}")
        print("=====================================\n")
        return 0


# ── Main Entrypoint ───────────────────────────────────────────────────────────


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecourts",
        description="eCourts India Production Scraper & Resilient Queue System",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. explore
    p_explore = subparsers.add_parser("explore", help="Explore court hierarchy dropdowns")
    p_explore.add_argument("--state", help="State code (e.g. 3 for Karnataka)")
    p_explore.add_argument("--district", help="District code (e.g. 20 for Bengaluru)")
    p_explore.add_argument("--complex", help="Court complex token (e.g. 1030135@3@Y)")
    p_explore.add_argument("--establishment", help="Court establishment code (e.g. 3)")
    p_explore.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    p_explore.add_argument("--har", help="Path to offline HAR fixture file")
    p_explore.add_argument("--pacing", type=float, default=2.0, help="Inter-request pacing delay")

    # 2. enqueue
    p_enqueue = subparsers.add_parser("enqueue", help="Add search or lookup task to the queue")
    p_enqueue.add_argument("--db", default="ecourts.db", help="SQLite database path")
    p_enqueue.add_argument("--cnr", help="Direct 16-character CNR number lookup")
    p_enqueue.add_argument("--state", help="State code")
    p_enqueue.add_argument("--district", help="District code")
    p_enqueue.add_argument("--complex", help="Court complex token")
    p_enqueue.add_argument("--establishment", help="Establishment code")
    p_enqueue.add_argument("--case-type", help="Case type compound token (e.g. 12^3)")
    p_enqueue.add_argument("--year", help="Registration year (e.g. 2025)")
    p_enqueue.add_argument("--status", choices=["Pending", "Disposed", "Both"], default="Pending")
    p_enqueue.add_argument("--priority", type=int, default=0, help="Task priority")
    p_enqueue.add_argument("--max-retries", type=int, default=3, help="Max retry attempts")
    p_enqueue.add_argument("--no-details", action="store_true", help="Skip fetching deep case details")
    p_enqueue.add_argument("--max-details", type=int, default=25, help="Max details to fetch per search")

    # 3. run
    p_run = subparsers.add_parser("run", help="Run the worker to process queued tasks")
    p_run.add_argument("--db", default="ecourts.db", help="SQLite database path")
    p_run.add_argument(
        "--captcha",
        choices=["ocr", "manual", "2captcha", "mock"],
        default="ocr",
        help="CAPTCHA solver strategy",
    )
    p_run.add_argument("--api-key", help="API key for 3rd party solver (e.g. 2captcha)")
    p_run.add_argument("--once", action="store_true", help="Process until queue is empty, then exit")
    p_run.add_argument("--pacing", type=float, default=2.0, help="Minimum seconds between requests")
    p_run.add_argument("--poll-interval", type=float, default=1.0, help="Seconds to sleep when queue empty")
    p_run.add_argument("--max-tasks", type=int, default=None, help="Maximum tasks to execute before stopping")
    p_run.add_argument("--max-captcha-retries", type=int, default=3, help="Max retries on CAPTCHA rejection")
    p_run.add_argument("--har", help="Path to offline HAR fixture for offline testing")

    # 4. export
    p_export = subparsers.add_parser("export", help="Export scraped cases to CSV or JSON")
    p_export.add_argument("--db", default="ecourts.db", help="SQLite database path")
    p_export.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    p_export.add_argument(
        "--mode",
        choices=["flat", "relational"],
        default="flat",
        help="CSV export mode (flat denormalized or relational tables)",
    )
    p_export.add_argument("--output", help="Output file path (or directory if mode=relational)")
    p_export.add_argument("--status", choices=["Pending", "Disposed"], help="Filter by case status")

    # 5. status
    p_status = subparsers.add_parser("status", help="Show system and queue statistics")
    p_status.add_argument("--db", default="ecourts.db", help="SQLite database path")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "explore": cmd_explore,
        "enqueue": cmd_enqueue,
        "run": cmd_run,
        "export": cmd_export,
        "status": cmd_status,
    }

    handler = commands.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except Exception as exc:
        log.error("Command '%s' failed: %s", args.command, exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
