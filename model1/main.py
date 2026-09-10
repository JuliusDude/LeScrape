"""
eCourts Scraper — CLI & Usage Examples
========================================

Quick start:
    python main.py explore --state 24         # list districts for Maharashtra
    python main.py enqueue-cnr MHPU010123456  # queue a CNR lookup
    python main.py run --captcha manual        # run worker with manual CAPTCHA
    python main.py export --format csv
"""

import argparse
import json
import logging

from storage import init_db, enqueue_job, export_csv, export_json
from scraper import ECourtScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ecourts")

# ── Known State Codes (partial list) ──────────────────────────────────────────
STATE_CODES = {
    "1":  "Andhra Pradesh",
    "3":  "Assam",
    "4":  "Bihar",
    "5":  "Chhattisgarh",
    "8":  "Gujarat",
    "9":  "Haryana",
    "10": "Himachal Pradesh",
    "12": "Jharkhand",
    "13": "Karnataka",
    "14": "Kerala",
    "15": "Madhya Pradesh",
    "16": "Maharashtra",
    "20": "Odisha",
    "21": "Punjab",
    "22": "Rajasthan",
    "25": "Tamil Nadu",
    "26": "Telangana",
    "28": "Uttar Pradesh",
    "29": "Uttarakhand",
    "30": "West Bengal",
    "31": "Delhi",
}


def cmd_explore(args):
    """Explore the dropdown hierarchy for a state."""
    scraper = ECourtScraper(captcha_mode="ocr", delay=2.0)
    scraper.session.init()

    print(f"\n{'='*50}")
    print(f"Exploring state: {STATE_CODES.get(args.state, args.state)}")
    print(f"{'='*50}")

    districts = scraper.list_districts(args.state)
    print(f"\nDistricts ({len(districts)}):")
    for d in districts[:10]:
        print(f"  [{d['code']}] {d['name']}")

    if args.district:
        complexes = scraper.list_complexes(args.state, args.district)
        print(f"\nCourt Complexes ({len(complexes)}):")
        for c in complexes[:10]:
            print(f"  [{c['code']}] {c['name']}")

        if args.complex:
            ests = scraper.list_establishments(args.state, args.district, args.complex)
            print(f"\nEstablishments ({len(ests)}):")
            for e in ests:
                print(f"  [{e['code']}] {e['name']}")

            if args.establishment:
                types = scraper.list_case_types(
                    args.state, args.district, args.complex, args.establishment
                )
                print(f"\nCase Types ({len(types)}):")
                for t in types:
                    print(f"  [{t['code']}] {t['name']}")


def cmd_enqueue_cnr(args):
    """Queue one or more CNR lookups."""
    init_db()
    cnrs = args.cnr if isinstance(args.cnr, list) else [args.cnr]
    for cnr in cnrs:
        job_id = enqueue_job("cnr", {"cnr_number": cnr})
        print(f"Queued CNR lookup: {cnr} (job #{job_id})")


def cmd_enqueue_case_type(args):
    """Queue a case-type search."""
    init_db()
    params = {
        "state_code": args.state,
        "district_code": args.district,
        "complex_code": args.complex,
        "est_code": args.establishment,
        "case_type": args.case_type,
        "case_number": args.case_number,
        "year": args.year or "",
    }
    job_id = enqueue_job("case_type", params)
    print(f"Queued case-type search (job #{job_id})")
    print(f"Params: {json.dumps(params, indent=2)}")


def cmd_enqueue_party(args):
    """Queue a party name search."""
    init_db()
    params = {
        "state_code": args.state,
        "district_code": args.district,
        "complex_code": args.complex,
        "est_code": args.establishment,
        "party_name": args.name,
        "year": args.year or "",
    }
    job_id = enqueue_job("party_name", params)
    print(f"Queued party name search: '{args.name}' (job #{job_id})")


def cmd_run(args):
    """Start the worker."""
    from worker import run_worker
    run_worker(
        captcha_mode=args.captcha,
        api_key=args.api_key,
        run_once=args.once,
    )


def cmd_export(args):
    """Export scraped data."""
    if args.format == "csv":
        export_csv(args.output or "ecourts_cases.csv")
    else:
        export_json(args.output or "ecourts_cases.json")
    print(f"Exported to {args.output}")


# ── Argument Parser ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="eCourts.gov.in Case Status Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # See all districts in Karnataka (state 13)
  python main.py explore --state 13

  # Drill down to court complexes
  python main.py explore --state 13 --district 7

  # Queue a CNR lookup
  python main.py enqueue-cnr KARB010098762023

  # Queue a case type search
  python main.py enqueue-case-type --state 13 --district 7 \\
    --complex 5 --establishment 12 --case-type CRL --case-number 123 --year 2024

  # Queue a party name search
  python main.py enqueue-party --state 13 --district 7 \\
    --complex 5 --establishment 12 --name "Sharma"

  # Run the worker (manual CAPTCHA entry for testing)
  python main.py run --captcha manual

  # Run with 2captcha (production)
  python main.py run --captcha 2captcha --api-key YOUR_KEY_HERE

  # Export all scraped data
  python main.py export --format csv --output results.csv
        """
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # explore
    p_explore = sub.add_parser("explore", help="Explore dropdown hierarchy")
    p_explore.add_argument("--state", required=True)
    p_explore.add_argument("--district", default="")
    p_explore.add_argument("--complex", default="")
    p_explore.add_argument("--establishment", default="")
    p_explore.set_defaults(func=cmd_explore)

    # enqueue-cnr
    p_cnr = sub.add_parser("enqueue-cnr", help="Queue CNR lookup(s)")
    p_cnr.add_argument("cnr", nargs="+", help="One or more CNR numbers")
    p_cnr.set_defaults(func=cmd_enqueue_cnr)

    # enqueue-case-type
    p_ct = sub.add_parser("enqueue-case-type", help="Queue case type search")
    p_ct.add_argument("--state", required=True)
    p_ct.add_argument("--district", required=True)
    p_ct.add_argument("--complex", required=True)
    p_ct.add_argument("--establishment", required=True)
    p_ct.add_argument("--case-type", required=True)
    p_ct.add_argument("--case-number", required=True)
    p_ct.add_argument("--year", default="")
    p_ct.set_defaults(func=cmd_enqueue_case_type)

    # enqueue-party
    p_party = sub.add_parser("enqueue-party", help="Queue party name search")
    p_party.add_argument("--state", required=True)
    p_party.add_argument("--district", required=True)
    p_party.add_argument("--complex", required=True)
    p_party.add_argument("--establishment", required=True)
    p_party.add_argument("--name", required=True)
    p_party.add_argument("--year", default="")
    p_party.set_defaults(func=cmd_enqueue_party)

    # run
    p_run = sub.add_parser("run", help="Start worker")
    p_run.add_argument("--captcha", choices=["ocr", "2captcha", "manual"], default="ocr")
    p_run.add_argument("--api-key", default="")
    p_run.add_argument("--once", action="store_true")
    p_run.set_defaults(func=cmd_run)

    # export
    p_export = sub.add_parser("export", help="Export data")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--output", default="")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
