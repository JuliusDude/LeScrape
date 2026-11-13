# eCourts Case Status Scraper

A scraper for https://services.ecourts.gov.in (ecourtindia_v6)

**Field names and response formats below are CONFIRMED from a real HAR
capture** (`services_ecourts_gov_in_2.har`), not guesswork.

## Key Facts Confirmed from HAR

- All AJAX responses are **JSON** with the HTML nested inside, e.g.
  `{"dist_list": "<option>...</option>", "status": 1}`
- **No cookies are used anywhere** in this flow — no `Cookie` header sent,
  no `Set-Cookie` received. Every request just needs`ajax_req=true` and
  `app_token=""`.
- CAPTCHA field on final submit is **`ct_captcha_code`**, not `captcha`.
- `complex_code` values look like `1030135@3@Y` (complex_id @ est_codes @ flag).
  Use the numeric prefix (`1030135`) as `court_complex_code` in later calls,
  but the FULL string in `set_data`.
- `case_type_1` values look like `12^3` (case_type_code ^ est_code).

## File Structure

```
ecourts_scraper/
├── main.py           ← CLI entrypoint
├── scraper.py        ← Core scraper (confirmed flow/field names)
├── captcha_solver.py ← OCR + 2captcha solver
├── parsers.py        ← JSON-aware response parsers
├── storage.py        ← SQLite + job queue
├── worker.py         ← Queue worker
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

## Quick Start — Drill down to find codes

```bash
# 1. List states... (state codes are NOT returned by an API - use the
#    known list in your browser's dropdown, e.g. Karnataka = 3)

# 2. Districts for Karnataka
python main.py explore --state 3

# 3. Court complexes in a district (e.g. Bengaluru = dist_code 20)
python main.py explore --state 3 --district 20

# 4. Establishments in a complex (use the FULL complex code from step 3)
python main.py explore --state 3 --district 20 --complex "1030135@3@Y"

# 5. Case types for that establishment
python main.py explore --state 3 --district 20 --complex "1030135@3@Y" --establishment 3
```

## Queue a search

```bash
python main.py enqueue \
  --state 3 --district 20 \
  --complex "1030135@3@Y" --establishment 3 \
  --case-type "12^3" --year 2024 --status Pending
```

## Run the worker

```bash
# Manual CAPTCHA entry (recommended for first test runs)
python main.py run --captcha manual --once

# Free OCR (lower accuracy, auto-retries on wrong CAPTCHA)
python main.py run --captcha ocr

# 2captcha (paid, ~99% accuracy)
python main.py run --captcha 2captcha --api-key YOUR_KEY
```

## Export results

```bash
python main.py export --format csv --output results.csv
```

## Confirmed Request Flow

```
1. POST getCaptcha               -> {"div_captcha": "<img src=securimage_show.php?HASH>"}
2. GET  securimage_show.php?HASH -> CAPTCHA PNG
3. POST fillDistrict   {state_code}                                   -> dist_list
4. POST fillcomplex    {state_code, dist_code}                        -> complex_list
5. POST fillCourtEstablishment {state_code, dist_code, court_complex_code} -> establishment_list
6. POST set_data       {complex_code (full), selected_state_code, selected_dist_code, selected_est_code=""}
7. POST fillCaseType   {state_code, dist_code, court_complex_code, est_code, search_type=c_type} -> casetype_list
8. POST set_data       (same as 6 but selected_est_code=<chosen>)
9. POST getCaptcha      (fresh captcha for submission)
10. POST submit_case_type {case_type_1, search_year, case_status, ct_captcha_code, state_code, dist_code, court_complex_code, est_code}
    -> {"case_data": "<table or 'Record not found'>", "div_captcha": "..."}
```

## CAPTCHA Strategy

| Method | Cost | Accuracy | Best For |
|--------|------|----------|----------|
| `manual` | Free | 100% | Testing the flow |
| `ocr` | Free | ~70-85% | Small/dev runs (auto-retries on rejection) |
| `2captcha` | ~$1-3/1000 | ~99% | Production |

## Known Limitations / Things to Re-verify

- **Only `Record not found` was observed** in the captured submission — we
  haven't seen the HTML structure of an actual results table. The table
  parser in `parsers.py` is written defensively (header-based column
  mapping) but should be tested against a real "results found" response.
  If you get a search with actual results, save that HAR and we can refine
  `_parse_case_row()`.
- `app_token=""` worked in this capture, but if the site adds token
  validation later, requests will start failing — re-capture a HAR
  including the initial page load if so.
- Default delay between requests is 3s — increase if you see 403/429s.

## Legal Note

eCourts data is public, but automated access to government infrastructure
sits in a legal grey zone in India. Keep request rates conservative.
