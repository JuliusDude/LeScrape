# LeScrape changelog

Milestones are listed newest first. Commit IDs identify the corresponding source snapshots. V1 and V2 are scraper generations. Earlier snapshots are identified by date because the repository has no historical release tags.

## 2026-11-20 · V2 capture workspace and storage updates

### Added

- `8cef1b3` — Added the local capture dashboard with court selectors, live progress, saved-case browsing, and a searchable, sortable run spreadsheet. **Save to DB** publishes complete cases from a finished run to the shared SQLite library.
- `cffcae8` — Added SQL caseload reports and a per-case Excel workbook with separate sheets for parties, hearings, orders and applications.
- Documented the district-court portal and Karnataka court hierarchy and included tools for regenerating the catalogues.

### Fixed and changed

- `cffcae8` — Case snapshots and their child rows commit atomically. Failed refreshes preserve the last complete snapshot. Foreign keys and duplicate-key checks protect related records, and repeat saves avoid duplicate cases.
- `cffcae8` — Case details show recognized dates once in ISO format. Excel export filters unsupported control characters. Transport retries are visible in run diagnostics.
- `8cef1b3` — The capture view uses the DAKSH palette and a flat run monitor. Run statistics appear below configuration and progress.

## 2026-11-13 · V2 scraper and standalone V1

- `11ef76c` — Split the project into V1 and V2, made V1 standalone, introduced the V2 scraper and incorporated fixes from live testing.

## 2026-09-12 · Deep case extraction and batch export

- `4a59cc4` and `a525561` — Updated the project README in two documentation patches.
- `bccd87c` — Extended deep extraction to a 20-case execution batch and added relational Excel, JSON and CSV exports.
- `dba1104` — Introduced the deep case model, parser, database and exporter with an execution-petition example.

## 2026-09-11 · Scraper package and first batch workflows

- `d521d1c` — Added a dynamic portal handshake, a 100-case batch export and a 20-petition dataset.
- `9858130` — Added the eCourts scraper package and offline verification suite.

## 2026-09-03 · Project created

- `be61516` — Created the repository with its initial README.
