# deeper_tool: Validated Findings and Remediation Blueprint

Date: 2026-11-12 · Builds on `AUDIT_deeper_tool.md` · Status: planning only. No production code, datasets or portal traffic were touched.

**Evidence labels:**
- **V-code**: read in the source with a line reference.
- **V-repro**: reproduced offline with synthetic HTML against the real parser.
- **V-data**: seen in the saved outputs or fixture.
- **UNVERIFIED**: needs a real portal capture.

Paths are relative to `LeScrape/` unless stated.

> **Precedence (owner, 2026-11-12):**
> - Sections **3.1, 3.2, 3.3 and 10.1 are authoritative.**
> - Wherever older text in sections 4, 6–9, 11 and 13 mentions any of the following, it is superseded by those sections:
>   - a `FakeTransport` or transport interface
>   - a `jobs` table or queue
>   - `enumerate.py` or `dates.py`
>   - shared parser helpers or `linked_cases`
>   - the two-step `set_data`
>   - removing the `Pending` default
> - Offline tests patch `deeper_tool.scraper.subprocess.run`.
> - **Owner decision:** keep the `Pending` default and add a `status_assumed` flag (this replaces the name `case_status_inferred`).
> - No live portal requests without separate owner authorisation.

---

## 1. Validated Audit Findings

### 1.1 Corrections to the previous audit

| Previous claim | Correction |
|---|---|
| **A.** "The actual Sub Stage value is lost" (P0) | **Not proven.** Offline reproduction shows `"Case disposedSub Stage"` is produced only when the text `Sub Stage` sits *inside the same `<td>`* as the status value. If a value had been in that cell, it would also have been concatenated (e.g. `"Case disposedSub StageFinal decree"`). The merge therefore did **not** swallow a value. The label was either empty or its value sat outside the cell or in a non-text node. **The real defect is general:** `get_text(strip=True)` with no separator merges sibling text nodes for **every** mapped label. This was reproduced for `nature_of_disposal` too (`parser.py:151-153`). |
| **E.** "Missing status label defaults to Pending" | Confirmed, but the impact is narrower. A truly empty or error page has no CNR, so `save_case` raises (`db.py:154`) and nothing is saved. Mislabelling happens only when a page *has* a CNR but no status row. |
| **H.** Main matters | Upgraded from "may be missed" to **V-repro**. Two `Main Case No.` rows collapse into one item (last-row values). |
| **H.** Orders | **V-repro.** A second `order_table` is dropped entirely. Also, the fixture's table heading says "Final Orders / Judgements" while the link's `aria-label` says `Interim Orders \| Order on Exhibit`. **The heading is not a reliable category; the aria-label is.** |
| Previous audit's 84% figure | Unchanged and still applies only to one disposed EX case. Not extrapolated. |

### 1.2 New defects the previous audit missed

| ID | Finding | Evidence |
|---|---|---|
| N1 | **Search `errormsg` path is dead.** `search_cases` passes only `case_data` to `parse_case_results`. A `{"errormsg":"Invalid Captcha"}` reply with no `case_data` returns `[]`, which is treated as "succeeded, 0 cases": no retry, and the batch aborts as "no results". | V-code `deeper_tool/scraper.py:225,230`; `ecourts/parser/search.py:137-144` |
| N2 | **No check that the parsed CNR equals the requested `cino`.** When the CNR row is missing, the fallback scan takes the first CNR-shaped token anywhere, e.g. the main matter's CNR link. The case is then upserted under the **wrong primary key**, overwriting another case. | V-code `parser.py:166-172`, `scraper.py:243-252` |
| N3 | `hearing_index` counts from the newest hearing, so it **shifts every time a hearing is added**. It is not a stable identifier. Duplicate business dates exist (KABC010000532023 has `03-11-2023` three times), so `business_date` is not a key either. | V-data |
| N4 | `view_params` are never persisted, so stored cases cannot be re-fetched without searching again. | V-code `scraper.py:268-275`, `db.py` DDL |
| N5 | Partial runs are indistinguishable from full runs. Files are named `*_20_deep` even with fewer cases, and there is no failure summary. | V-code `batch_extract_20.py:85` |
| N6 | `exporter.py:25,38,72` uses `cnr_list or db.list_cnrs()`, so an empty list exports the **entire DB**. | V-code |
| N7 | The base parser maps "e-filing number" into `filing_number` because of dict order, and "stage"/"court" are loose substrings. Reusing it naively would import these bugs. | V-code `ecourts/parser/case_details.py:170-186` |
| N8 | Masked parties (`XXXXXXX`, 99/99 rows of `cases_100_export.csv`) are stored as real names with no flag. | V-data |
| N9 | `deeper_tool` is not packaged (`pyproject` includes `ecourts*` only), not in `run_tests.py`, and `openpyxl` is declared nowhere. | V-code |
| N10 | `_curl` decodes with `errors="ignore"`. `_post_api` / `fetch_view_history` turn any parse or `AttributeError` into `{}` / `""`. | V-code `scraper.py:81,137-140,189-191` |

### 1.3 Validated findings table

| # | Finding | Prev. | Verified | Source | Evidence | Confidence |
|---|---|---|---|---|---|---|
| A | Cell-text concatenation corrupts mapped values (Sub Stage in 11/20) | P0 | **P1** (data corruption, repairable offline; value loss unproven) | `parser.py:149-163` | V-repro + V-data | High |
| B | No raw-page retention | P0 | **P0** | `scraper.py:166-191, 238-252` | V-code | High |
| N2 | Wrong-CNR upsert possible | — | **P0** | `parser.py:166-172`, `scraper.py:243` | V-code | High (mechanism); trigger frequency unknown |
| N1 | Captcha/error reply silently becomes "0 results" | — | **P1** | `scraper.py:225-235` | V-code | High |
| C | viewBusiness args discarded; proceedings not fetched | P1 | **P1** | `parser.py:266-288` | V-data (32 links; 11 args) | High (links); response content UNVERIFIED |
| D | Order aria-label discarded; PDFs not fetched; tokens stored raw | P1 | **P1** | `parser.py:292-316` | V-data | High; token lifetime UNVERIFIED |
| E1 | `next_hearing_date` parsed then dropped | P1 | **P1** | `parser.py:135`, `models.py` | V-repro | High |
| E2 | Case Stage unmapped; no IA handling | P1 | **P1** | `parser.py:127-143` | V-code; site layout UNVERIFIED | Medium |
| F | Search scope fixed, sliced at 20, rows not persisted | P1 | **P1** | `batch_extract_20.py:58-83`, `scraper.py:262` | V-code | High |
| G | Main case record not fetched | P2 | **P2** | `parser.py:236-262` | V-data | High |
| H1 | Only the first `order_table` parsed | P2 | **P1** (silent loss) | `parser.py:292` | V-repro | High (code); second table on site UNVERIFIED |
| H2 | Only the last main-matter row kept | P2 | **P2** | `parser.py:245-262` | V-repro | High |
| N3 | Unstable hearing index | — | **P1** (blocks hearing↔proceeding linkage) | `parser.py:268-287` | V-data | High |
| N4 | `view_params` not persisted | — | **P1** | `db.py` DDL | V-code | High |
| R1 | No timeout (curl or subprocess) | P2 | **P2** | `scraper.py:63,77` | V-code | High |
| R2 | No HTTP status check | P2 | **P1** (error pages reach the parser) | `scraper.py:77-81` | V-code | High |
| R3-5 | Print-and-skip, no failure ledger, no viewHistory retry | P2 | **P1** | `scraper.py:166-191, 287-288` | V-code | High |
| R6 | `WrongCaptchaError` uncaught | P2 | **P2** (only on case-variant text; N1 is the bigger hole) | `scraper.py:226-230` | V-code | High |
| R7 | `"Pending"` default | P2 | **P2** | `parser.py:330`, `models.py:99` | V-repro | High |
| R8 | Two date formats as text | P2 | **P2** | outputs | V-data (40 + 40 values) | High |
| R9-10 | Package import requires openpyxl; tests can't run | P3 | **P2** (blocks all testing) | `deeper_tool/__init__.py:20`, `exporter.py:15` | V-code + observed `ModuleNotFoundError` | High |
| N5/N6 | Partial-run naming; empty list exports whole DB | — | P2 / P3 | see 1.2 | V-code | High |
| N8 | Masked parties unflagged | — | P2 | data | V-data | High |

