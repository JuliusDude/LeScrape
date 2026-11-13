# Audit Report: `deeper_tool` eCourts Scraper

Date: 2026-11-12 · Scope: `LeScrape/deeper_tool` (the brief said `deeper_tools`; the real directory is `deeper_tool`) · Mode: audit only, no code changed.

## Evidence base and its limits

| Source | What it proves |
|---|---|
| `deeper_tool/fixtures/target_case_view.html` | **The only real `home/viewHistory` response in the repo** (EX/2/2023, CNR KABC010342242022). Most "Verified" findings below rest on it. |
| `deeper_tool/execution_cases_20_deep.{json,csv,xlsx}` | Persisted output for 20 real cases. Used for data-quality checks. |
| `fixtures/services.ecourts.gov.in 2.har` (byte-identical copies in `Model2/`, `Model3/`) | 18 entries covering only the dropdown/captcha/search flow. The one search in it returned "Record not found". **It has no viewHistory, viewBusiness or PDF traffic.** |
| `fixtures/sample_case_detail.html` | Hand-written synthetic file, not site evidence. |
| `Model_BackUP/index.html` | Unrelated page (F1-themed). No eCourts content. |
| Live site | **Not checked.** A live probe was blocked by the session's permission policy because the scraper's flow requires automated CAPTCHA solving. No live requests were made. |
| `Model3/parsers.py` header | Refers to `services_ecourts_gov_in_4.har` (described as "real results + case detail view"). **That file is not in the repo.** |

Labels used below:
- **Verified**: seen in the real fixture or the persisted outputs.
- **Code-verified**: a defect read directly from the code.
- **Unverified**: depends on site behaviour that no available capture shows.

---

## A. Executive Summary

**For the main case page it does fetch, the scraper is fairly complete.**
- The one real viewHistory page shows 38 distinct field types.
- 32 are persisted correctly, 2 partially, and 4 not at all (section G).

**The main loss is everything one click beyond that page. None of these are fetched:**
- Per-hearing **business/proceedings detail** (`viewBusiness`): 32 links in the sample case, 301 hearings across the 20-case batch.
- **Order/judgment PDFs** (`displayPdf`).
- The **linked main matter's own case record** (`funView_CinoHistory`).
- The **QR code / cause title** page (`display_case_acknowlegement`).

