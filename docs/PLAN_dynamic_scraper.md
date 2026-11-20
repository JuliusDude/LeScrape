# Plan: Case Type scraper, phased by geography, with a future-proof database

**Status:** proposal, revised 2026-11-13. Nothing here is implemented yet.
**Builds on:** `V2/` (live-tested scraper) and `docs/ECOURTS_SITE_MAP.md`.

## 1. Scope

**One search route only:** *Case Status → Case Type* (`casestatus/submit_case_type`).
- A search unit is one combination of court, case type, year and status.
- Every case found is fetched in full by V2's existing case fetcher: history, hearing business, IA, order PDFs and acknowledgement.
- The other portal search options (Party, Advocate, FIR, Cause List, and so on) are **out of scope**. The design doesn't block them later.

**Geographic phases:**

| Phase | Coverage | Portal | Known size |
|---|---|---|---|
| **1** | Bengaluru (district 20) + Bengaluru Rural (district 21), district courts | services.ecourts.gov.in | Dist 20: 6 complexes, 22 courts, **246** case-type options (enumerated). Dist 21: 7 complexes, 21 courts (complex list captured); case types not yet enumerated. |
| **2** | All 31 Karnataka districts | same | Partially enumerated: 4 districts give 33 complexes, 95 courts and 4,311 case-type options. Full sync needed. |
| **3** | All other states and UTs (36 total) | same | Not enumerated |
| **4** | High Courts | **separate portal** (hcservices.ecourts.gov.in) | Not mapped. Needs its own site map first. |

**Phases 1–3 use the same code.** Moving from one to the next is a configuration change plus a catalog sync, not new scraper code. Phase 4 needs a new portal adapter. The database must be able to hold it from day one.

## 2. What changes in the scraper

V2 already does the hard parts: session handshake, token rotation, CAPTCHA OCR, WAF back-off and case-page parsing. Three things are missing.

### 2.1 Catalog: the hierarchy as data, not CLI defaults
- `catalog sync --state 3 --district 20,21` walks `fillDistrict → fillcomplex → fillCourtEstablishment → fillCaseType (c_type)` and writes the results to the catalog tables (§3.2).
- It generalises `docs/ecourts_map/enumerate_state.py`, which already does this read-only at about 0.8 rps and resumes after interruption.
- **Seed** from the existing captures in `docs/ecourts_map/raw/` (Bengaluru and the 4 Karnataka districts done so far), so Phase 1 starts without new calls.
- Each row records `first_seen_at`, `last_seen_at` and `active`. Re-syncing marks courts or case types that have disappeared as inactive; it never deletes them, because old cases still point at them.

### 2.2 Job spec and planner
A job states *what to cover*. Wildcards expand against the catalog:

```yaml
name: phase1-bengaluru
portal: district                    # district | high_court (phase 4)
where:
  state: [3]
  district: [20, 21]                # phase 2: "*"; phase 3: state "*"
  complex: "*"
  establishment: "*"
case_types: "*"                     # or ["EX", "O.S.", "M.V.C."]
years: 2015..2025
status: [Pending, Disposed]
fetch: { pdfs: true, raw: true, follow_main_matters: true }
limits: { max_rps: 1.4, workers: 2 }
```

- **Planner:** expands the spec into `work_units`, one row per *(establishment, case type, year, status)*, which is exactly one portal search. A unit has a status (`pending / running / done / failed / empty`), attempts, `rows_found` and `site_total`.
- **Two-pass run:**
  1. **Census:** run only the searches. Each one costs one CAPTCHA and about 3–4 requests. Store every hit (unit → CNR). That gives the **real case count** per court, case type and year before committing to the expensive part. Phase 1 example: 246 options × 11 years × 2 statuses ≈ 5,400 searches for district 20, about 4–5 hours.
  2. **Fetch:** pull the deduplicated CNRs from `case_queue` and run V2's `CaseScraper` on each, at about 22 requests or 14–20 s per case.
