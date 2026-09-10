"""
Queue Worker
=============
Processes scrape_jobs from the database queue.
Safe to kill and restart — jobs resume from where they left off.

Job params (job_type="case_type") must contain:
    state_code, dist_code, full_complex_code, est_code,
    case_type_value, search_year, case_status

Usage:
    python worker.py                      # run continuously
    python worker.py --once               # process one job and exit
    python worker.py --captcha 2captcha --api-key XXXX
    python worker.py --captcha manual     # type CAPTCHA yourself (testing)
"""

import argparse
import time
import logging

from scraper import ECourtScraper
from storage import init_db, claim_next_job, complete_job, upsert_case

log = logging.getLogger("ecourts.worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def process_job(scraper: ECourtScraper, job: dict) -> int:
    """Process a single job. Returns number of results found."""
    job_type = job["job_type"]
    p = job["params"]

    if job_type == "case_type":
        cases = scraper.search_with_details(
            state_code=p["state_code"],
            dist_code=p["dist_code"],
            full_complex_code=p["full_complex_code"],
            est_code=p["est_code"],
            case_type_value=p["case_type_value"],
            search_year=p["search_year"],
            case_status=p.get("case_status", "Pending"),
            max_results=p.get("max_results", 10),
        )
    else:
        raise ValueError(f"Unknown job type: {job_type}")

    for case in cases:
        upsert_case(
            case,
            state_code=p.get("state_code", ""),
            dist_code=p.get("dist_code", ""),
        )

    return len(cases)


def run_worker(captcha_mode: str = "ocr", api_key: str = "",
               run_once: bool = False, idle_sleep: int = 30):
    init_db()
    scraper = ECourtScraper(captcha_mode=captcha_mode, api_key_2captcha=api_key)
    log.info(f"Worker started. CAPTCHA mode: {captcha_mode}")

    while True:
        job = claim_next_job()

        if not job:
            if run_once:
                log.info("No jobs in queue. Exiting.")
                break
            log.info(f"No pending jobs. Sleeping {idle_sleep}s...")
            time.sleep(idle_sleep)
            continue

        log.info(f"Processing job #{job['id']}: {job['job_type']} | params: {job['params']}")

        try:
            count = process_job(scraper, job)
            complete_job(job["id"], results_count=count)
        except Exception as e:
            log.error(f"Job #{job['id']} failed: {e}", exc_info=True)
            complete_job(job["id"], error=str(e))

        if run_once:
            break

        time.sleep(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="eCourts scrape worker")
    parser.add_argument("--captcha", choices=["ocr", "2captcha", "manual"], default="ocr")
    parser.add_argument("--api-key", default="", help="2captcha API key")
    parser.add_argument("--once", action="store_true", help="Process one job and exit")
    args = parser.parse_args()

    run_worker(captcha_mode=args.captcha, api_key=args.api_key, run_once=args.once)
