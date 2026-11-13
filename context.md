# Session Context: deeper_tool Remediation (handoff)

> **Repo reorganised 2026-11-13 (not committed):**
> - `deeper_tool/` became **`V1/`** and `alternate_tool/` became **`V2/`**. Python imports were renamed to match.
> - `AUDIT_deeper_tool.md` and `PLAN_deeper_tool_remediation.md` moved to `V1/docs/`.
> - The unused `model1/`, `Model2/`, `Model3/`, `Model_BackUP/`, `run_ep_flow.py`, `scrape_100.py` and their outputs moved to `archive/`.
> - `ecourts/`, `tests/` and `fixtures/` stay at the root.
> - **V1 is now independent of `ecourts`:** `V1/captcha.py` and `V1/search.py` are verbatim copies, and `V1/test_independence.py` enforces it. This was an owner request; it overrides the plan's "no new CAPTCHA module" rule.
> - Older paths below refer to the old names.
> - `run_tests.py` runs ecourts + V1 + V2 (441 tests).


Date: 2026-11-12 · Repo: `F:\Project\daksh\LeScrape` · Branch: **`remediation/deeper-tool`** (nothing committed; all changes are uncommitted in the working tree)

## Key documents (read these first)
- `AUDIT_deeper_tool.md`: original audit (gaps in the live scraper).
- `PLAN_deeper_tool_remediation.md`: plan.
  - **Authoritative sections:** "Precedence" note at the top, and sections 3.1, 3.2, 3.3 and 10.1.
  - Older Revision 1 text is superseded.
  - Owner rules:
    - Preserve the existing live scraper and use targeted edits only.
    - Keep the `Pending` default and add a `status_assumed` flag.
    - No live portal requests without owner authorisation.
  - **Prohibited:** new transport, CAPTCHA, enumerator, date or migration modules; shared parser helpers; a job queue; changing `fetch_view_history`'s return type; replacing `main_matters`; the two-step `set_data`.

## Baseline
- Before any change, `python run_tests.py -q` ran 252 tests, all passing (ecourts only).
- openpyxl is NOT installed globally. A copy for the "with openpyxl" test run is at `C:\Users\user\AppData\Local\Temp\claude\2b5aa8e936c85d0a\b9f918bc-c1ad-4cda-8ed2-ae197c78c7a6\scratchpad\dq\lib` (set PYTHONPATH to it). It is temporary and may be gone after a reboot; if so, run `pip install --target <dir> openpyxl`.

## Phase 0: DONE and reviewed
- Lazy imports in `deeper_tool/__init__.py`, openpyxl imported lazily in `exporter.py`, `deeper_tool` added to packaging with an `excel` extra, `run_tests.py` also discovers `deeper_tool/`.
- Synthetic fixtures are in `deeper_tool/fixtures/synthetic/` (see its README).
- Snapshots:
  - `deeper_tool/fixtures/snapshots/request_baseline.json`: 13 curl calls from the UNMODIFIED scraper.
  - `golden_target_case.json`
  - `synthetic_baseline/`
- New tests: `test_request_snapshots.py`, `test_golden_parse.py`, `test_import_isolation.py`. The snapshot compare strips only `--connect-timeout n`, `--max-time n` and `-w fmt`.

## Phase 1: IMPLEMENTED, review in progress (NOT yet accepted)
- Done by a subagent. Without openpyxl: 294 tests, 293 pass, 1 skip. With openpyxl: 294 pass.
- Changes:
  - `scraper.py`: `_curl` timeouts and `-w` status code, `ScraperHTTPError`, `CnrMismatchError`; `fetch_view_history` stores raw responses and retries; `search_cases` handles `errormsg` / `WrongCaptchaError` and persists search runs and rows; `batch_extract` records failures, builds `last_run_summary`, and supports `skip_existing`.
  - `db.py`: migration v2 with `raw_responses`, `fetch_errors`, `search_runs`, `search_results`, `schema_version`, plus new `cases` columns.
  - `models.py`: new fields.
  - `parser.py`: `status_assumed`.
  - `exporter.py`: `[]` vs `None` fix.
  - `batch_extract_20.py`: exits 1 if any case failed.
- Tests: `deeper_tool/test_phase1.py` (31 tests).

**Update 2026-11-13:** both fixes below are APPLIED (as issue 4 of the live-test fixes). Tests are green in both modes, `request_baseline.json`, the snapshots and the datasets are unchanged, and curl was checked locally to expand the literal `\n` in `-w`. **Phase 1 is accepted.**