---

## 2. Pipeline Reconstruction (Task A)

| Stage | File : function | Input → Output | Discarded | Failure behaviour |
|---|---|---|---|---|
| Session | `scraper.py:83 init_session` | GET index + components.js → `app_token`, `delimeter`, header name | — | Regex misses fall back silently (`""`, `"uiryhjtry57"`, `None`). No status check. |
| Establishment | `scraper.py:155 set_establishment` | codes → server session state | response (`differ_mast_est`) | Unchecked |
| Captcha | `scraper.py:142 solve_captcha` | `getCaptcha` → OCR text | — | `""` causes a retry (max 5) |
| Search | `scraper.py:193 search_cases` | type/year/status/codes → `case_data` HTML | `errormsg`, `status`, `div_captcha` | `[]` after 5 tries. N1. R6. |
| Row parse | `ecourts/parser/search.py:112 parse_case_results` | HTML → row dicts (`sr_no`, `case_number`, `petitioner`, `respondent`, `court_name`, `cnr_number`, `view_params`, `raw_cells`) | rows with fewer than 4 tds | Raises `WrongCaptchaError`. `"no record"` substring returns `[]`. |
| Selection | `scraper.py:262 batch_extract` | rows → `rows[:20]` | rows 21+, all row fields except `view_params` | Rows without `view_params` are skipped (printed) |
| Detail fetch | `scraper.py:166 fetch_view_history` | `view_params` → `data_list` HTML | all other JSON keys, raw HTML | `""` on any error, which leads to `ValueError` |
| Parse | `parser.py:97 parse_deeper_case_detail` | HTML → `DeeperCaseDetail` | `next_hearing_date`, unmapped labels (Sub Stage, Case Stage), all onclick args except order onclick, aria-labels, 2nd+ order tables, 2nd+ main matters, 2nd+ advocate lines, IA/other sections | Blank CNR; `"Pending"` default |
| Enrich | `scraper.py:243-250` | meta → court codes | `view_params` codes | — |
| Persist | `db.py:152 save_case` | case → upsert + delete/insert children | history of prior values | `ValueError` on blank CNR, caught upstream as a printed error |
| Export | `exporter.py` | DB → JSON (full), CSV (counts only), XLSX (no e-filing or court codes) | see inventory | `openpyxl` import at module load |

---

## 3. Architecture Decision (Revision 2: preserve and extend the existing live scraper)

> **Revision 2 (2026-11-12) supersedes Revision 1.** `deeper_tool` is a working live scraper and is the foundation. Every change below edits the smallest part of an existing function, keeps its signature, and is covered by a regression test proving existing behaviour still holds plus a test proving the defect is fixed. Revision 1 proposals that would have replaced working components are withdrawn (section 3.3).

### 3.1 Preservation register

These components are kept as they are. A change is allowed only when it fixes the specific defect listed.

| Component | Location | Kept | Allowed targeted change | Defect that justifies it |
|---|---|---|---|---|
| Session handshake | `scraper.py:83 init_session` | Request sequence, token/delimiter/custom-header scraping, fallbacks | Log a warning when a regex falls back (no behaviour change) | Silent fallback (R-obs) |
| curl transport | `scraper.py:58 _curl` | `curl.exe`, cookie jar, headers, pacing, signature, return types | Add `--connect-timeout`/`--max-time`, `subprocess.run(timeout=)`, and append `-w` http code (parsed off, then stripped). Check `returncode`. Raise typed errors **only** on timeout, non-2xx or curl failure. | R1, R2 (V-code) |
| AJAX wrapper | `scraper.py:114 _post_api` | Headers, token rotation, return `dict` | Keep returning `{}` on non-JSON, but also record the failure (see 1.1) | R3/N10 |
| Establishment selection | `scraper.py:155 set_establishment` | Single `set_data` call with `complex@est@Y` | **None.** The browser HAR makes two calls (empty est, then est), but the existing single call worked live. Not changed without a demonstrated failure. | — |
| CAPTCHA | `scraper.py:142 solve_captcha`, `LocalOcrSolver` | Flow and solver | Optional `solver=` constructor argument (default = current `LocalOcrSolver()`), so the approved policy can be applied without editing the flow | Policy (section 3.4) |
| Search | `scraper.py:193 search_cases` | Payload, retry loop, return `List[Dict]` | Detect `errormsg` and case-insensitive captcha text; catch `WrongCaptchaError`; persist rows and raw body as a side effect | N1, R6 (V-code) |
| Search-row parser | `ecourts/parser/search.py` | Unchanged | none | — |
| viewHistory request | `scraper.py:166 fetch_view_history` | Payload, headers, return `str` | Store the raw body before returning (side effect); expose `self.last_raw_response_id` | B (V-code) |
| Parser | `deeper_tool/parser.py` | All currently correct extractions (golden-tested) | In-place fixes per finding (section 10) | A, E1, E2, H1, H2, C, D, N2, N3 |
| DB | `deeper_tool/db.py` | Existing tables, columns, `save_case`/`get_case` signatures, delete/reinsert of children | Additive columns and tables via an in-file migration step in `_init_schema` | B, N4, R4 |
| Exports | `deeper_tool/exporter.py` | Existing JSON keys, CSV columns and order, XLSX sheets and headers | Append new keys/columns/sheets at the end; lazy `openpyxl` import; `[]` vs `None` fix | R9-10, N6 |
| Run scripts | `batch_extract_20.py`, `run_single_case.py` | Flow and outputs | Print the failure summary; non-zero exit on failures | N5 |

### 3.2 Decisions

- **Parser:** fix in place inside `deeper_tool/parser.py`. Port the base parser's `next_hearing_date`, `case_stage` and IA logic **into** it (no new parser module, no shared-helper extraction, and `ecourts/parser/case_details.py` is untouched).
- **Transport:** keep curl. Make the targeted `_curl` changes above. Offline tests patch `deeper_tool.scraper.subprocess.run` (or `DeeperScraper._curl`) with scripted responses. **No new transport module.**
- **Retry:** a small bounded retry *inside* `fetch_view_history`, for transient errors only. `batch_extract` keeps its loop and return type (`List[DeeperCaseDetail]`), and additionally records each failure in a `fetch_errors` table and exposes `self.last_run_summary`.
- **Resumability:** `batch_extract(..., skip_existing=False)`. When true, it skips CNRs that already have a raw response and a saved case. A job queue is **deferred** and only built if Phase 7 scale requires it (the `ecourts` worker exists if needed, but it swallows detail failures).
- **Hierarchy enumeration (Phase 7):** a `DeeperScraper.list_hierarchy()` method that calls the fill* endpoints through the existing `_post_api` and parses them with the existing `ecourts.parser.hierarchy`. No new module.
- **Dates:** a private `_to_iso()` in `parser.py`, populating *new* `*_iso` fields. Existing raw date fields keep their exact current values.

### 3.3 Withdrawn from Revision 1

The following Revision 1 items are withdrawn:
- the `transport.py` module (CurlTransport/RequestsTransport/FakeTransport)
- the `captcha_provider.py` module
- the `enumerate.py` module
- the `dates.py` module
- the `migrations.py` module (folded into `db.py`)
- the shared `ecourts/parser/_html.py`
- the job-queue rewrite of `batch_extract`
- changing `fetch_view_history` to return a tuple
- replacing `main_matters` with `linked_cases` plus a view
- the natural-key upsert of hearings (deferred to Phase 4, where proceedings need it)
- mirroring the browser's two-step `set_data`

