# V2: autonomous eCourts India case scraper

V2 is the second-generation scraper, written from scratch (formerly `alternate_tool`). For each case it captures everything the eCourts district-court portal shows:
- case details;
- all parties and advocates;
- acts and processes;
- main matters;
- interim applications (IA), plus each IA's own business detail;
- every hearing, plus that hearing's daily business text;
- every order and judgment, plus the PDF file and its full text;
- the acknowledgement page (Decree Holder / Judgement Debtor);
- the raw site responses.

Everything is stored in SQLite and exported to JSON and CSV. It runs unattended:
- one OCR-solved CAPTCHA per search, with automatic retry;
- retries and session recovery;
- automatic pausing and slow-down when the portal's firewall blocks.

V2 is standalone: it imports nothing from `V1` or `ecourts`.
- Dependencies: requests, bs4, lxml, Pillow, numpy, opencv, easyocr, pymupdf.
- openpyxl is optional (for `.xlsx`).

## Status (live-tested 2026-11-13 on services.ecourts.gov.in)

| Run | Cases | Complete | Blocks | s/case |
|---|---|---|---|---|
| live_run3 (2 workers, ~1.3 rps) | 10 | 10 | 0 | 19.5 |
| live_run4 (3 workers, auto-tune from 2 rps) | 30 | 30 | 1 (auto-recovered) | 25.4 (14.6 excluding the block) |
| live_run5 (2 workers, auto-tune 1.0–1.5 rps) | 20 | 20 | 0 | 14.2 |

Independent verification:
- Run3: 978 checks with 0 missing/wrong, and 11/11 PDFs' text matched.
- Run4: 2794 checks; the only 2 flags were checker miscounts.
- Ten cases scraped about 50 minutes apart came out identical, except the session-bound `pdf_url` and the "Main Filing No." site counter.

**Speed limit.**
- A case needs about 22 requests: one viewBusiness per hearing, plus the PDF and the acknowledgement page.
- The portal's IP firewall blocked at about 5 rps within 16 s, and at about 2.25 rps within about 1 min. Blocks last 5–11 min.
- Realistic speed is therefore **about 14–20 s per case**.
- Recommended settings: `--workers 2 --auto-tune --start-rps 1.0 --max-rps 1.5`.

**View one case** as a self-contained HTML page:

```
python -m V2.view --list --out V2/output/live_run5
python -m V2.view <CNR> --out V2/output/live_run5     # writes V2/output/live_run5/view/<CNR>.html
```

`V2/output/` contains real court PDFs and party names. It is git-ignored; do not commit it.

## Usage

```
python -m V2 run --state 3 --dist 20 --complex 1030135 --est 3 \
    --case-type 23^3 --year 2023 --status Disposed --limit 10 --workers 1 --max-rps 1.0 [--auto-tune --start-rps 1.0 --max-rps 3] [--max-block-min 30] \
    --out V2/output/run1 [--resume] [--follow-main-matters] [--no-pdfs] [--no-raw] [--solver manual]
python -m V2 export --out V2/output/run1     # re-export from ecourts.db
python -m V2 ocr path/to/captcha.png                     # diagnostic
python -m unittest discover -s V2/tests -t .             # offline tests (~15 s, includes one real-OCR test)
```

Output dir: `ecourts.db` (SQLite, WAL), `pdfs/<sha[:2]>/<sha>.pdf`, `summary.json` (throughput, retries, recoveries, failed CNRs),
`json/<CNR>.json` (nested, everything incl. hearing business text, PDF text and `interim_applications[].business`), `json/index.json`,
`cases.csv`, `hearings.csv`, `orders.csv`, `ia.csv` (one row per IA business entry), `parties.csv`, `cases.xlsx` (if openpyxl). Exit code 2 if any case is still incomplete.

## Architecture

* `http.py` - `EcourtsSession` (one per worker): keep-alive `requests.Session`, handshake
  (`casestatus/index` -> `components.js` -> `casestatus/set_data`), **dynamic** delimiter value / extra header name parsed from `ajaxCall`
  in `components.js`, token rotation (every JSON response carries the next `app_token`), retries with exponential backoff + jitter
  on connection errors / timeouts / 5xx / 429 (`Retry-After` honoured), and **session recovery**: WAF/"Security Page"/"Search Page not Found",
  403/405, non-JSON or token-less replies trigger a fresh cookie jar + full handshake + retry of the call.
* `ratelimit.py` - `RateLimiter` (slot reservation; global ceiling `--max-rps` across all workers), `Governor` (circuit breaker: error rate
  > 50% over the last 20 requests -> everybody pauses, concurrency halved, limiter interval doubled; gradual restore after 60 successes),
  `ConcurrencyGate`.
* `captcha.py` - easyocr (singleton reader, lowercase-alnum allowlist; raw image first, then upscaled / Otsu variants + majority vote),
  length/charset sanity check, pluggable solvers (`ManualSolver`, `CallableSolver`), generic `solve_with_retries`.
* `search.py` - getCaptcha -> image -> OCR -> `submit_case_type`; on rejection the fresh captcha shipped with the error is reused (saves a request).
* `parsers.py` - pure parsers: case page (viewHistory / viewCNRHistory), viewBusiness, acknowledgement, search table, JS-call args, ISO dates.
  Unknown `<h3>` sections go to `extra_sections`; unknown labels to `details_extra` / `status_extra`; nothing is dropped silently.
