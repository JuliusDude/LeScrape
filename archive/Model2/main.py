"""
eCourts Scraper — CLI
=======================

Workflow:
  1. explore  -> find state/district/complex/establishment/case-type codes
  2. enqueue  -> queue a search using those codes
  3. run      -> process queued jobs (solves CAPTCHA, fetches results)
  4. export   -> dump results to CSV/JSON

Example (Bengaluru City Civil Court, Criminal Cases, 2024, Pending):

  python main.py explore --state 3
      -> find dist_code for BENGALURU (e.g. 20)

  python main.py explore --state 3 --district 20
      -> find complex code, e.g. "1030135@3@Y" (City Civil Court Complex)

  python main.py explore --state 3 --district 20 --complex "1030135@3@Y"
      -> find establishment code, e.g. "3"

  python main.py explore --state 3 --district 20 --complex "1030135@3@Y" --establishment 3
      -> find case type code, e.g. "12^3" (C.C. - CRIMINAL CASES)

  python main.py enqueue --state 3 --district 20 --complex "1030135@3@Y" \\
      --establishment 3 --case-type "12^3" --year 2024 --status Pending

  python main.py run --captcha manual     # type the CAPTCHA yourself for testing
  python main.py export --format csv
"""

import argparse
import json
import logging

from storage import init_db, enqueue_job, export_csv, export_json
from scraper import ECourtScraper
from parsers import complex_code_numeric

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ecourts")


def cmd_explore(args):
    scraper = ECourtScraper(captcha_mode="ocr", delay=2.0)

    if not args.district:
        districts = scraper.list_districts(args.state)
        print(f"\nDistricts for state {args.state} ({len(districts)}):")
        for d in districts:
            print(f"  [{d['code']}] {d['name']}")
        return

    if not args.complex:
        complexes = scraper.list_complexes(args.state, args.district)
        print(f"\nCourt complexes ({len(complexes)}):")
        for c in complexes:
            print(f"  [{c['code']}] {c['name']}")
        print("\n(Use the FULL value e.g. '1030135@3@Y' as --complex in the next step)")
        return

    court_complex_code = complex_code_numeric(args.complex)

    if not args.establishment:
        ests = scraper.list_establishments(args.state, args.district, court_complex_code)
        print(f"\nEstablishments ({len(ests)}):")
        for e in ests:
            print(f"  [{e['code']}] {e['name']}")
        return

    types = scraper.list_case_types(args.state, args.district, court_complex_code, args.establishment)
    print(f"\nCase types ({len(types)}):")
    for t in types:
        print(f"  [{t['code']}] {t['name']}")
    print("\n(Use the value e.g. '12^3' as --case-type in 'enqueue')")


def cmd_enqueue(args):
    init_db()
    params = {
        "state_code": args.state,
        "dist_code": args.district,
        "full_complex_code": args.complex,
        "est_code": args.establishment,
        "case_type_value": args.case_type,
        "search_year": args.year,
        "case_status": args.status,
    }
    job_id = enqueue_job("case_type", params)
    print(f"Queued job #{job_id}")
    print(json.dumps(params, indent=2))


def cmd_run(args):
    from worker import run_worker
    run_worker(captcha_mode=args.captcha, api_key=args.api_key, run_once=args.once)


def cmd_export(args):
    if args.format == "csv":
        export_csv(args.output or "ecourts_cases.csv")
    else:
        export_json(args.output or "ecourts_cases.json")


def main():
    parser = argparse.ArgumentParser(
        description="eCourts.gov.in Case Status Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_explore = sub.add_parser("explore", help="Drill down to find codes")
    p_explore.add_argument("--state", required=True, help="State code, e.g. 3 for Karnataka")
    p_explore.add_argument("--district", default="")
    p_explore.add_argument("--complex", default="", help="FULL complex code e.g. '1030135@3@Y'")
    p_explore.add_argument("--establishment", default="")
    p_explore.set_defaults(func=cmd_explore)

    p_enqueue = sub.add_parser("enqueue", help="Queue a case-type search")
    p_enqueue.add_argument("--state", required=True)
    p_enqueue.add_argument("--district", required=True)
    p_enqueue.add_argument("--complex", required=True, help="FULL complex code e.g. '1030135@3@Y'")
    p_enqueue.add_argument("--establishment", required=True)
    p_enqueue.add_argument("--case-type", required=True, help="e.g. '12^3'")
    p_enqueue.add_argument("--year", required=True)
    p_enqueue.add_argument("--status", default="Pending", choices=["Pending", "Disposed", "Both"])
    p_enqueue.set_defaults(func=cmd_enqueue)

    p_run = sub.add_parser("run", help="Process the job queue")
    p_run.add_argument("--captcha", choices=["ocr", "2captcha", "manual"], default="ocr")
    p_run.add_argument("--api-key", default="")
    p_run.add_argument("--once", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_export = sub.add_parser("export", help="Export scraped data")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--output", default="")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