### 3.4 CAPTCHA and access policy (owner decision)

- The existing OCR flow is preserved unchanged.
- An automated live probe on that flow was blocked in this audit session. **The project owner must confirm, before any further live run, that this flow is permitted** under the portal's terms or an access arrangement.
- The only change offered is the optional `solver=` argument, so an operator-entry solver can be passed in if the policy requires it.
- Nothing bypasses CAPTCHAs or session controls.

### 3.5 Live-compatibility evidence (checked 2026-11-12 against `fixtures/services.ecourts.gov.in 2.har`)

| Request | HAR | deeper_tool | Match |
|---|---|---|---|
| `getCaptcha` | body `ajax_req=true&app_token=`; AJAX headers (Accept json, form content type, Origin, X-Requested-With) | same keys + headers (`_post_api`) | **Yes** |
| `set_data` | `complex_code=1030135@3@Y&selected_state_code=3&selected_dist_code=20&selected_est_code=3` (2nd call) | identical keys/values (`scraper.py:157-162`) | **Yes** (2nd-call form) |
| `submit_case_type` | `case_type_1, search_year, case_status, ct_captcha_code, state_code, dist_code, court_complex_code, est_code` | identical keys (`scraper.py:214-223`) | **Yes** |
| Referer | `https://services.ecourts.gov.in/` | `…/?p=casestatus/index` | Differs; deeper's value worked live, so keep it |
| `delimeter` / custom header, cookies, `app_token` value | **absent in HAR** (sanitised or older capture) | sent | **Cannot verify** |
| `casestatus/index`, `components.js`, `home/viewHistory` | **not in HAR** | used | **Cannot verify** from captures. The only evidence is the real viewHistory fixture and the 20-case outputs, which prove the workflow worked when they were produced. |

**Rule for later phases:** every change to `_curl`, `_post_api`, `init_session`, `set_establishment`, `solve_captcha`, `search_cases` or `fetch_view_history` must pass a **request-construction regression test**. The test captures the curl argv / form body produced by the patched code and asserts that it is identical to the current code's output for the same inputs, apart from the explicitly added flags (`--max-time`, `--connect-timeout`, `-w`). That is the strongest live-compatibility guarantee possible offline. Real live verification is a separate, owner-authorised step (section 14).

---

## 4. Canonical Schema (Task B)

SQLite, one file (`deeper_ecourts.db`), additive migration from the current DDL. Conventions:
- Natural keys get `UNIQUE` constraints so re-runs are idempotent upserts.
- Every row carries `source_response_id` (provenance) and `first_seen_at` / `last_seen_at`.
- Dates are stored twice: `*_raw` (verbatim) and `*_iso` (`YYYY-MM-DD`, or NULL if unparseable).

> **Revision 2: phased, additive adoption of this schema.** The target model below is unchanged as a destination, but it is reached by additive steps only. Existing tables and columns are never renamed or dropped.
>
> - **Phases 1–2 add:**
>   - `raw_responses`, `fetch_errors`, `search_runs`, `search_results`, `schema_version`
>   - new nullable columns on `cases`, `hearings` (`hearing_seq`, `vb_*`, ISO dates) and `orders` (section, category, description, `pdf_args_json`)
>   - `parties.party_seq` plus `parties.advocates_json`. This replaces the `party_advocates` table: the existing `advocate` column keeps the first advocate, unchanged.
>   - `main_matters.seq`. This replaces `linked_cases`: the existing `main_matters` table stays the store, and a `linked_cases` table is added only if real captures show other relation types.
> - **Phase 4+ adds:** `proceedings`, `documents`, `interim_applications`.
> - **`jobs` is deferred** (see section 3.2).
> - **Kept as-is:** `save_case`'s delete/reinsert of children until Phase 4. From then on, hearings and orders need natural-key upserts, because proceedings and documents reference them.

### 4.1 Which entities need their own table

| Requested entity | Decision | Why |
|---|---|---|
| Case | table `cases` (existing, extended) | root |
| Party | table `parties` (existing, extended) | 1..n per case, ordered |
| Advocate | table `party_advocates` (`party_id`, `seq`, `name`) | Parties can list several advocates. The site provides **no advocate identifier**, so a global advocate master would be guesswork. Advocate names are queryable through this table. |
| Hearing | table `hearings` (existing, extended) | 1..n, needs a stable key |
| Proceeding | table `proceedings` (1:1 with hearing) | A separate fetch with its own lifecycle (fetched / failed / not available) and its own raw response. Folding it into `hearings` would mix states. |
| Order | table `orders` (existing, extended) | 1..n; interim and final distinguished by a column, **not** a separate table |
| Document | table `documents` | A binary with hash, MIME type and path. One order may have 0..1 now, n later. Content-hash dedupe needs its own table. |
| Interim Application | table `interim_applications` | Distinct columns (IA no, party, status, dates). **Columns UNVERIFIED**, so start with core columns plus `extra_json`. |
| Connected Case + Main Matter | **one** table `linked_cases` with `relation_type` (`main_matter`, `connected`, `linked`, `lower_court`, `transfer`) | Same shape (case no, CNR, filing no). The relation type is the only difference. Keep a `main_matters` **view** for backward compatibility. |
| Process | table `processes` (existing) | unchanged plus provenance |
| Act | table `acts` (existing) | unchanged plus `section_norm` |
| Supporting | `raw_responses`, `search_runs`, `search_results`, `jobs`, `fetch_errors`, `schema_version` | provenance, coverage, resumability |

### 4.2 DDL sketch