* `pdf.py` - saves PDFs by sha256, pymupdf text per page (form-feed separated), page count, `needs_ocr` when there is no text layer.
* `store.py` - SQLite; one transaction per case replaces all of its rows (idempotent upsert); per-thread connections.
* `pipeline.py` - coordinator: one search (one captcha) -> shared queue -> N worker threads, each with **its own session**
  (own cookies/token; no captcha needed after `set_data`) -> per case: viewHistory -> viewBusiness for every hearing -> display_pdf + GET + text for
  every order -> acknowledgement -> (optional) main matters via `cnr_status/viewCNRHistory/` (depth 1, cycle-safe) -> completeness check -> save.
  Incomplete/failed cases are saved (complete=0) and retried in up to `case_retries` extra rounds with fresh sessions; `--resume` skips only `complete=1`.
* Interim applications (IA): the case page's `IA Status` table (class `IAheading`) is parsed by `parsers.parse_ia_status` (ia_number, parties list,
  filing date, next date / purpose, status, and the 7 `viewIABusiness(...)` args named `ia_no, cinoia, ia_case_type_name, ia_filno, ia_filyear,
  national_court_code, search_by`). For every IA the pipeline POSTs `home/viewIABusiness` (body as the site JS builds it) and
  `parsers.parse_ia_business` turns the `Case No | IA No | Business on Date | Hearing Date | Business` table into rows (zero rows is normal and
  still counts as fetched). A failed IA fetch adds `ia seq=N` to `missing` -> case incomplete -> retried like any other link. A parsed IA section is
  not duplicated into `extra_sections`. The only real `viewIABusiness` response captured is the zero-row one (`fixtures/viewIABusiness.txt`);
  the with-rows variant in `tests/fakes.py` (`IA_BUSINESS_SYNTHETIC`) is SYNTHETIC.
* Generic (catch-all) tables keep `<br>` boundaries: cell lines are joined with `"; "` (also `details_extra` and main-matter extras).
* `export.py`.

## Schema (SQLite)

`runs`, `search_runs`, `search_results`, `cases` (all header/status fields raw + ISO, `complete`, `missing_json`, `vb_links/vb_fetched`,
`pdf_links/pdf_saved`, `ack_*`, `kind` = searched|linked, `parent_cnr`, `*_extra_json`, `extra_sections_json`, `warnings_json`),
`parties` (role, seq, advocates_json, extra_lines_json), `acts`, `processes`, `main_matters` (seq, main_cnr, filing_no, `filing_no_suspect`),
`hearings` (seq oldest=1, vb_args_json, business_text, next_purpose, next_hearing_date, business_judge, fetched, error),
`orders` (section, category, pdf_args_json, pdf_url, pdf_path, sha256, size, pages, text, needs_ocr, fetched, error),
`interim_applications` (cnr, seq, ia_number, parties_json, filing_date(+_iso), next_date(+_iso), next_purpose, ia_status, args_json, business_case_no,
fetched, error), `ia_business` (cnr, ia_seq, seq, ia_no, business_on_date(+_iso), hearing_date(+_iso), business), `cases.ia_links/ia_fetched`
(added with a guarded `ALTER TABLE` so older DBs still open), `ack` (decree_holder, judgement_debtor, fields_json), `raw_responses` (gzip body per endpoint/cnr), `errors`.
`Main Filing No.` is a session-scoped site artefact: stored verbatim; values shared by different main CNRs are flagged `filing_no_suspect=1`
and listed in `summary.json`.

## Throughput / limits

~22 requests per case (1 history + ~17 business + orders x2 + ack). Wall-clock per case ~= 22 / min(max_rps, workers / avg_latency).
Reaching ~2 s/case needs roughly `--max-rps 12 --workers 8`; the defaults (6 / 4) give ~3.5-4 s/case. The token rotates per response, so requests
inside one session are strictly sequential - parallelism comes only from more sessions. PDF OCR is not performed (`needs_ocr=1` rows are flagged).

## WAF / block handling (BlockGuard, `ratelimit.py`)

Safe defaults: `--workers 1 --max-rps 1.0`. A block signal (HTTP 403/405 on any request incl. the index GET, or a "Security Page" /
"Search Page not Found" body) pauses ALL sessions (no requests, handshakes or retries) for 90 s, doubling per consecutive block up to 900 s
(+0-25% jitter). Then exactly one probe (GET index) is sent; if it is 200 the run resumes, every session re-handshakes lazily and serialised,
and concurrency and rate are halved for the rest of the run (floor 1 worker / 0.5 rps); otherwise the next, longer cooldown starts. After
`--max-block-min` (30) of total blocked time the run aborts gracefully: unfinished cases stay pending, `summary.json` has
`"aborted": "blocked"`, exit code 3, and `--resume` continues them. A session that handshakes more than 20 times per run counts as blocked.
`--auto-tune` starts at `--start-rps`, adds 0.25 rps per 60 s without a block up to `--max-rps`; after a block the rate drops to 50% and the ramp
never exceeds it (`tolerated_rps` in the summary). Summary also has `blocks`, `total_blocked_s`, `final_rps`, `final_workers`,
`request_stats` (count and avg latency per endpoint) and `s_per_case_excl_blocked`. Exit codes: 0 ok, 2 incomplete cases, 3 aborted.
