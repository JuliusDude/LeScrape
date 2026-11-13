# V1: deep case extractor (formerly `deeper_tool`)

V1 is the first-generation deep scraper for eCourts India district-court cases. It is **self-contained**: it does not import the repo's `ecourts` package.
- Its CAPTCHA solver (`V1/captcha.py`) and search-result parser (`V1/search.py`) are verbatim copies of the `ecourts` versions, taken on 2026-11-13.
- `test_independence.py` enforces this: it blocks `ecourts` imports, and runs V1 from a copy with no `ecourts` folder.
- Dependencies: beautifulsoup4, Pillow, easyocr (for the CAPTCHA), `curl.exe`; openpyxl is optional.

It searches one court establishment and case type using an OCR-solved CAPTCHA. It then fetches each case's `home/viewHistory` page and parses it into a relational SQLite database, with JSON, CSV and XLSX exports.

V2 (`../V2`) is the newer, standalone scraper. It also fetches per-hearing business text, order PDFs and their text, the acknowledgement page and IA details. Use V1 when you only need the case-history page or want its relational model.

## What V1 captures (per case, from viewHistory)

- **Case details:** type, filing and registration number/date, CNR, e-filing.
- **Status:**
  - first hearing date, decision date, next hearing date
  - case status (with `case_status_raw`), **Sub Stage**, case stage
  - nature of disposal, court number and judge
- **Parties:** every numbered petitioner and respondent (several can sit in one list item), each with an advocate and `party_seq`.
- **Acts, processes, main matters:** one item per row. "Main Filing No." is a session-scoped site artefact; each run flags it in `last_run_summary["suspect_main_filing_number"]`.
- **Hearings:** every row, with the 11 `viewBusiness` link arguments (`vb_*`) and `hearing_seq`, where the oldest hearing is 1.
- **Orders:** every order table (Interim Orders and Final Orders / Judgements), with `order_section`, `order_category`, `order_description` and the `displayPdf` arguments. V1 does **not** download the PDFs.
- **Provenance:**
  - the raw viewHistory response (gzip) in `raw_responses`
  - fetch errors in `fetch_errors`
  - search runs and all result rows in `search_runs` / `search_results`

## Usage

```bash
# Live batch: EX / 2023 / Disposed, City Civil Court Bengaluru, first 20 cases
python V1/batch_extract_20.py

# Parse the bundled fixture offline
python V1/run_single_case.py

# Tests (from the repo root). run_tests.py runs ecourts, V1 and V2 together.
python run_tests.py -q
```

Programmatic use:

```python
from V1.scraper import DeeperScraper
s = DeeperScraper(db_path="V1/deeper_ecourts.db", pacing=2.0)
s.init_session()
rows = s.search_cases(case_type="23^3", search_year="2023", case_status="Disposed",
                      complex_code="1030135", state_code="3", dist_code="20", est_code="3")
cases = s.batch_extract(rows, limit=10, meta={"court_name": "PRL. CITY CIVIL AND SESSIONS JUDGE"})
print(s.last_run_summary)
```

Notes:
- Transport is `curl.exe`, run as a subprocess with a cookie jar, timeouts, HTTP status checks and transient retries.
- Pacing is 2 s between requests.
- The CAPTCHA is solved by EasyOCR (`pip install easyocr`). openpyxl is optional (`pip install .[excel]`).

## Layout

```
V1/
├── scraper.py      DeeperScraper: handshake, CAPTCHA, search, viewHistory, batch_extract
├── captcha.py      CAPTCHA preprocessing + LocalOcrSolver (EasyOCR, pytesseract fallback)
├── search.py       case-type search response -> rows with viewHistory params and CNR
├── parser.py       parse_deeper_case_detail(): viewHistory HTML -> DeeperCaseDetail
├── models.py       dataclasses (case, party, act, process, main matter, hearing, order)
├── db.py           SQLite schema with in-file migrations (v2 provenance, v3 live-test fields)
├── exporter.py     JSON (nested), CSV (flat), XLSX (multi-sheet; openpyxl optional)
├── batch_extract_20.py, run_single_case.py   run scripts
├── test_*.py       offline tests (golden parse, request snapshots, phase 1, live fixes, import isolation)
├── fixtures/
│   ├── target_case_view.html   real viewHistory page of EX/2/2023 (golden)
│   ├── live_20261112/          10 real viewHistory pages, ground truth for the tests
│   ├── synthetic/              hand-made variants (each labelled synthetic)
│   └── snapshots/              request baseline + golden parse output
├── docs/
│   ├── AUDIT_deeper_tool.md            original audit
│   └── PLAN_deeper_tool_remediation.md remediation plan (sections 3.1–3.3 and 10.1 are authoritative)
└── case_EX_2_2023.*, execution_cases_20_deep.*   datasets from earlier runs (do not modify)
```

## Verification history

- **2026-11-12:** live run of 10 cases. An independent verifier found 3 gaps:
  - 2nd and later respondents dropped (7 of 10 cases)
  - Sub Stage merged into the status (6 of 10)
  - the final order lost when an Interim Orders table came first (1 of 10)

  All three were fixed with tests (`test_live_fixes.py`).
- **2026-11-13:** live re-run of the fixed code. 10/10 cases matched ground truth: 19 respondents, 11 orders, 167 hearings and 6 sub stages.

Remaining limits: V1 does not fetch per-hearing business text, PDFs, IA details or linked main-matter records (V2 does), and pending-case fields are covered only by synthetic tests. The full history is in `../context.md`.