```sql
-- provenance -----------------------------------------------------------
CREATE TABLE raw_responses (
  id INTEGER PRIMARY KEY,
  endpoint TEXT NOT NULL,              -- 'home/viewHistory', 'casestatus/submit_case_type', ...
  request_params_json TEXT NOT NULL,   -- app_token/captcha REDACTED
  http_status INTEGER, fetched_at TEXT NOT NULL,
  body_sha256 TEXT NOT NULL, body BLOB NOT NULL,  -- gzip; or body_path for large bodies
  cnr_number TEXT,                     -- requested cino, if any
  parser_version TEXT                  -- parser version that last processed it
);
CREATE INDEX ix_raw_cnr ON raw_responses(cnr_number, endpoint, fetched_at);

-- case -----------------------------------------------------------------
ALTER TABLE cases ADD COLUMN case_status_raw TEXT;      -- verbatim cell text
ALTER TABLE cases ADD COLUMN case_sub_stage TEXT;       -- NULL = label absent, '' = label present & empty
ALTER TABLE cases ADD COLUMN case_stage TEXT;
ALTER TABLE cases ADD COLUMN next_hearing_date_raw TEXT;
ALTER TABLE cases ADD COLUMN next_hearing_date_iso TEXT;
ALTER TABLE cases ADD COLUMN first_hearing_date_iso TEXT;
ALTER TABLE cases ADD COLUMN decision_date_iso TEXT;
ALTER TABLE cases ADD COLUMN filing_date_iso TEXT;
ALTER TABLE cases ADD COLUMN registration_date_iso TEXT;
ALTER TABLE cases ADD COLUMN court_no TEXT;             -- split from court_number_judge ('1147')
ALTER TABLE cases ADD COLUMN judge_designation TEXT;
ALTER TABLE cases ADD COLUMN national_court_code TEXT;  -- e.g. 'KABC01' (from onclick args)
ALTER TABLE cases ADD COLUMN view_params_json TEXT;     -- re-fetch key (N4)
ALTER TABLE cases ADD COLUMN parties_masked INTEGER DEFAULT 0;   -- N8
ALTER TABLE cases ADD COLUMN unparsed_sections_json TEXT;        -- catch-all for unknown h3 sections
ALTER TABLE cases ADD COLUMN source_response_id INTEGER REFERENCES raw_responses(id);
ALTER TABLE cases ADD COLUMN first_seen_at TEXT;
-- case_status stays: normalised value ('Case disposed'), no default

-- parties / advocates --------------------------------------------------
ALTER TABLE parties ADD COLUMN party_seq INTEGER;       -- ordinal from '1)'
ALTER TABLE parties ADD COLUMN raw_text TEXT;
CREATE UNIQUE INDEX ux_parties ON parties(case_cnr, type, party_seq);
CREATE TABLE party_advocates (
  id INTEGER PRIMARY KEY, party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL, name TEXT NOT NULL, UNIQUE(party_id, seq));
-- parties.advocate kept (first advocate) for backward compatibility

-- hearings / proceedings -----------------------------------------------
ALTER TABLE hearings ADD COLUMN hearing_seq INTEGER;    -- 1 = OLDEST; stable as hearings are added (N3)
ALTER TABLE hearings ADD COLUMN business_date_iso TEXT;
ALTER TABLE hearings ADD COLUMN hearing_date_iso TEXT;
ALTER TABLE hearings ADD COLUMN vb_next_date_key TEXT;  -- viewBusiness arg 3 (yyyymmdd)
ALTER TABLE hearings ADD COLUMN vb_status_flag TEXT;    -- arg 6 ('Disposed'/'DisposedP')
ALTER TABLE hearings ADD COLUMN vb_court_no TEXT;       -- arg 8
ALTER TABLE hearings ADD COLUMN vb_srno TEXT;           -- arg 11
ALTER TABLE hearings ADD COLUMN vb_args_json TEXT;      -- all 11 args verbatim
CREATE UNIQUE INDEX ux_hearings ON hearings(case_cnr, hearing_seq);
CREATE TABLE proceedings (
  id INTEGER PRIMARY KEY, hearing_id INTEGER NOT NULL UNIQUE REFERENCES hearings(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('fetched','failed','unavailable')),
  business_text TEXT, next_purpose TEXT, next_date_raw TEXT, next_date_iso TEXT,
  extra_json TEXT,                      -- fields UNVERIFIED until a real response is captured
  source_response_id INTEGER REFERENCES raw_responses(id), fetched_at TEXT);

-- orders / documents ---------------------------------------------------
ALTER TABLE orders ADD COLUMN order_section TEXT;       -- heading of the table it came from
ALTER TABLE orders ADD COLUMN order_category TEXT;      -- from aria-label, before '|' ('Interim Orders')
ALTER TABLE orders ADD COLUMN order_description TEXT;   -- full aria-label
ALTER TABLE orders ADD COLUMN order_date_iso TEXT;
ALTER TABLE orders ADD COLUMN pdf_args_json TEXT;       -- displayPdf args, split
ALTER TABLE orders ADD COLUMN pdf_args_captured_at TEXT;-- tokens assumed session-bound
CREATE UNIQUE INDEX ux_orders ON orders(case_cnr, order_section, order_number, order_date);
CREATE TABLE documents (
  id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
  case_cnr TEXT NOT NULL REFERENCES cases(cnr_number),
  sha256 TEXT NOT NULL UNIQUE, mime_type TEXT, size_bytes INTEGER,
  original_filename TEXT, source_url TEXT, storage_path TEXT NOT NULL,
  fetched_at TEXT NOT NULL, source_response_id INTEGER REFERENCES raw_responses(id));

-- linked cases / IA ----------------------------------------------------
CREATE TABLE linked_cases (
  id INTEGER PRIMARY KEY, case_cnr TEXT NOT NULL REFERENCES cases(cnr_number) ON DELETE CASCADE,
  relation_type TEXT NOT NULL, seq INTEGER NOT NULL,
  linked_case_number TEXT, linked_cnr TEXT, linked_filing_number TEXT, extra_json TEXT,
  UNIQUE(case_cnr, relation_type, seq));
CREATE INDEX ix_linked_cnr ON linked_cases(linked_cnr);
-- main_matters: keep the table during transition; dual-write, then turn it into a VIEW over linked_cases
CREATE TABLE interim_applications (
  id INTEGER PRIMARY KEY, case_cnr TEXT NOT NULL REFERENCES cases(cnr_number) ON DELETE CASCADE,
  seq INTEGER NOT NULL, ia_number TEXT, party TEXT, filing_date_raw TEXT, next_date_raw TEXT,
  status TEXT, extra_json TEXT, UNIQUE(case_cnr, seq));

-- coverage & jobs ------------------------------------------------------
CREATE TABLE search_runs (id INTEGER PRIMARY KEY, state_code TEXT, dist_code TEXT,
  court_complex_code TEXT, est_code TEXT, case_type TEXT, search_year TEXT, case_status TEXT,
  started_at TEXT, finished_at TEXT, row_count INTEGER, outcome TEXT, source_response_id INTEGER,
  UNIQUE(state_code,dist_code,court_complex_code,est_code,case_type,search_year,case_status,started_at));
CREATE TABLE search_results (id INTEGER PRIMARY KEY, search_run_id INTEGER NOT NULL REFERENCES search_runs(id),
  sr_no TEXT, case_number TEXT, petitioner TEXT, respondent TEXT, court_name TEXT,
  cnr_number TEXT, view_params_json TEXT, raw_cells_json TEXT, UNIQUE(search_run_id, sr_no));
CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_type TEXT NOT NULL,   -- search|detail|proceeding|document|linked
  job_key TEXT NOT NULL, params_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','dead')),
  attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 3, next_attempt_at TEXT,
  last_error_class TEXT, last_error TEXT, run_id TEXT, updated_at TEXT,
  UNIQUE(job_type, job_key));
CREATE TABLE fetch_errors (id INTEGER PRIMARY KEY, run_id TEXT, job_id INTEGER, endpoint TEXT,
  cnr_number TEXT, error_class TEXT, message TEXT, http_status INTEGER,
  raw_response_id INTEGER, occurred_at TEXT);
CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
```

### 4.3 Identifiers

| Entity | Key |
|---|---|
| Case | `cnr_number`. Must equal the requested `cino` (N2 guard). |
| Party | (`case_cnr`, `type`, `party_seq`), where `party_seq` comes from the `N)` prefix, or the ordinal if absent |
| Hearing | (`case_cnr`, `hearing_seq`), where `hearing_seq` = position counted **from the oldest row**. If `vb_srno` turns out to be monotonic in real data, prefer it; it is UNVERIFIED (`0`, `31` … `1` in the fixture suggests a reverse sequence). |
| Order | (`case_cnr`, `order_section`, `order_number`, `order_date`) |
| Document | `sha256` of the content |
| Linked case | (`case_cnr`, `relation_type`, `seq`), plus an index on `linked_cnr` for graph traversal |

**Child-row upserts:** children keep delete-and-reinsert only inside one transaction **after** a successful parse, and only for sections actually present in the page. Proceedings and documents are **never** deleted on re-parse. They hang off `hearing_id` / `order_id`, so hearings and orders must be **upserted by natural key**, not deleted and reinserted (otherwise the cascade would drop fetched proceedings).

---

## 5. Recovery Plan (Task C)

**Current state:** `deeper_ecourts.db` does **not** exist. The only persisted data is `execution_cases_20_deep.json` (20 cases), plus `.csv` and `.xlsx`, and `case_EX_2_2023.json`. No raw HTML exists except `fixtures/target_case_view.html` (1 case).

### 5.1 Recoverable offline (no portal access)

A new one-off script, `deeper_tool/tools/import_legacy_json.py`, reads the JSON into the new schema in a **new** DB file. The source files are never modified.

| Item | Method | Cases |
|---|---|---|
| `case_status` | Strip a trailing `Sub Stage` token. Keep the original in `case_status_raw`. Set `case_sub_stage = NULL` (unknown). | 11 |
| Date ISO columns | Parse `dd-mm-yyyy` and `02nd January 2023` | 20 |
| `hearing_seq` | Reverse the page order | 301 hearings |
| `section_norm` | `','` → NULL | 20 |
| `court_no` / `judge_designation` | Split on the first `-` | 20 |
| `linked_cases` | From `main_matters` | 14 rows |
| `pdf_args_json` | Split from `pdf_params` (tokens marked stale: `pdf_args_captured_at` unknown) | 20 |
| Fixture case | Full re-parse with the new parser, including viewBusiness args and aria-label | 1 |