### Two fixes I was applying when the session stopped (now applied, see update above)
1. **3xx must NOT raise.** In `deeper_tool/scraper.py` `_curl`, the current code raises `ScraperHTTPError` for any non-2xx status. The old behaviour returned redirect bodies (curl runs without `-L`). Change it to log a warning and return the body for 300–399; raise only for ≥400 (transient for 429/5xx). Add a test (302 → body returned, no raise).
2. **The `-w` value is currently a real newline:** `"-w", "\n%{http_code}"` at `scraper.py:~90`. Change it to the literal backslash-n, i.e. Python `"\\n%{http_code}"`, which curl expands. Then:
   - `deeper_tool/test_request_snapshots.py:~143` (Recorder fake): render `fmt.replace("\\n", "\n")` before substituting `%{http_code}`.
   - `deeper_tool/test_phase1.py:~123`: assert `"\\n%{http_code}"`.
   - The test_phase1 fake at `~line 89` already appends `b"\n"+status` itself, so it is fine.

Then re-run `python run_tests.py -q` (both with and without openpyxl), confirm `git status` shows the snapshots and datasets unchanged, and accept Phase 1.

## Next phases
- **Phase 2:** parser completeness plus additive schema (migration v3). Section 10.1 rows R2-2.1, 2.2, 2.3, 2.4, R2-2.5, 2.6, 2.7, R2-2.8, 2.9, R2-2.10, 2.11:
  - status / Sub Stage split with `case_status_raw`;
  - `next_hearing_date`, `case_stage`;
  - the 11 viewBusiness args as `vb_*` columns, plus `hearing_seq` counted from the oldest hearing;
  - all order tables, plus the aria-label as category/description and the split pdf args;
  - one item per main matter with `seq`;
  - `party_seq`, `advocates_json`, and a masked-party flag;
  - unparsed sections captured as JSON;
  - `_to_iso` dates;
  - IA parsing into `cases.interim_applications_json` (markup unverified);
  - exporter: append new columns and sheets only.
  - The golden test must stay green for existing keys. The 11 legacy "Case disposedSub Stage" cases become "Case disposed" (an intended fix).
- **Phase 3:** `deeper_tool/tools/import_legacy_json.py`, which imports `execution_cases_20_deep.json` into a NEW DB with repairs. Never modify the source files.
- **Phases 4–7:** blocked on the owner's decisions:
  - CAPTCHA policy;
  - human browser HAR captures of viewBusiness, displayPdf and the main-matter link;
  - live smoke-run authorisation.

## Live test: 2026-11-12 (owner-authorised)
- Scope: EX (`23^3`) / 2023 / Disposed, City Civil Court Bengaluru (1030135, est 3). Scraper code unchanged; scratch DB only (repo DB and datasets untouched).
- OCR: `easyocr` 1.7.2 installed (pip, user site) on owner request. LocalOcrSolver solved the CAPTCHA on the first attempt. tesseract is not installed; openpyxl is still not installed globally.
- Result: handshake OK; search returned 2344 rows (all persisted in `search_results`); 10/10 cases fetched, 0 errors, ~2.7 s/case; raw viewHistory stored (gzip) for every case.
- Independent verification (Sonnet subagent, own BeautifulSoup extractor, not `deeper_tool.parser`; Opus spot-checked the new gaps in the raw HTML):
  - Complete for 10/10 cases: case details, dates, CNR, disposal, court/judge, acts, processes, main matters, petitioners, hearings (167 on site = 167 captured).
  - No captured value was wrong.
  - The same 10 cases are identical to the earlier `execution_cases_20_deep.json`, except Main Filing No.
- Real raw pages from this run are copied to `deeper_tool/fixtures/live_20261112/` for tests.

