# eCourts Case Status Scraper

A scraper for https://services.ecourts.gov.in (ecourtindia_v6)

**All field names, response formats, and the "View" detail flow below are
CONFIRMED from real HAR captures** of a successful search (Karnataka →
Bengaluru → NyayaDegula Court Complex → PRL. Judge Family Court →
M.C. - Matrimonial Cases → 2025), not guesswork.

## Key Facts Confirmed from HAR

- All AJAX responses are **JSON** with the HTML nested inside, e.g.
  `{"dist_list": "<option>...</option>", "status": 1}`
- **No cookies are used anywhere** — every request just needs
  `ajax_req=true` and `app_token=""`.
- CAPTCHA field on final submit is **`ct_captcha_code`**, not `captcha`.
- `complex_code` values look like `1030135@3@Y` (complex_id @ est_codes @ flag).
  Use the numeric prefix as `court_complex_code`, the full string in `set_data`.
- `case_type_1` values look like `12^3` (case_type_code ^ est_code).
- The results table (`case_data`) has rows with
  `onClick="viewHistory(case_no, cino, court_code, hideparty, search_flag,
  state_code, dist_code, court_complex_code, search_by)"` — clicking "View"
  POSTs these 9 values to **`home/viewHistory`**, which returns the full
  case detail HTML (`data_list` key).

## File Structure

```
ecourts_scraper/
├── main.py           ← CLI entrypoint
├── scraper.py        ← Core scraper (search + case detail / "View")
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
# Districts for Karnataka (state 3)
python main.py explore --state 3

# Court complexes in Bengaluru (dist_code 20)
python main.py explore --state 3 --district 20

# Establishments (use the FULL complex code from previous step)
python main.py explore --state 3 --district 20 --complex "1030136@15,16,17,18,19,20,21,23@Y"

# Case types for that establishment
python main.py explore --state 3 --district 20 --complex "1030136@15,16,17,18,19,20,21,23@Y" --establishment 15
```

## Queue a search (with case-detail fetching)

```bash
python main.py enqueue \
  --state 3 --district 20 \
  --complex "1030136@15,16,17,18,19,20,21,23@Y" --establishment 15 \
  --case-type "46^15" --year 2025 --status Pending \
  --max-results 10
```

`--max-results` controls how many of the returned rows get a full "View"
detail fetch (a search can return thousands of rows — only the first N get
the extra `home/viewHistory` request).

## Run the worker

```bash
python main.py run --captcha manual --once   # type CAPTCHA yourself (testing)
python main.py run --captcha ocr             # free OCR, auto-retries
python main.py run --captcha 2captcha --api-key YOUR_KEY   # production
```

## Export results

```bash
python main.py export --format csv --output results.csv
```

The CSV includes: `cnr_number`, `case_number`, `case_type`, `filing_number`,
`filing_date`, `registration_number`, `registration_date`, `petitioner`,
`respondent`, `petitioner_advocate`, `respondent_advocate` (JSON),
`first_hearing_date`, `next_hearing_date`, `case_stage`,
`court_number_judge`, `court_name`, `acts` (JSON), `case_history` (JSON).

## Confirmed Request Flow

```
1.  POST getCaptcha               -> {"div_captcha": "<img src=securimage_show.php?HASH>"}
2.  GET  securimage_show.php?HASH -> CAPTCHA PNG
3.  POST fillDistrict   {state_code}                                   -> dist_list
4.  POST fillcomplex    {state_code, dist_code}                        -> complex_list
5.  POST fillCourtEstablishment {state_code, dist_code, court_complex_code} -> establishment_list
6.  POST set_data       {complex_code (full), selected_state_code, selected_dist_code, selected_est_code=""}
7.  POST fillCaseType   {state_code, dist_code, court_complex_code, est_code, search_type=c_type} -> casetype_list
8.  POST set_data       (same as 6 but selected_est_code=<chosen>)
9.  POST getCaptcha      (fresh captcha for submission)
10. POST submit_case_type {case_type_1, search_year, case_status, ct_captcha_code,
                            state_code, dist_code, court_complex_code, est_code}
    -> {"case_data": "<table of rows, each with onClick=viewHistory(...)>", "div_captcha": "..."}
11. POST home/viewHistory {case_no, cino, court_code, hideparty, search_flag,
                            state_code, dist_code, court_complex_code, search_by}
    -> {"data_list": "<Case Details, Case Status, Petitioner/Respondent+Advocate, Acts, Processes, IA Status, Case History>"}
```

Step 11 is the "View" button — `scraper.get_case_detail(view_params)`, or
the combined `scraper.search_with_details(..., max_results=N)` which does
steps 1-11 in one call.

### Result row -> view_params

Each row's `onClick="viewHistory(case_no, cino, court_code, hideparty,
search_flag, state_code, dist_code, court_complex_code, search_by)"` is
parsed into a `view_params` dict on each case dict — pass it straight to
`get_case_detail()`.

### Case detail fields (from home/viewHistory)

`parse_case_detail()` returns: `court_name`, `case_type`, `filing_number`,
`filing_date`, `registration_number`, `registration_date`, `cnr_number`,
`first_hearing_date`, `next_hearing_date`, `case_stage`,
`court_number_judge`, `petitioners` (list of `{name, advocate}`),
`respondents` (same), `acts` (list of `{act, sections}`), `case_history`
(list of `{judge, business_date, hearing_date, purpose}`).

⚠️ **Not yet parsed**: the "Processes" table and "IA Status" table also
present in the detail HTML — not extracted yet. Let us know if you need
those too.

## CAPTCHA Strategy

| Method | Cost | Accuracy | Best For |
|--------|------|----------|----------|
| `manual` | Free | 100% | Testing the flow |
| `ocr` | Free | ~70-85% | Small/dev runs (auto-retries on rejection) |
| `2captcha` | ~$1-3/1000 | ~99% | Production |

## Known Limitations / Things to Re-verify

- `app_token=""` worked in captures so far, but if the site adds token
  validation later, requests will start failing — re-capture a HAR
  including the initial page load if so.
- Default delay between requests is 3s — increase if you see 403/429s.
- `search_with_details` adds 1 extra request per row (up to `max_results`)
  — be conservative with this on large result sets.

## Legal Note

eCourts data is public, but automated access to government infrastructure
sits in a legal grey zone in India. Keep request rates conservative.