- **Dedup:** a case is fetched once however many units found it.
- **Resume:** all queues live in the DB, so a crash, kill or block abort continues where it stopped.
- **Truncation check:** if `rows_found ≠ site_total`, the unit is flagged for review. Splitting it further isn't possible, since case type, year and status are already the finest filter.
- **Refresh:** pending cases are re-fetched on a schedule through `cnr_status/viewCNRHistory/` (by CNR, no CAPTCHA). A new version is written only if the content hash changed.
- **CLI:** `lescrape catalog sync …`, `lescrape plan job.yaml` (dry run: unit count, estimated time), `lescrape census job.yaml`, `lescrape fetch job.yaml`, `lescrape refresh`, `lescrape status`.

### 2.3 Portal adapter boundary (needed for Phase 4)
Split today's code into a **portal-neutral core** (rate limiter, BlockGuard, CAPTCHA solver, PDF store, job runner, storage) and a **portal adapter**:

```python
class PortalAdapter(Protocol):
    portal: str                                   # "district" | "high_court"
    def sync_catalog(self, scope) -> Iterable[CatalogNode]: ...
    def search_case_type(self, session, node, case_type, year, status) -> list[Hit]: ...
    def fetch_case(self, session, hit) -> CaseBundle: ...   # normalised dict, same shape for every portal
    def refresh_case(self, session, cnr) -> CaseBundle: ...
```

Phases 1–3 use `DistrictAdapter`, which is today's V2 code moved behind this interface. Phase 4 adds `HighCourtAdapter`. The core, the database and the app don't change.

### 2.4 Throughput
- The portal limits **per IP**: blocks at about 2 rps, safe at about 1.4. Running more jobs in parallel shares that budget rather than adding to it.
- That's about **4,000–6,000 cases per day**. The census pass turns the Phase 2 and Phase 3 volume into a real number before work starts, so jobs can be scoped (years, case types) to fit.
- Growth comes from planning and incremental refresh, never from pushing the portal past its limits.

## 3. Database: designed for all four phases and future versions

**Engine:** SQLAlchemy models plus **Alembic** migrations. Use SQLite for local development and **Postgres** for shared use and the cloud; the same code runs on both.
**Rule:** schema changes are **additive** (new columns or tables, never repurposed ones), so old data and old code keep working.

### 3.1 Principles
1. **One court tree for every system.** District courts and High Courts are different shapes in the same table (§3.2). Phase 4 adds rows, not tables.
2. **Portal codes are stored, never relied on as keys.** Our own surrogate IDs, plus the raw portal value (for example `1030135@3@Y`, `23^3`) in its own column. If the portal renumbers something, history survives.
3. **Common core plus extensions.** Fields every case has go in typed columns. Fields only some portals or states have go in a JSONB `attributes` column, and get promoted to real columns when they're used widely. Unknown page sections go to `extra` (as V2 does now), so nothing is dropped.
4. **Raw is the source of truth.** Every raw response is kept (in the BlobStore, §4), and every parsed row records `parser_version`. A future parser can **re-parse old data without re-scraping.**
5. **History, not overwrite.** `case_versions` records each change. User edits live in an overlay table and never touch scraped values.
6. **Provenance on everything.** Every row can be traced to the job, unit and run that produced it.

### 3.2 Tables

**Catalog (court hierarchy)**

| Table | Key columns | Notes |
|---|---|---|
| `court_systems` | id, code (`district`, `high_court`), name | Room for more later (tribunals, consumer forums) |
| `court_nodes` | id, system_id, parent_id, level, name, portal_code, portal_value_raw, state_code, attributes JSONB, active, first/last_seen_at | **Self-referencing tree.** District: `state → district → complex → establishment`. High Court: `high_court → bench → side/jurisdiction`. `level` is free text, validated per system. |
| `case_types` | id, court_node_id, portal_value (`23^3`), label (`EX`), active, first/last_seen_at | Per establishment (or per bench in Phase 4) |
| `case_type_map` | case_type_id → canonical_type, category (civil/criminal/…) | Filled in gradually. Lets analytics compare "EX" with "Ex.C" with "E.P." across courts and states |
| `states` | code, name, iso_code | Shared reference for both systems |