### 5.2 Requires a new authorized fetch

| Item | Why |
|---|---|
| Sub Stage value (if any), Case Stage, next hearing date | Not in stored text |
| viewBusiness args, order aria-label | Only in HTML, which was discarded (except for the fixture case) |
| Proceedings text | Never fetched; endpoint UNVERIFIED |
| Order PDFs | Never fetched; stored tokens almost certainly expired |
| Main-case records | 14 linked CNRs, never fetched |
| Search rows 21+ and pre-slice count | Never stored |

**Re-fetch path:** `view_params` were never stored (N4), so one authorized search is needed (`23^3`, 2023, Disposed, est 3) to rebuild them.
- A `case_no` pattern can be inferred from two samples (`20`+`23`+`0000002`+`2023` and `20`+`46`+`0000001`+`2025`), but it is **UNVERIFIED**. Do not rely on it.
- Alternatively, use the CNR-search route the `ecourts` worker supports (`cnr_search` task). Its payload and captcha requirement must be confirmed from a capture.

### 5.3 Duplicate prevention on repeated runs

- UNIQUE natural keys (section 4.3) with upserts.
- `documents.sha256` UNIQUE.
- `jobs (job_type, job_key)` UNIQUE.
- `raw_responses` is append-only. Dedupe by `body_sha256` per (`cnr`, `endpoint`) to avoid storing identical bodies twice.

---

## 6. Linked-Content Extraction Design (Task D)

**Gate G1, blocking Phases 4 and 5:** the endpoints and parameters behind `viewBusiness`, `displayPdf`, `funView_CinoHistory` and `display_case_acknowlegement` are **unknown**. No HAR, fixture or code in the repo contains them.

**Unblock by** having a person capture, in a normal browser session, a HAR of: opening one case, clicking one Business-on-Date link, clicking one order link, and clicking the main-matter CNR link. Also save the site JS files loaded on that page. The HAR and JS then become fixtures. No automation is needed for this.

**Hearings:**
- The parser extracts all 11 `viewBusiness` args per row into `vb_*` columns and `vb_args_json`. This needs no new requests and ships in Phase 2.
- After G1, the `proceeding` job is keyed by (`cnr`, `hearing_seq`). It posts to the discovered endpoint with the args from the same page, stores the raw response, parses it into `proceedings`, and links it by `hearing_id`.
- Status is `unavailable` when the site returns no business text, so the job is not retried forever.

**Orders:**
- The parser iterates over **all** `order_table`s.
- `order_section` comes from the nearest preceding `<h3>` / `#orderheading`.
- `order_category` / `order_description` come from the aria-label.
- `pdf_args_json` holds the split args.
- **Assumption (explicit):** the tokens are session-bound. PDF download therefore runs **in the same session** immediately after the viewHistory fetch, or the job re-fetches viewHistory first to get fresh tokens.
- Each document stores: `sha256`, the MIME type (from `Content-Type` and magic bytes, with a check for `%PDF`), the original filename (from `Content-Disposition` or the URL basename), `source_url`, and a size cap.
- An HTML or error body served in place of a PDF is recorded as `fetch_errors`, not as a document.

**Connected matters:**
- Rows are written to `linked_cases`. If `linked_cnr` is not yet in `cases`, enqueue a `linked` job with `job_key = linked_cnr`.
- `jobs` UNIQUE prevents repeats and cycles.
- `max_link_depth` (default 1) is stored in `params_json`.
- The fetch route for a CNR (`funView_CinoHistory`) depends on G1.

---

## 7. Search Coverage (Task E)

**Constraints established from repository evidence:**
- One search = one tuple (state, district, complex, establishment, case type, year, status) **plus one CAPTCHA**. (V-HAR: `submit_case_type` params.)
- Enumeration is possible with endpoints already seen in the HAR:
  - `fillDistrict` → `fillcomplex`: values look like `1030134@2,5,10,11,12,13,14@Y`, so a complex can contain several establishments.
  - → `fillCourtEstablishment` → `fillCaseType`: case-type values look like `23^3`, and the list is **per establishment**.
- `ecourts/parser/hierarchy.py` already parses all four lists. No state-list endpoint was observed, so the state code stays configuration.
- `set_data` is called twice in the browser (first with an empty est, then with the est). Mirror that.
- **Pagination: UNVERIFIED.** The search JSON has no page or total keys. The 99-row `cases_100_export.csv` disproves a 100-row cap, but nothing proves the absence of a larger cap.
  - **Mitigation:** store the raw `case_data` per run in `search_runs.row_count`, and add a completeness heuristic. Compare the row count with the maximum registration number seen for that type and year. If the gap exceeds a threshold, flag `outcome='suspect_truncated'`.
- `case_status=Both` is accepted by the CLI but is unverified on the portal. Run Pending and Disposed separately.
- Masked parties (`hideparty`, `XXXXXXX`) are detected and flagged.

**Design:**
- `deeper_tool/enumerate.py` builds `search` jobs from configured scopes (lists of districts, complexes, est, types, years, statuses), resolved by the fill* endpoints and **cached** in a `court_hierarchy` cache file.
- Each search job persists `search_runs` + `search_results`, then enqueues one `detail` job per row (no slicing; `limit` becomes an optional cap recorded on the run).
- The job queue resumes after a crash: claim queued or expired-running jobs.
- **Throughput is bounded by the CAPTCHA.** That needs the human-in-the-loop decision in section 3 before any broad run.

---

## 8. Reliability and Observability (Task F)

| Concern | Design |
|---|---|
| Timeouts | curl `--connect-timeout 15 --max-time 60`; `subprocess.run(timeout=75)` |
| Status validation | curl `-w "\n%{http_code}"`. Non-2xx raises `HttpError(status)`. A non-JSON reply to an ajax call raises `UnexpectedResponse`. |
| Error classes | `TransientError` (timeout, 5xx, connection) → retry. `SessionExpired` (missing or rotated token, login page, empty `data_list` with an error key) → one `init_session` re-handshake, then retry. `CaptchaRejected` → new captcha, bounded. `AccessDenied` (403, block page) → **stop the run**, no retry. `ParseError` / `CnrMismatch` → fail the job, no retry, raw kept. |
| Retries | max 3, exponential backoff 2s/4s/8s + jitter, **only** for transient errors and session-expired. Respect the existing pacing floor (≥2 s). |
| Failure ledger | Every failure writes a `fetch_errors` row (run_id, cnr, endpoint, error class, message, http status, raw_response_id) and is counted in `last_run_summary`. *(Revision 2: no `jobs` table until Phase 7 needs it.)* |
| CAPTCHA | Check `errormsg` **and** `case_data`, case-insensitively, inside `search_cases`. Catch `WrongCaptchaError`. Use the `CaptchaProvider` interface (human by default). |
| Status default | No default. A missing label gives `case_status=NULL` and a validation warning. |
| Validation | After parse, before save: CNR format, CNR == requested `cino`, required sections present (case details table), date parse rate, row counts vs link counts (hearing rows == viewBusiness links). Failure means the job fails and the raw response is kept. |
| Run statistics | A `run_id` per invocation. On exit, print and save a JSON summary: searched, rows, details ok/failed/dead, proceedings, documents, errors by class. **The exit code is non-zero if any job is dead.** |
| Structured logging | `logging` with a JSON formatter option. Every record carries `run_id`, `job_id`, `cnr`, `endpoint`. Replace the `print` calls in `batch_extract`. |
| Idempotency | Natural-key upserts (section 4.3); jobs UNIQUE; append-only raw store |
| Export safety | `cnr_list is None` → all; `[]` → nothing (N6). Output filenames derived from the actual count, or a `run_id`. |

---

## 9. Implementation Roadmap (reordered for dependencies)

