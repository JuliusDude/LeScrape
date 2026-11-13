"""CLI: python -m V2 run|export|ocr ..."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .config import Config, Location, SearchSpec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="V2", description="Autonomous eCourts case scraper")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="search once, then scrape every case with N parallel sessions")
    r.add_argument("--state", default="3")
    r.add_argument("--dist", default="20")
    r.add_argument("--complex", default="1030135")
    r.add_argument("--est", default="3")
    r.add_argument("--case-type", default="23^3")
    r.add_argument("--year", default="2023")
    r.add_argument("--status", default="Disposed", choices=["Disposed", "Pending", "Both"])
    r.add_argument("--limit", type=int, default=None, help="max cases to scrape (after resume filtering)")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--max-rps", type=float, default=1.0, help="global requests/second ceiling (with --auto-tune: "
                   "the top of the ramp)")
    r.add_argument("--auto-tune", action="store_true", help="start at --start-rps, +0.25 rps per 60 s without a "
                   "block, up to --max-rps; a block drops to 50%% and stays below it")
    r.add_argument("--start-rps", type=float, default=1.0)
    r.add_argument("--max-block-min", type=float, default=30.0, help="abort (exit 3) after this many blocked minutes")
    r.add_argument("--out", default="V2/output/run")
    r.add_argument("--resume", action="store_true", help="skip cases already complete in the DB")
    r.add_argument("--follow-main-matters", action="store_true")
    r.add_argument("--no-pdfs", action="store_true")
    r.add_argument("--no-raw", action="store_true", help="do not keep gzip raw responses")
    r.add_argument("--solver", default="easyocr", choices=["easyocr", "manual"])
    r.add_argument("--captcha-attempts", type=int, default=10)
    r.add_argument("--no-export", action="store_true")

    e = sub.add_parser("export", help="export an existing DB to JSON/CSV")
    e.add_argument("--out", required=True, help="run directory containing ecourts.db")

    o = sub.add_parser("ocr", help="solve one captcha image with the local OCR (diagnostic)")
    o.add_argument("image")
    return p


def exit_code(summary) -> int:
    """0 ok, 2 some cases incomplete, 3 aborted because the site kept blocking us (resume later)."""
    if summary.get("aborted"):
        return 3
    return 0 if summary["failed_final"] == 0 else 2


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "ocr":
        from .captcha import EasyOcrSolver
        with open(args.image, "rb") as f:
            print(EasyOcrSolver().solve(f.read()))
        return 0
    if args.cmd == "export":
        from .export import export_all
        from .store import Store
        st = Store(os.path.join(args.out, "ecourts.db"))
        print(json.dumps(export_all(st, args.out), indent=2))
        return 0

    from .captcha import make_solver
    from .export import export_all
    from .pipeline import Pipeline
    cfg = Config(max_rps=args.max_rps, workers=args.workers, limit=args.limit, resume=args.resume,
                 follow_main_matters=args.follow_main_matters, fetch_pdfs=not args.no_pdfs,
                 store_raw=not args.no_raw, captcha_attempts=args.captcha_attempts, auto_tune=args.auto_tune,
                 start_rps=args.start_rps, max_block_min=args.max_block_min)
    loc = Location(args.state, args.dist, args.complex, args.est)
    spec = SearchSpec(args.case_type, args.year, args.status)
    pipe = Pipeline(cfg, loc, spec, args.out, solver=make_solver(args.solver, cfg.captcha_length))
    summary = pipe.run()
    print(json.dumps(summary, indent=2))
    if not args.no_export:
        print(json.dumps(export_all(pipe.store, args.out)))
    return exit_code(summary)


if __name__ == "__main__":
    sys.exit(main())