**Jobs and provenance**

| Table | Notes |
|---|---|
| `jobs` | spec (JSON, as written), portal, created_by, status |
| `work_units` | job_id, court_node_id, case_type_id, year, status_filter, state, attempts, rows_found, site_total, flags |
| `search_hits` | work_unit_id, cnr, raw row fields. Many-to-many: one case can be found by several units |
| `scrape_runs` | id, job_id, started/finished, scraper_version, adapter, summary JSON |
| `case_queue` | cnr, priority, state, attempts, last_error, next_attempt_at |

**Case data** (V2's proven schema, generalised)

| Table | Changes from V2 |
|---|---|
| `cases` | PK `cnr` (unique across India, including High Court CNRs). Adds `system_id`, `court_node_id` (the establishment or bench), `state_code`, `district_node_id` (denormalised for fast filters), `case_type_id`, `filing_year`, `attributes` JSONB, `parser_version`, `content_hash`, `current_version`, `first_seen_at`, `last_scraped_at`. Keeps every existing V2 field. |
| `parties`, `advocates` | Adds `name_norm` for search; the raw name is kept |
| `hearings`, `orders`, `interim_applications`, `ia_business`, `acts`, `processes`, `main_matters`, `ack` | Same as V2, plus `attributes` JSONB, `parser_version` and a version link |
| `case_versions` | cnr, version, content_hash, scraped_at, run_id, changed_fields JSON, blob key of the snapshot |
| `documents` | sha256 (PK), blob key, size, pages, text, needs_ocr. Orders reference it, so a PDF shared by several cases is stored once |
| `raw_responses` | run_id, cnr, endpoint, params, status, ts, **blob key** (no longer a BLOB in the DB), parser_version |
| `errors` | as in V2 |

**Application**

| Table | Notes |
|---|---|
| `annotations` | entity, entity_id, field, value, note, author, ts. Corrections and notes shown next to the scraped value |
| `tags`, `entity_tags` | user labels |
| `saved_views` | name, owner, columns, filters, sort, grouping, shared |
| `users` | from Phase 4 or the cloud move, whichever comes first |

**Scaling for Phases 3–4:** at national scale, partition `cases` and its child tables in Postgres by `state_code`. Indexes go on `(court_node_id, case_type_id, filing_year)`, `case_status`, the date columns, and `name_norm`. Full-text search covers `documents.text`.

**Migration:** a one-off importer loads every existing `V2/output/*/ecourts.db`, assigning court nodes from the stored `search_runs` codes.

## 4. Storage layers (cloud-ready)

| Layer | Now | Later (cloud) |
|---|---|---|
| **Raw** (responses, snapshots, PDFs) | `LocalBlobStore` on disk | S3 / GCS / Azure through the same `BlobStore` interface (`fsspec`), switched by configuration |
| **Normalized** | SQLite file | Managed Postgres |
| **Analytics** | Parquet files + DuckDB | Same Parquet in the bucket, read by BigQuery / Athena / Snowflake |

**Blob key layout** (immutable, partition-friendly):
- Responses: `raw/{system}/{state}/{district}/{yyyy-mm-dd}/{cnr}/{endpoint}-{ts}.json.gz`
- PDFs: `pdfs/{sha256[:2]}/{sha256}.pdf`

**Analytics export:** after each job (or nightly), tables go to Parquet partitioned by `system/state/district/filing_year`.

**Ready-made views:**
- Case duration (filing → decision)
- Adjournments and hearings per case
- Pending cases by age bucket
- Disposals per court and case type
- Judge workload
- Orders needing OCR

## 5. Application (view, customise views, edit, export)

A FastAPI backend and a React frontend with a data grid (AG Grid Community or TanStack Table), on top of the database above.
- **Cases explorer:** server-side paged grid. Filter by system → state → district → complex → court (cascading, from the catalog), case type, year, status, dates, judge, party or advocate. Full-text search over order text.
- **Saved views:** save columns, filters, sort and grouping by name, and share them.
- **Case detail:** parties, hearings timeline, IA, orders with a PDF viewer, acknowledgement, and version history.
- **Edit:** notes, tags and corrections in the `annotations` overlay.
- **Export:** current view or selected rows as CSV, XLSX, JSON or Parquet, streamed for large exports. PDFs optionally as a zip.
- **Jobs:** build a job from the catalog pickers, see the dry-run and census estimate, start or stop it, and watch progress (units, cases, rps, blocks).
- **Coverage map:** per court and case type, what has been censused, fetched and refreshed. This makes phase progress visible.

## 6. Code layout

```
lescrape/                    # new package; V2 stays runnable until parity is reached
  core/        ratelimit.py, captcha.py, pdf.py, blob.py, runner.py   (portal-neutral)
  adapters/
    district/  http.py, search.py, parsers.py, case.py, catalog.py    (moved from V2)
    high_court/                                                       (phase 4)
  jobs/        spec.py, planner.py, census.py, fetch.py, refresh.py
  storage/     models.py, repo.py, migrations/ (alembic), import_v2.py
  analytics/   to_parquet.py, views/*.sql
  api/         FastAPI
  cli.py
web/                         # React app
```

## 7. Roadmap

| Step | Deliverable | Done when |
|---|---|---|
| **0. Foundation** | `lescrape/` package with core/adapter split; V2 code moved behind `DistrictAdapter`; config file replaces CLI defaults | All existing tests pass; one run produces the same output as V2 |
| **1a. Database** | Full schema from §3 (all phases' tables, including `court_systems`), Alembic, BlobStore (local), V2 importer | Past V2 runs imported; a test inserts a dummy High Court node and case without schema changes |
| **1b. Catalog + jobs** | Catalog sync and seed for districts 20 and 21; job spec, planner, census, fetch, resume, refresh | — |
| **Phase 1 run** | Census then fetch for Bengaluru + Bengaluru Rural | Coverage report shows every court × case type × year censused; all found cases fetched; spot-check against the portal |
| **1c. App MVP** | Explorer, case detail, saved views, export, coverage map | Browse, filter, save a view and export Phase 1 data from the UI |
| **Phase 2** | Catalog sync for all of Karnataka; Kannada/format quirks handled in parsers; census, then scoped fetch | Karnataka coverage report; parser warnings triaged |
| **Phase 3** | State-by-state catalog sync and census; per-state parser fixtures; Postgres partitioning; cloud BlobStore | Each state added by configuration plus fixtures only |
| **Phase 4** | High Court site map (like `ECOURTS_SITE_MAP.md`), `HighCourtAdapter` (session, CAPTCHA, case-type search, case page), HC catalog (court → bench) | HC cases appear in the same explorer and exports alongside district cases |

## 8. Risks and decisions to confirm

- **District names (confirmed 2026-11-13):** Phase 1 covers **Bengaluru (20)** and **Bengaluru Rural (21)**.
- **Year range and status** for Phase 1, for example 2015–2025, both Pending and Disposed. This sets the census size.
- **Volume:** Phase 2 and Phase 3 are likely far beyond 5,000 cases per day. The census will give real numbers; expect to prioritise by case type or years.
- **High Court portal:** a different site with its own CAPTCHA, parameters and page layout, none of it verified yet. Phase 4 starts with a read-only mapping exercise.
- **Personal data:** party names and order text. Restrict access to the app, encrypt cloud storage, and keep data out of git.
- **Site changes:** keep V2's dynamic `components.js` parsing and the `extra_*` catch-alls. Add a scheduled fixture smoke test.
- **Cloud provider:** decides S3, GCS or Azure, and the warehouse. Only needed by Phase 3.