The requested order is Phase 1 = critical fixes, then Phase 2 = schema. The critical fixes **need** new columns and tables (raw store, failure ledger, sub stage, `vb_*`), and nothing can be tested until the openpyxl import is fixed. So a Phase 0 foundation comes first, and schema plus critical fixes ship together.

| Phase | Content | Depends on |
|---|---|---|
| **0. Foundation** | openpyxl isolation; tests runnable and in `run_tests.py`; transport interface + fake transport; fixture library (real + synthetic); schema migration framework | — |
| **1. Stop silent loss** | Raw-response store; CNR == cino guard; failure ledger + run summary; timeouts and status checks; `errormsg` / `WrongCaptchaError` handling; persist `view_params` and search rows; no `Pending` default; export-safety fix | 0 |
| **2. Parser completeness + canonical schema** | Separator-aware cell reading; status/sub-stage split; `next_hearing_date`, `case_stage`; all order tables + aria-label + split pdf args; all main matters → `linked_cases`; `vb_*` hearing args; stable `hearing_seq`; party seq + multiple advocates; masked flag; ISO dates; catch-all unknown sections; shared helpers module | 0, 1 |
| **3. Legacy recovery** | `import_legacy_json.py` into a new DB; repair report | 2 |
| **G1 gate** | Human-captured HAR + site JS for viewBusiness / displayPdf / CNR-history; real pending, IA, criminal case fixtures | external |
| **4. Proceedings** | proceeding jobs + parser | 1, 2, G1 |
| **5. Orders & documents** | in-session PDF download, documents table | 1, 2, G1 |
| **6. Linked cases + pending/IA** | linked jobs with depth/cycle guard; IA parser from real fixture | 2, G1 (IA fixture) |
| **7. Search coverage** | enumeration, search jobs, resumable queue, truncation heuristic | 1, 2; live runs need the CAPTCHA-policy decision |
| **8. Hardening** | full regression suite, structured logging polish, performance (last) | all |

Phases 0–3 need **no portal access** and remove all silent loss that can be fixed offline. Phases 4–7 are gated on captures and policy.

**Revision 2 notes:**
- Phase 0 no longer includes a transport interface or a migration module. It is: openpyxl isolation, test runner, fixture library, and the **request-construction snapshot tests** taken against the *unmodified* code. These snapshots are the baseline every later phase must match.
- Phase 1 uses the targeted edits R2-0.5, R2-0.7, R2-1.2, R2-1.5.
- Phase 7 uses R2-7.1/R2-7.2 and changes no existing search code.
- **Before any phase is declared live-compatible:**
  - its request-construction snapshot tests must pass;
  - the golden fixture output must be unchanged for existing fields;
  - and, if the owner authorises it, a single-case live smoke run (EX/2/2023 via `run_single_case.py` with the fixture temporarily absent) must produce output identical to the stored `case_EX_2_2023.json` for existing fields.

---

## 10. File-Level Implementation Plan

> **Revision 2 overrides.** Rows 0.5, 0.6, 0.7, 1.2, 1.5, 1.8, 2.1, 2.5, 2.8, 2.10, 6.1, 7.1 and 7.2 below are replaced by the "R2-" rows in section 10.1. All other rows stand. Every row carries two test obligations: (1) a **regression** test showing existing behaviour is unchanged (golden fixture output, existing JSON keys, CSV columns, request construction), and (2) a **defect** test showing the fix.

| # | File | Function / class | Change | Depends | Tests |
|---|---|---|---|---|---|
| 0.1 | `deeper_tool/__init__.py` | module | Stop importing `exporter` and `scraper` eagerly. Export them lazily via `__getattr__`. Existing `from deeper_tool import X` keeps working. | — | `test_import_without_openpyxl` (patch `sys.modules['openpyxl']=None`); existing import names all resolve |
| 0.2 | `deeper_tool/exporter.py` | `export_to_multisheet_excel` | Move `import openpyxl` inside the function; raise a clear `RuntimeError("pip install .[excel]")` | — | xlsx test `skipUnless(openpyxl)` |
| 0.3 | `pyproject.toml`, `setup.py` | packaging | Add `deeper_tool*` to packages. Add the optional extra `excel = ["openpyxl>=3.1"]`. | — | `pip install -e .[excel]` in CI |
| 0.4 | `run_tests.py` | runner | Include `deeper_tool/test_deeper.py` (or move it to `tests/deeper/`) | 0.1 | runner exit code |
| 0.5 | *(withdrawn, see R2-0.5)* | | | | |
| 0.6 | *(withdrawn, see R2-0.5)* | | | | |
| 0.7 | *(withdrawn, see R2-0.7)* | | | | |
| 0.8 | `deeper_tool/fixtures/synthetic/` (new) | — | `sub_stage_*.html`, `two_order_tables.html`, `two_main_matters.html`, `pending_next_date_stage.html`, `no_cnr_row_with_main_cnr.html`, `multi_advocate.html`, `masked_parties.html`, search `errormsg.json`, `record_not_found.json` | — | used by everything below |
| 1.1 | `deeper_tool/db.py` | `DeeperDatabase` | `save_raw_response()`, `record_error()`, `upsert_search_run/results()`, jobs CRUD (`claim`, `complete`, `fail` with backoff) | 0.7 | unit tests per method |
| 1.2 | `deeper_tool/scraper.py` | `fetch_view_history` | Return `(html, raw_id)`; store the raw body before parsing; raise typed errors instead of `""` | 1.1, 0.5 | error JSON → `SessionExpired`; body stored even when the parse fails |
| 1.3 | `deeper_tool/scraper.py` | `scrape_and_save_case` | Validate `case.cnr_number == view_params['cino']`, otherwise `CnrMismatch`. Persist `view_params_json` and `source_response_id`. Take court codes from `view_params`, with meta as fallback. | 1.2 | `no_cnr_row_with_main_cnr.html` → mismatch, no DB write |
| 1.4 | `deeper_tool/scraper.py` | `search_cases` | Pass the full JSON to `parse_case_results`; catch `WrongCaptchaError`; persist `search_runs/results` + raw | 1.1 | `errormsg.json` → retry; `record_not_found` → 0 rows, outcome `empty` |
| 1.5 | `deeper_tool/scraper.py` | `batch_extract` | Replace print/skip with a job loop: ledger, retries (transient only), `run_id`, summary dict; `limit=None` default | 1.1-1.4 | 3 cases, 1 transient-then-ok, 1 permanent → summary {ok:2, dead:1}; exit code ≠ 0 |
| 1.6 | `deeper_tool/batch_extract_20.py` | `main` | Use the summary; filenames from `run_id`; non-zero exit on dead jobs | 1.5 | smoke test with `FakeTransport` |
| 1.7 | `deeper_tool/exporter.py` | 3 exporters | `cnr_list is None` vs `[]` | — | `[]` → empty file |
| 1.8 | `deeper_tool/parser.py`, `models.py` | `parse_deeper_case_detail`, `DeeperCaseDetail.case_status` | Remove the `"Pending"` default (→ `""` + warning flag) | — | missing label → `""` |
| 2.1 | `ecourts/parser/_html.py` (new) | `extract_cnr`, `parse_roster`, `read_label_value_tables` | Shared helpers. The table reader uses `get_text(" ", strip=True)` **per text node**, keeps the raw text, and returns ordered (label, value, cell) tuples. | — | existing `ecourts` tests stay green (the module is unused by `ecourts` at first) |
| 2.2 | `deeper_tool/parser.py` | label mapping | Use 2.1. Add `case stage`, `sub stage`, `next hearing date` → model. Split status vs sub stage inside one cell. Keep `case_status_raw`. | 2.1 | the 4 Sub Stage synthetic variants; fixture still gives `"Case disposed"` |
| 2.3 | `deeper_tool/parser.py` | hearings block | Extract the `viewBusiness` args (regex over the onclick); `hearing_seq` from the oldest; ISO dates | — | fixture: 32 rows, 32 `vb_args`; row 2 args == expected tuple; `hearing_seq` of the last row == 1 |
| 2.4 | `deeper_tool/parser.py` | orders block | `find_all` order tables; section heading; aria-label → category/description; split `displayPdf` args | — | `two_order_tables.html` → 2 orders, 2 sections; fixture category `Interim Orders` |
| 2.5 | `deeper_tool/parser.py` | main matters block | One item per main-case row; also emit `linked_cases` | — | `two_main_matters.html` → 2 |
| 2.6 | `deeper_tool/parser.py` | `_parse_roster` | `party_seq`; all advocate lines; masked detection | 2.1 | `multi_advocate.html`, `masked_parties.html` |
| 2.7 | `deeper_tool/parser.py` | new `_collect_unparsed_sections` | Every `<h3>` + following table not consumed → `unparsed_sections_json` | — | an unknown section is captured, not dropped |
| 2.8 | `deeper_tool/dates.py` (new) | `to_iso()` | `dd-mm-yyyy`, `02nd January 2023`, `-` → None | — | table-driven tests incl. bad input |
| 2.9 | `deeper_tool/models.py` | all dataclasses | New fields (defaults so constructors stay compatible); new `PartyAdvocate`, `LinkedCase`, `InterimApplication`, `Proceeding`, `Document` | — | `to_dict` keys superset of old |
| 2.10 | `deeper_tool/db.py` | `save_case`, `get_case`, DDL | Migration v2 (section 4.2); natural-key upserts for hearings and orders (no cascade loss); dual-write `main_matters` + `linked_cases` | 0.7, 2.9 | round-trip; re-save twice → identical row counts; proceedings survive a re-save |
| 2.11 | `deeper_tool/exporter.py` | all | Add the new fields; new XLSX sheets (Linked Cases, IA, Proceedings, Documents); CSV keeps its old columns first | 2.10 | header superset check |
| 3.1 | `deeper_tool/tools/import_legacy_json.py` (new) | `main` | JSON → new DB with the section 5.1 repairs; writes a repair report CSV; refuses to overwrite an existing DB | 2.x | 20 cases imported; 11 status fixes; 301 hearings with `seq` |
| 4.1 | `deeper_tool/proceedings.py` (new) | `fetch_proceeding`, `parse_business` | After G1 only | G1, 1.x | HAR-replay fixture |
| 5.1 | `deeper_tool/documents.py` (new) | `download_order_pdf` | Same-session download, sha256, MIME sniff, size cap | G1 | fake PDF bytes; HTML-instead-of-PDF → error |
| 6.1 | `deeper_tool/scraper.py` | new `enqueue_linked` | depth/cycle-guarded linked jobs | 2.5, G1 | A→B→A enqueues B once, never re-enqueues A |
| 6.2 | `deeper_tool/parser.py` | IA block | Port `ecourts/parser/case_details.py:272-288` once a real fixture confirms the markup | G1 fixture | real fixture |
| 7.1 | `deeper_tool/enumerate.py` (new) | `build_search_jobs` | Use `ecourts.parser.hierarchy` + fill* endpoints; cache the hierarchy | 1.1 | HAR fixture `fillcomplex` → 6 complexes, est split |
| 7.2 | *(withdrawn, see R2-7.2)* | | | | |

