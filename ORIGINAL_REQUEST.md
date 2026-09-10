# Original User Request

## 2026-09-10T18:20:00Z

Consolidate and build a production-ready eCourts India scraper system based on verified network captures, featuring hierarchical court exploration, case search, detailed case record extraction, a resilient persistent queue worker with multi-strategy CAPTCHA handling, and structured export capabilities.

Working directory: F:/Project/Ecourts-Scrapper
Integrity mode: development

## Requirements

### R1. Hierarchical Court Exploration & Deep Case Extraction
Provide automated discovery of court hierarchies (State → District → Court Complex → Establishment → Case Type) and execution of case searches. For search results, extract full case details including CNR number, filing/registration dates, case stage, presiding court/judge, petitioner and respondent rosters with respective advocates, acts and sections, and chronological hearing history.

### R2. Persistent Storage & Resilient Job Queue
Implement a persistent database and task queue to manage scraping workloads. The queue must track task lifecycles (queued, in-progress, completed, failed), support atomic state transitions, handle automatic retries with backoff upon CAPTCHA rejection or network failure, and persist extracted case records with deduplication on unique case identifiers (CNR).

### R3. Multi-Strategy CAPTCHA Resolution & Operational Safeguards
Incorporate an extensible CAPTCHA handling system supporting automated local OCR, manual interactive input, and third-party solver integration. Detect CAPTCHA failures gracefully, automatically refresh tokens and images, and enforce configurable inter-request rate limiting to prevent IP blocking.

### R4. CLI Interface & Data Export Pipeline
Expose the complete system via a clean, intuitive Command Line Interface providing commands for court exploration, job queuing, worker execution, and data export. Provide export pipelines capable of producing clean, structured CSV and JSON outputs containing all case metadata, party details, and hearing logs.

### R5. Controlled Infrastructure & Network Guardrails
External HTTP requests to services.ecourts.gov.in must adhere to configurable pacing (minimum 2.0s default delay) and emulate required request conventions (e.g. jax_req=true, appropriate headers, dynamic session tokens). Automated test verification must run self-contained against local HAR fixtures without requiring live network access.

## Acceptance Criteria

### Exploration & Search Mechanics
- [ ] Exploration commands accurately parse and return structured lists of districts, complexes, establishments, and case types given valid parent identifiers.
- [ ] Compound identifiers (such as complex codes with establishment flags and case type tokens) are correctly parsed and forwarded to downstream endpoints.
- [ ] Search queries properly submit case filters, status, year, and CAPTCHA parameters, successfully capturing result tables and item-level view parameters.

### Case Details Parsing Fidelity
- [ ] Detailed case views are parsed into structured data models capturing CNR number, case classification, filing/registration numbers and dates, case status/stage, and court/judge information.
- [ ] Parties (petitioners, respondents) and their advocates are cleanly parsed into structured lists.
- [ ] Statutory acts and sections are parsed into structured records.
- [ ] Full chronological hearing history (business dates, next hearing dates, purpose of hearing, judge names) is extracted accurately.
- [ ] Optional or missing HTML sections (such as interim applications or unassigned advocates) parse gracefully without crashing.

### Queue, Storage & Error Recovery
- [ ] Worker processes queued tasks with atomic status transitions (queued → unning → completed or ailed).
- [ ] Transient failures (network disconnects, invalid CAPTCHAs) trigger automatic retry cycles up to the configured retry limit before marking a job failed.
- [ ] Extracted cases are persisted in the database with deduplication on CNR number.

### CAPTCHA & Safeguards
- [ ] CAPTCHA resolution module supports local OCR, manual entry fallback, and API solver options.
- [ ] CAPTCHA rejection triggers automatic re-fetching of a fresh CAPTCHA without failing the parent batch.
- [ ] Enforced inter-request delay prevents burst requests.

### CLI & Structured Export
- [ ] CLI supports commands for explore, enqueue, un (worker), and export.
- [ ] Export command generates valid, properly formatted CSV and JSON files matching database records.

### Programmatic Verification
- [ ] A self-contained test suite passes 100% of test cases without requiring active internet connectivity.
- [ ] Parser tests verify extraction accuracy against recorded HAR fixtures (services.ecourts.gov.in 2.har), asserting exact matches for all core fields.
- [ ] Queue and worker mechanics (state transitions, retry logic, persistence) are validated with automated tests.