### Ranked issues from the live test (fix order)
| Rank | Issue | Seen live | Evidence | Plan row |
|---|---|---|---|---|
| 1 (P0) | **2nd and later respondents dropped.** The site puts all respondents in ONE `<li>`, separated by `<br>` and numbered `1) 2) 3)`. The parser keeps only the first. | 7/10 | KABC010342472022: `1) SHIVARAJU N<br>2) NETHRAVATHI<br>3) SHOBA M` gives 1 respondent | 2.6 (partial: `party_seq`) |
| 2 (P0) | **Sub Stage corrupts `case_status`, and its value is lost.** `<td><strong>Case disposed</strong><strong>Sub Stage </strong></td><td><strong>ISSUE DELIVERY WARRANT</strong></td>` gives "Case disposedSub Stage". | 6/10 | KABC010342952022 | R2-2.1 |
| 3 (P0) | **Only the first order table is read.** An Interim Orders table followed by a Final Orders / Judgements table loses the final order. The section and `aria-label` are not stored. | 1/10 | KABC010342962022: order 2 (10-10-2025) missing | 2.4 |
| 4 (P1) | Pending Phase 1 transport fixes: 3xx must not raise; `-w` must be the literal `\\n%{http_code}`. They did not trigger live. | 0/10 | see "Two fixes" above | R2-0.5 |
| 5 (P1) | Link data not stored: the 11 `viewBusiness` args per hearing (plus `hearing_seq`), and `national_court_code` from the `display_case_acknowlegement` link. | 10/10 | all pages | 2.3 |
| 6 (P2) | **"Main Filing No." is not real data.** The site renders one value for every case, and it changes per session (6849 earlier, 8360 today). The value is kept verbatim; the fix flags it (run-level detection plus a warning). | 7/7 with a main matter | all main-matter pages | new |
| 7 (P3) | The JSON export omits search-row fields (petitioner/respondent text, sr_no, hideparty). They exist only in `search_results`. | n/a | `cases10.json` | 2.11 (append-only) |

**Status 2026-11-13: all 7 issues are FIXED** (uncommitted).
- Implemented by a Sonnet subagent and reviewed by Opus.
- Opus hardened issue 1: a new party starts only on the first line of an `<li>` or on a numbered `N)` line, so an unnumbered extra line (e.g. an address) is ignored, as before, and never becomes a phantom party.
- Tests:
  - New file `deeper_tool/test_live_fixes.py` (38 tests). It includes a ground-truth check over all 10 live fixtures, defect and regression tests per issue, and a v3 migration test.
  - Suite: 330 pass and 2 skipped (xlsx) without openpyxl; 332 pass with openpyxl.
  - `golden_target_case.json` and `request_baseline.json` are untouched.
  - The only existing assertion changed: `test_phase1.test_legacy_db_migrates_in_place` now expects schema versions `[(2,),(3,)]`.
- Offline end to end (parse the 10 live pages, save to a fresh DB, export JSON and CSV): 19 respondents, 11 orders, 167 hearings, 6 sub stages, all statuses "Case disposed".
- Schema: migration v3 in `db.py` adds:
  - parties: `party_seq`
  - cases: `case_status_raw`, `case_sub_stage`, `case_stage`, `next_hearing_date`, `national_court_code`
  - hearings: `hearing_seq`, `vb_*` (11 columns), `vb_args_json`
  - orders: `order_section`, `order_category`, `order_description`, `pdf_args_json`
  - Exports append the new columns at the end.
- `batch_extract` sets `last_run_summary["suspect_main_filing_number"]` when cases with different main CNRs share one value.
- Note: the aria-label category can disagree with the section heading (EX/2/2023 says "Interim Orders |" under the Final Orders heading). Both are stored verbatim.
- **Live re-run of the fixed code (2026-11-13, owner-authorised): PASSED.** Same scope, scratch DB only.
  - Handshake: new session values (delimeter `pooiioutret43`, header `Iiuurwwer34`) were picked up dynamically.
  - Search: OCR needed 4 attempts (3 CAPTCHA rejections, all retried and logged as `CaptchaRejected`); 2344 rows.
  - Cases: 10/10 OK, 0 failures. All responses were HTTP 200 (no redirects seen).
  - Output matches ground truth: 19 respondents, 10 petitioners, 11 orders, 167 hearings, 6 sub stages, all `vb_*` filled, `search_row` present for all, `suspect_main_filing_number` = 8360 flagged.
  - Every viewHistory page was byte-identical to the 2026-11-12 fixtures.
  - An earlier attempt that day was interrupted by the owner after fetching 4 cases, so the scratch DB holds 2 search runs.