### 10.1 Revision 2 replacement rows (targeted edits to existing code)

| # | File | Existing function | Smallest change | Interface preserved | Regression test | Defect test |
|---|---|---|---|---|---|---|
| R2-0.5 | `deeper_tool/scraper.py` | `_curl` | Add `--connect-timeout 15 --max-time 60` and `-w "\n%{http_code}"` to argv. Strip the trailing code from stdout. Add `subprocess.run(..., timeout=75)`. Raise `ScraperHTTPError` (new exception class, in `scraper.py`) on timeout, non-zero `returncode` or non-2xx. Keep binary mode and the same return types. | Signature, return `str`/`bytes` | Patched `subprocess.run` records argv: identical to the current argv apart from the 5 added tokens. A 2xx body is returned byte-identical. | Hanging fake → `ScraperHTTPError` within the timeout; HTTP 500 → error; `returncode` 28 → error |
| R2-0.7 | `deeper_tool/db.py` | `_init_schema` | After the existing DDL loop, run ordered `ALTER TABLE ADD COLUMN` / `CREATE TABLE IF NOT EXISTS` steps, guarded by `PRAGMA table_info`, and record them in `schema_version` | `DeeperDatabase(path)` | DB built with the **current** DDL + rows → open with the new code → rows and `get_case()` output unchanged | New columns exist; second open is a no-op |
| R2-1.2 | `deeper_tool/scraper.py` | `fetch_view_history` | Before JSON parsing, call `self.db.save_raw_response("home/viewHistory", redacted_params, body, http_status, cino)`. Set `self.last_raw_response_id`. Up to 2 retries on `ScraperHTTPError` (transient) with 2/4 s backoff. On final failure call `record_error`, then keep the current behaviour (return `""`). | Return `str` | Same payload and headers (argv capture). Successful return value unchanged. | Body stored even when the JSON is bad; transient-then-ok returns HTML after a retry; error row written on final failure |
| R2-1.5 | `deeper_tool/scraper.py` | `batch_extract` | Keep the loop, prints and return list. In the `except` branch and the "no view_params" branch, also call `db.record_error(run_id, cnr, error_class, msg)`. Build `self.last_run_summary` = {total, ok, failed, skipped}. Add `skip_existing=False` and `limit` (default unchanged, 20). | Signature extended with defaults; return type unchanged | Scripted 3-case run returns the same `List[DeeperCaseDetail]` as today | 1 failing case → 1 `fetch_errors` row; summary {ok:2, failed:1} |
| R2-1.8 | `deeper_tool/parser.py` | `parse_deeper_case_detail` | **Keep** the `"Pending"` fallback for backward compatibility, but set a new field `status_assumed=True` when the label was absent, and record a validation warning | `case_status` values unchanged | Fixture status unchanged | Missing label → `status_assumed=True`. Changing the default itself needs owner sign-off (downstream consumers). |
| R2-2.1 | `deeper_tool/parser.py` | label/value loop (l.146-163) | In place: read the value cell with `get_text(" ", strip=True)` for matching only. Store the verbatim text in `case_status_raw`. Split a trailing `Sub Stage[: value]` into `case_sub_stage`. Add labels `case stage`, `sub stage`, and pass `next_hearing_date` through. **No shared module.** | Existing field values for the fixture are byte-identical (golden) | Golden fixture JSON unchanged for all existing keys | 4 Sub Stage variants; pending synthetic sets `next_hearing_date`/`case_stage` |
| R2-2.5 | `deeper_tool/parser.py` | main-matters block | Append a `MainMatterItem` per main-case row (pairing each following "Main Filing No" row with its case row) instead of overwriting. Add a `seq` field. | `main_matters` list type; existing table | Fixture still yields 1 identical item | 2-row synthetic → 2 items |
| R2-2.8 | `deeper_tool/parser.py` | new private `_to_iso` | Populate new `*_iso` fields only | Raw date fields unchanged | Golden raw dates unchanged | Table-driven ISO conversion |
| R2-2.10 | `deeper_tool/db.py` | `save_case`, `get_case` | Write and read the new columns. Keep delete/reinsert of children (until Phase 4). | Signatures | Round-trip of the fixture equals the current round-trip for old fields | New fields round-trip |
| R2-6.1 | `deeper_tool/scraper.py` | `batch_extract` | Optional `follow_main_matters=False`. When true, after a case saves, fetch the main CNRs not yet in the DB, using a `seen` set and depth 1. Fetch route needs G1. | Default off = current behaviour | Default run makes no extra requests (argv count) | A→B→A fetches B once |
| R2-7.1 | `deeper_tool/scraper.py` | new method `list_hierarchy(state_code, dist_code=None, complex_code=None, est_code=None)` | Calls `fillDistrict`/`fillcomplex`/`fillCourtEstablishment`/`fillCaseType` via the existing `_post_api` (param shapes as in the HAR) and parses them with the existing `ecourts.parser.hierarchy`. `search_cases` unchanged; a thin loop over it in a run script. | `search_cases` untouched | `search_cases` argv unchanged | HAR bodies → 6 complexes; est split |
| R2-7.2 | `deeper_tool/scraper.py` | `DeeperScraper.__init__` | Optional `solver=None` argument; `self.solver = solver or LocalOcrSolver()` | Default identical | Default constructs `LocalOcrSolver` | A custom solver is called by `solve_captcha` |