**Critical / high findings:**
1. **P0: Sub Stage data is corrupted and lost (Verified).** In 11 of 20 persisted cases, `case_status` = `"Case disposedSub Stage"`. A "Sub Stage" label is merged into the status value, and the sub stage value itself is never stored. No raw HTML was kept, so it cannot be recovered without re-fetching.
2. **P0: No raw responses are kept (Code-verified).** viewHistory HTML is parsed and thrown away. Parser bugs (like #1) cannot be fixed in the stored data, and each finding below can only be checked by re-scraping.
3. **P1: Order/judgment documents are not obtained (Verified).**
   - Only the `displayPdf(...)` onclick string is stored. Its arguments are opaque encrypted tokens, probably tied to the session (Unverified), so the stored string may be useless later.
   - The order description in the link's `aria-label` (e.g. "Interim Orders | Order on Exhibit on 2025-12-06") is dropped.
4. **P1: Daily proceedings (business on date) are not fetched (Verified that the links exist; what they return is Unverified).** The hearing link arguments (court no `1147`, next-date key `20251206`, serial `31`, status flag `DisposedP`) are also thrown away.
5. **P1: Regression against the repo's own base parser (Code-verified).** `ecourts/parser/case_details.py` captures `next_hearing_date`, `case_stage` and the IA table. `deeper_tool`:
   - parses `next_hearing_date` and then discards it (it is not in the model);
   - has no `case stage` label at all;
   - has no IA handling.

   All 20 batch cases are disposed, so this has not yet cost data. **It will drop key fields as soon as pending cases are scraped.**
6. **P1: The orders parser reads only the first `order_table` (Code-verified).** If the site also renders a separate interim-orders table (Unverified), it is silently skipped. Every one of the 20 cases has exactly 1 order with order number `1`.
7. **P1: Coverage is set by the run script, not by the data.**
   - The batch uses one establishment, one case type (`23^3`), one year (2023) and status `Disposed` only.
   - It then keeps the first 20 results (`batch_extract(limit=20)`) and drops the rest.

**Severity overall:** case-level metadata is reliable. Case history is shallow (hearing rows only, no proceedings), and documents are missing entirely. Pending-case fields and several standard eCourts sections are unhandled or unverifiable.

---

## B. Existing Scraper Architecture

```
deeper_tool/
  scraper.py          DeeperScraper: curl.exe subprocess HTTP, session handshake, captcha, search, viewHistory, batch
  parser.py           parse_deeper_case_detail(): BeautifulSoup over viewHistory HTML
  models.py           DeeperCaseDetail + Party/Act/Process/MainMatter/Hearing/Order items
  db.py               SQLite (deeper_ecourts.db): cases + 6 child tables, upsert + delete/reinsert children
  exporter.py         JSON (nested), CSV (flat summary), XLSX (7 sheets)
  run_single_case.py  Parses the fixture (falls back to a live fetch only if the fixture is missing)
  batch_extract_20.py Live: search EX/2023/Disposed -> first 20 -> deep extract -> export
  test_deeper.py      Offline tests against the fixture
```
It reuses `ecourts.core.captcha.LocalOcrSolver` and `ecourts.parser.search.parse_case_results`.

**Execution flow** (`batch_extract_20.py`):
1. `init_session`:
   - GET `/` and `?p=casestatus/index`, and scrape `app_token`;
   - GET the versioned `components.js` and read the `delimeter` custom header from it.
2. `search_cases`, which does three things:
   - `set_data` (selects the establishment);
   - `getCaptcha` + OCR, up to 5 tries;
   - `submit_case_type`, then `parse_case_results` builds `view_params` from the row's `viewHistory(...)` onclick.
3. `batch_extract`: for each of the first 20 rows, POST `home/viewHistory`, then `parse_deeper_case_detail`, then `db.save_case`.
4. Export from the DB to JSON, CSV and XLSX.

**Endpoints:**

| Endpoint | Called? |
|---|---|
| `casestatus/getCaptcha`, `set_data`, `submit_case_type`, `home/viewHistory` | yes |
| `casestatus/fillDistrict`, `fillcomplex`, `fillCourtEstablishment`, `fillCaseType` | no; codes are hard-coded (fine for a fixed scope, but blocks enumeration) |
| `viewBusiness`, `displayPdf`, `funView_CinoHistory`, `display_case_acknowlegement` | **no**. Endpoint names and params are not yet known; they would have to be read from the site's JS |

---

## C. Scraped Data Inventory

"Avail." = available on the target. V = Verified in the real fixture or outputs, U = Unverified.

| Category | Field | Avail. | Scraped | Persisted | Status |
|---|---|---|---|---|---|
| Court | Establishment heading (`h2#chHeading`) | V | yes | yes | Complete |
| Case details | Case type | V | yes | yes | Complete |
| | Filing number / date | V | yes | yes | Complete |
| | Registration number / date | V | yes | yes | Complete |
| | CNR | V | yes | yes | Complete |
| | e-Filing number / date | V (empty in sample) | yes | yes | Complete (`-` not normalised) |
| Status | First hearing date | V | yes | yes | Complete (long-form date, not normalised) |
| | Decision date | V | yes | yes | Complete (same) |
| | Case status | V | yes | corrupted in 11/20 | **Partial** |
| | Sub Stage | V (shown by the artefact in 11/20) | no | no | **Missing** |
| | Nature of disposal | V | yes | yes | Complete |
| | Court number and judge | V | yes | yes, as one string | Complete (court no and judge not split) |
| | Next hearing date | U (pending cases) | parsed | **dropped** (not in model) | **Not persisted** |
| | Case stage | U (pending cases) | no | no | Missing (Unverified) |
| Parties | Petitioner name, advocate | V | yes | yes | Complete |
| | Respondent name, advocate | V | yes | yes | Complete |
| | Party ordinal; 2nd and later advocate lines | U | no (only the first "Advocate-" line) | no | Unverified |
| Acts | Act, section | V | yes | yes | Complete (section is literally `,` in 20/20) |
| Processes | ID, title, date | V | yes | yes | Complete |
| Main matters | Case no, CNR, filing no | V | yes | yes | Complete for one; a 2nd main matter would overwrite the first (Code-verified) |
| | Main matter's own case record | V (link exists) | no | no | **Missing** |
| Hearings | Judge, business date, hearing date, purpose | V | yes | yes | Complete |
| | Link args (court no, next-date key, serial, status flag) | V | no | no | **Missing** |
| | Business/proceedings detail per hearing | V link, U content | no | no | **Missing** |
| Orders | Number, date, details label | V | yes | yes | Complete |
| | Order type/description (`aria-label`) | V | no | no | **Missing** |
| | PDF tokens | V | raw onclick | yes | Partial (likely tied to the session) |
| | PDF document | V link, U content | no | no | **Missing** |
| | Interim-orders table (second `order_table`) | U | no (only the first table) | no | Unverified |
| Ack | QR code / cause title | V link | no | no | Missing |
| Other sections | IA status, FIR details, subordinate/lower court, transfer details, linked cases, objections | U (none appear in the one real capture) | no | no | Unverified |
| Search row | Sr no, case no, pet/resp text, court, `raw_cells`, `hideparty` | V (code) | yes | **no** | Not persisted |
| Provenance | Raw HTML, fetch time, source params | V | yes | only `updated_at` | **Not persisted** |

---

## D. Missing Data and Coverage Gaps

1. **Sub Stage / case status corruption (P0, Verified)**
   - Source: `case_status_table` row(s) carrying "Sub Stage".
   - Cause: `parser.py` walks cells as label/value pairs (`i += 2`) and joins each cell with `get_text(strip=True)`. A cell that holds both the status and a "Sub Stage" label is joined into one string. The label is not in `label_norm_order`, so its value is dropped.
   - Evidence: 11 of 20 rows of `execution_cases_20_deep.json` contain `"case_status": "Case disposedSub Stage"`. The exact HTML is not available because raw responses are not kept.
   - Fix:
     - capture the raw HTML of an affected case;
     - add `case_sub_stage` (and `case_stage`, `next_hearing_date`) to the model, DB and parser;
     - parse nested label/value pairs within cells.

2. **Next hearing date / case stage (P1, Code-verified)**
   - Cause: `parser.py` maps "next hearing date" into `field_map`, but `DeeperCaseDetail` has no such field, so it is lost. "case stage" is not mapped at all.
   - The base parser `ecourts/parser/case_details.py` handles both.
   - Fix: add the fields across model, db and exporter, and add the label mappings.

3. **Hearing proceedings (`viewBusiness`) (P1)**
   - Source: each Business-on-Date link `viewBusiness('3','20','20251125','KABC010342242022','3','DisposedP','25-11-2025','1147','KABC01','CScaseType','31')`.
   - Cause: the parser keeps only the link text.
   - Fix:
     - first persist the link args (cheap, no new requests);
     - then find the endpoint in the site's JS and fetch each hearing's business detail, paced, into a `hearing_business` table.
   - Dependency: what the response contains is Unverified.

4. **Order documents (P1)**
   - Source: `displayPdf(token1..token4,'')` plus the `aria-label`.
   - Fix:
     - store the `aria-label` as `order_description`;
     - resolve the PDF within the same session at scrape time;
     - store the file plus its hash, size and path.

   Persisting the tokens alone is probably worthless.

5. **Interim orders (P1, Unverified on site / Code-verified in parser)**
   - Cause: `soup.find("table", class_=order_table)` takes only the first table.
   - Fix: iterate over all `order_table` tables and tag each order with the heading it sits under.

6. **Main matter record (P2)**
   - Source: `funView_CinoHistory('KABC010166632020')`.
   - Fix: optionally queue linked CNRs for a full fetch, with a depth limit.

7. **Multiple main matters (P2, Code-verified)**
   - Cause: the loop reassigns scalar variables, so only the last row is kept.
   - Fix: append one item per row.

8. **Additional sections (P1, Unverified)**
   - IA status, FIR, subordinate court, transfer, linked cases.
   - Fix:
     - get real viewHistory captures for criminal, appeal, pending and IA-bearing cases (or locate `services_ecourts_gov_in_4.har`);
     - add a "catch-all" step that stores every `<h3>`-headed table generically, so unknown sections are never silently lost.

9. **Search-row data and scope (P1)**
   - The search row fields and `hideparty` are not saved.
   - `limit=20` drops the remaining results.
   - Scope is a single establishment, type, year and status.
   - Fix: persist search rows to a `search_results` table, and make the limit and scope configurable.

---

## E. Technical Issues

**Network / reliability** (`scraper.py`)
- `_curl` has several gaps:
  - no `--max-time` and no subprocess timeout, so a stuck request can hang the run;
  - no HTTP status check (`-s` without `--fail`);
  - no retry;
  - `decode(errors="ignore")` silently drops bytes it cannot decode.
- Parse failures return `{}` or `""`. viewHistory failures raise `ValueError`, which `batch_extract` prints and skips. Failed CNRs are not recorded and there is no automatic re-try, so partial runs are easy to miss.
- No session re-initialisation on token or session expiry.
- `parse_case_results` raises `WrongCaptchaError` when `errormsg` contains "invalid captcha". `search_cases` only checks `case_data` text and does not catch that exception, so a rejected captcha signalled through `errormsg` crashes the search instead of retrying.
- If all 5 captcha attempts fail, it returns `[]`.

**Pagination**
- The HAR's search JSON (`case_data`, `status`, `div_captcha`) has no page or total keys.
- The parser reads one table and has no pagination logic.
- No real non-empty result capture exists, so whether large result sets are cut off is **Unverified**. Count results against an independent figure (e.g. the highest registration number seen) to check.

**Data integrity**
- `case_status` defaults to `"Pending"` when the label is missing. A truncated or error page is therefore stored as a pending case.
- Mixed date formats (`dd-mm-yyyy` and `02nd January 2023`), all stored as TEXT with no normalised column.
- `hearing_index` 1 is the newest hearing, which is the reverse of "chronological". Rows are not linked to orders.
- Upsert overwrites the case and delete/reinserts children. There is no history of status changes between runs.
- `court_name` comes from the establishment heading. The actual court hall is only inside `court_number_judge`.
- Act `section` = `,` is the source value. It is kept as-is, with no normalisation flag.

**Environment / testing**
- `deeper_tool/__init__.py` imports the exporter, which imports `openpyxl`. Without `openpyxl` the parser cannot be imported, and `test_deeper.py` fails here with `ModuleNotFoundError: openpyxl`.
- Tests cover a single fixture only.
- `deeper_ecourts.db` does not exist in the repo. The exports are the only persisted artefacts present.

---

## F. Prioritized Recommendations

| # | Pri | Change | Files | Outcome / risk |
|---|---|---|---|---|
| 1 | P0 | Save the raw viewHistory HTML (plus request params and timestamp) for every fetch, e.g. a `raw_responses` table or files keyed by CNR + time | scraper.py, db.py | Allows re-parsing, and is the evidence needed to finish this audit. Low risk. |
| 2 | P0 | Collect real captures for disposed, pending, criminal (FIR), appeal (lower court), IA-bearing, transferred and multi-order cases (or locate `services_ecourts_gov_in_4.har`) and add them as fixtures | fixtures/, test_deeper.py | Turns the Unverified rows into findings. Needs authorised site access done manually or with approval. |
| 3 | P0 | Fix Sub Stage parsing; add `case_sub_stage`, `case_stage`, `next_hearing_date` | parser.py, models.py, db.py, exporter.py | Needs a schema migration (`ALTER TABLE ADD COLUMN`). |
| 4 | P1 | Parse all `order_table`s; store `order_description` (aria-label) and the source section | parser.py, models.py, db.py | Low risk. |
| 5 | P1 | Persist hearing link args; then fetch business-on-date detail | parser.py, scraper.py, db.py | About 15 extra requests per case on average (301 hearings / 20 cases); keep the pacing. |
| 6 | P1 | Resolve and download order PDFs within the session; store file metadata | scraper.py, db.py | Storage cost; tokens likely expire. |
| 7 | P1 | Generic catch-all capture of unrecognised `<h3>` sections and tables | parser.py, db.py | Prevents silent loss. |
| 8 | P1 | Persist search rows; configurable scope and limit; record failed CNRs plus a retry queue | scraper.py, batch_extract_20.py, db.py | Gives full coverage and makes runs auditable. |
| 9 | P2 | curl timeouts, HTTP status checks, retry with backoff, session re-init, catch `WrongCaptchaError`, don't default the status to Pending | scraper.py, parser.py | Better reliability. |
| 10 | P2 | Multiple main matters; optionally follow linked CNRs | parser.py, scraper.py | Medium risk (fan-out). |
| 11 | P2 | Normalised ISO date columns alongside the raw text; split court no and judge | parser.py, db.py | Additive. |
| 12 | P3 | Lazy-import openpyxl; add party ordinals and all advocate lines; correct the `hearing_index` semantics | __init__.py, exporter.py, parser.py | Minor. |

Suggested order: 1, then 2, then 3–4, then 7, then 9, then 5–6, then 8, then 10–12.

---

## G. Overall Completeness Assessment

Measured against the **one real viewHistory page** (the only verified evidence of what the target shows):
- **38 distinct field types visible:** 32 persisted complete (84%), 2 partial (case status, PDF tokens), 4 missing (sub stage, hearing link args, order description, QR/cause title).
- **Linked resources** (proceedings detail, order PDFs, main-matter record): **0 of 3 kinds fetched.**

This is **not** a site-wide completeness percentage. The sample is one disposed EX case, so it cannot show:
- pending-case fields (next hearing date, case stage);
- criminal or appeal sections (FIR, lower court);
- IA, transfer or interim-order sections.

The parser is known to drop or not handle several of these. A real site-wide figure needs the captures in recommendation #2, so true completeness is lower than 84% by an amount that cannot yet be measured.

**Answer to the key question:** for a typical case, we miss:
- the Sub Stage, and status text is corrupted in 11 of 20 cases;
- every hearing's proceedings detail;
- every order/judgment document and its description;
- the linked main case's record;
- for pending cases, the next hearing date and case stage;
- possibly whole sections (interim orders, IA, FIR, lower court, transfers) that no available evidence can confirm or rule out.