Out of scope (still open): fetching viewBusiness text and order PDFs (Phases 4–5; a friend's fork shows the endpoint params `home/viewBusiness` and `home/display_pdf`, see below), the main matter's own record, IA, `_to_iso`, unparsed sections, and pending-case fields (no pending sample yet).

### Friend's fork (github.com/MishaelJulian/DAKSH), reviewed 2026-11-12
- Its `deeper_tool/` equals our commit `4a59cc4`; it has none of the Phase 0/1 work.
- It adds curl_cffi scripts that call:
  - `home/viewBusiness` (payload keys `court_code, dist_code, nextdate1, case_number1, state_code, disposal_flag, businessDate, court_no, national_court_code, search_by, srno, court_complex_code` = the 11 onclick args in order + complex);
  - `home/display_pdf` (`normal_v, case_val, court_code, filename, appFlag` from the `displayPdf` args; the JSON `order` key gives the relative PDF path).
- Do not copy: it hard-codes fallback values (acts, judge), keeps only the first order/process/main matter, and inherits the Sub Stage bug.

## Working method
- Opus supervises and Sonnet subagents implement one phase at a time.
- Opus reviews each phase's git diff before accepting it.
- Never commit unless the user asks.

## alternate_tool (built from scratch, 2026-11-13)
- Standalone scraper in `alternate_tool/`. It does not import `deeper_tool` or `ecourts`. Built by a Sonnet subagent and reviewed and hardened by Opus.
- Tests: 97 offline (`python -m unittest discover -s alternate_tool/tests -t .`).
- Run: `python -m alternate_tool run --state 3 --dist 20 --complex 1030135 --est 3 --case-type "23^3" --year 2023 --status Disposed --limit N --workers 2 --auto-tune --start-rps 1.0 --max-rps 1.5 --out <dir>`. Use `--resume` to continue, and `export` to re-export.
- What it captures per case:
  - viewHistory, with the same fixes as deeper_tool (all parties, sub stage, all order tables, vb args)
  - viewBusiness for every hearing (business text, next purpose, next date, judge)
  - display_pdf, then the PDF download (stored by sha256), with text via pymupdf and a `needs_ocr` flag
  - case_acknowlegement (Decree Holder / Judgement Debtor)
  - optionally `cnr_status/viewCNRHistory` for linked main matters (`--follow-main-matters`)
  - raw responses gzipped in SQLite, plus the raw search response
- Endpoint params were confirmed from the site's `components.js`. Delimiter/header names change per JS version and are parsed from `ajaxCall`.
- Live findings:
  - The **WAF blocks by IP**: GET index returns HTTP 405 (228 bytes).
    - Blocked within 16 s at about 5 rps (4 workers), and within about 1 min at about 2.25 rps (3 workers).
    - Blocks last about 5–11 min.
    - Ran clean at about 1.3 rps (2 workers).
  - A stale session answers JSON `errormsg` "Invalid Request..."; the tool now re-handshakes.
  - CAPTCHA: 13 hand-labelled live images are in `tests/fixtures/captcha_bench`. Otsu-no-blur gets 13/13 and is now first. One run got 10 rejections in a row while the site was in a bad state; rejected images are now saved to `<out>/captcha_rejected/`.
- Live results:
  - run3: 10/10 complete, 19.5 s/case, 0 blocks. Independently verified: 978 checks, 0 missing/wrong, 11/11 PDFs text-matched.
  - run4: 30/30 complete. One block at 2.25 rps was auto-recovered (323 s). 25.4 s/case overall, 14.6 s/case excluding blocked time.
- **The 2 s/case target is not reachable with full data.** Each case needs about 22 requests (one viewBusiness per hearing), and the site's tolerated rate is about 1.3–2 rps, which means about 15–20 s/case. The only way near 2 s/case is to skip per-hearing business text (about 3–4 requests per case), which would still be ~3 s at a tolerated rate.
- Output folders `alternate_tool/output/*` contain real court PDFs and party names. Do not commit them.
- 2026-11-13 later: an integrity pass found the **IA Status** section (2 of 30 cases). It is now parsed into `interim_applications` (parties split on `<br>`), and each IA's `home/viewIABusiness` page is fetched into `ia_business`.
  - Live run5: 20/20 complete, 14.2 s/case, 0 blocks, both IAs fetched (the site returned zero IA business rows for both).
  - Cross-run check: the same 10 cases scraped ~50 min apart are identical except the session-bound `pdf_url` and Main Filing No (8361 then 8362, a site counter); PDF sha256s are identical.
  - Viewer: `python -m alternate_tool.view <CNR> --out <run_dir>` writes `<run_dir>/view/<CNR>.html`. Tests: 111 offline.