---

## 11. Testing Strategy

- **Offline only.** `FakeTransport` (scripted responses) for unit tests. `HarReplayAdapter` (existing, `ecourts/testing/har_replay.py`) for HAR-based flows once a `RequestsTransport` exists. No test touches the portal.
- **Fixtures:**
  - `fixtures/target_case_view.html` (real, golden): its parsed output is frozen as `golden_target_case.json`, and any diff fails the test.
  - Synthetic variants (0.8), each labelled synthetic in its file header.
  - Real pending / IA / criminal / business / PDF fixtures from G1.

| Requirement | Test |
|---|---|
| Status / Sub Stage | 4 synthetic variants + fixture; `case_status_raw` preserved |
| Pending vs disposed | `pending_next_date_stage.html` vs fixture |
| Optional fields missing | strip each section from the fixture → no exception, field empty, `unparsed_sections_json` unaffected |
| Multiple order tables / main matters | 2.4 / 2.5 |
| Hearing ↔ proceeding | migrate, save case, add proceeding, re-save case → proceeding still attached to the same `hearing_seq` |
| Document metadata | fake PDF and fake HTML bodies |
| Connected cases | cycle guard 6.1 |
| Pagination | search fixture with N rows → N `search_results`, no slice; truncation heuristic flagged on a synthetic gap |
| Duplicates | run the same batch twice → identical counts in every table; `documents` deduped by hash |
| Partial failure / retry | `FakeTransport` scripts: timeout → ok; 500×3 → dead; 403 → run abort; captcha errormsg → retry |
| Dates | table-driven `to_iso` |
| Migration | build a DB with the **current** DDL + legacy rows → migrate → old columns and values intact, new columns NULL, `get_case` works; idempotent re-run |
| openpyxl isolation | import parser, db and scraper with `openpyxl` blocked; XLSX test skipped, not errored |
| `ecourts` regression | full `run_tests.py` (220 tests) green after 2.1 |

---

## 12. Risk Assessment

| Risk | Mitigation |
|---|---|
| **Regression:** the separator change alters values on currently-correct fields (e.g. extra spaces) | Golden fixture test; normalise whitespace; keep the raw text |
| **Regression:** the `case_status` default change breaks consumers expecting `Pending` | Only `deeper_tool` consumers; document; empty plus warning rather than a wrong value |
| **Migration:** `ALTER TABLE` on a populated DB; SQLite cannot add UNIQUE via ALTER | Use `CREATE UNIQUE INDEX` (allowed); back up the DB file before migrating; dry-run on a copy; migration aborts if existing rows violate new unique keys (report duplicates first) |
| **Migration:** a hearings upsert switch could orphan proceedings | Proceedings only start being written after 2.10; test the re-save survival |
| **Duplicates:** `hearing_seq` shifts if the portal ever removes or reorders past rows | Store `vb_args_json` + `business_date`; on a mismatch of (`seq`, `business_date`) for existing rows, re-key and log `hearing_rekeyed` rather than silently duplicating |
| **Duplicates:** order key changes if the site renumbers orders | Include `order_date` in the key; documents deduped by hash regardless |
| **Storage:** raw HTML + PDFs grow quickly | gzip bodies; documents on disk by hash; retention config |
| **Privacy:** raw responses contain party names, and tokens/cookies end up in stored request params | Redact `app_token`/captcha from `request_params_json`; keep the DB and the raw store git-ignored (`*.db` already ignored; add `raw_store/` and `documents/`); existing output files are **not** git-ignored, so decide whether to keep them in git |
| **Portal access:** CAPTCHA per search, possible rate limits or blocks | Human captcha provider by default; pacing ≥2 s kept; `AccessDenied` stops the run; no parallelism; **owner confirms terms before broad collection** |
| **Portal access:** token-bound PDFs | Download in the same session; never rely on stored tokens |
| **Unverified site behaviour:** IA, FIR, lower court, pagination, viewBusiness payloads | The catch-all section capture keeps the data even before parsers exist; G1 captures are required before claiming support |

---

## 13. Definition of Done

| Phase | Acceptance criteria |
|---|---|
| 0 | `python run_tests.py` runs deeper and ecourts tests: 0 errors with openpyxl absent (XLSX tests skipped), all pass with it present. Parser importable without openpyxl. Migration v1→v2 test passes on a legacy-DDL DB. |
| 1 | With `FakeTransport`: 100% of fetched viewHistory bodies have a `raw_responses` row. A CNR mismatch produces 0 DB writes and 1 `fetch_errors` row. A captcha `errormsg` triggers a retry. Every case in a scripted batch ends as `done` or `dead` (none unaccounted). The run summary totals equal the input row count. The exit code is ≠ 0 when any job is dead. Requests time out in ≤ 75 s (tested with a hanging fake). |
| 2 | Fixture golden diff is clean except for the new fields. Fixture: 32/32 hearings with `vb_args`; order category `Interim Orders`; `hearing_seq` 1 = 02-01-2023. All synthetic variants pass (Sub Stage ×4, 2 order tables → 2, 2 main matters → 2, pending → `next_hearing_date` + `case_stage` set). Re-saving a case twice leaves row counts unchanged. All 220 `ecourts` tests are still green. |
| 3 | The new DB has 20 cases, 301 hearings, 20 orders, 14 linked cases. 0 rows with `case_status LIKE '%Sub Stage%'`. 11 rows with `case_status_raw` ≠ `case_status`. ISO dates populated for ≥ 99% of non-empty date fields. Source JSON/CSV/XLSX byte-identical (hash before/after). |
| G1 | HAR(s) + JS committed as fixtures covering viewHistory, viewBusiness, displayPdf, CNR history, and at least one pending case, one IA case and one criminal case |
| 4 | HAR replay: proceedings stored for 100% of hearings in the fixture case, each linked to the correct `hearing_seq`. Re-run creates 0 new proceeding rows. |
| 5 | HAR replay: the judgment PDF is stored with sha256, MIME `application/pdf`, size > 0 and a filename. An HTML-instead-of-PDF body is recorded as an error. Re-run creates 0 new documents. |
| 6 | Cycle test passes. Fixture main matter enqueued once. IA rows parsed from the real fixture match a manual count. |
| 7 | HAR replay of fill* yields 6 complexes / correct est split. A scripted search with N rows produces N `search_results` + N detail jobs. A killed-and-restarted run completes without duplicates. A synthetic truncation gap is flagged. |
| 8 | Full suite green. Coverage report for `deeper_tool` ≥ 85% lines. A run report is produced for every invocation. |

---

## 14. Open Decisions for the Project Owner

1. **CAPTCHA policy.** Is automated OCR solving permitted for this project, or should collection use the human-in-the-loop provider? This blocks Phase 7 live runs and any re-fetch.
2. **G1 captures.** Can someone record the browser HAR and JS described in section 6? This blocks Phases 4–6.
3. **Output files in git.** Should the `deeper_tool/*.json/.csv/.xlsx` outputs remain versioned once raw responses and documents (personal data) are stored locally?
4. **Live smoke-run authorisation (Revision 2).** May a single-case live smoke run (EX/2/2023 only, existing pacing) be used after Phases 1 and 2 to confirm live compatibility? Without it, compatibility rests only on request-construction snapshots and the golden fixture.
5. **`"Pending"` default.** **Decided (owner):** keep it, and add the `status_assumed` flag. Changing it needs explicit owner approval.
