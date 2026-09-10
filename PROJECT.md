# Project: eCourts India Production Scraper System

## Architecture
The system is an enterprise-grade, offline-verifiable web scraper and persistent task pipeline for eCourts India (District & Sessions Courts via `services.ecourts.gov.in`).
It is decoupled into clean, modular layers:
1. **Network & Guardrails (`ecourts.core.client`)**: `PacedSession` enforcing >= 2.0s pacing, header simulation (`ajax_req=true`, `app_token`, cookies), exponential retry on socket errors, and session token lifecycle.
2. **CAPTCHA Subsystem (`ecourts.core.captcha`)**: Abstract `BaseCaptchaSolver` strategy with `LocalOcrSolver` (singleton cached model, OpenCV image preprocessing), `ManualInteractiveSolver` (cross-platform CLI prompt), `ThirdPartyApiSolver` (generic pluggable HTTP solver), and `MockCaptchaSolver` (offline deterministic replay). Features in-band refresh recovery without parent batch failure.
3. **Hierarchy & Case Parsers (`ecourts.parser`)**: Pure DOM parsing routines for court cascades (State -> District -> Complex -> Establishment -> Case Type), search result tables (`viewHistory(...)` 9 args + CNR extraction), and deep case records (`case_details`, `parties`, `acts`, `hearings`, `ia_status`).
4. **Storage & Task Queue (`ecourts.storage`, `ecourts.queue`)**: Normalized SQLite (WAL mode) schema for `cases`, `parties`, `acts`, `hearings`, and `tasks`. Atomic 4-state task queue (`queued` -> `running` -> `completed` / `failed`) with lease timeouts, exponential backoff with jitter, and dead-worker watchdog recovery.
5. **CLI & Export Engine (`ecourts.cli`, `ecourts.export`)**: Full CLI suite (`explore`, `enqueue`, `run`, `export`, `status`) and export pipelines for relational/denormalized CSV and nested JSON.
6. **Self-Contained Offline Replay (`ecourts.testing.har_replay`)**: `HarReplayAdapter` mounting to `requests.Session`, mapping recorded HTTP transactions in `services.ecourts.gov.in 2.har` for 100% offline verification.

---

## Feature Inventory
Every requirement from ORIGINAL_REQUEST.md is enumerated and assigned to a milestone.

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Hierarchy Exploration | State -> District -> Court Complex -> Establishment -> Case Type dropdown cascades with `set_data` synchronization | M2 | R1, AC |
| 2 | Compound Token Parsing | Correct handling of `{complex_code}@{est}@{flag}` and `{type}^{est}` tokens | M2 | R1, AC |
| 3 | Case Search Execution | Submitting `case_type_1`, `search_year`, `case_status`, and `ct_captcha_code` | M2 | R1, AC |
| 4 | Search Result Table & CNR Extraction | Parsing `table.table-fixed`, extracting 9 positional arguments from `viewHistory(...)` and 16-char CNR | M2 | R1, AC |
| 5 | Deep Case Record Extraction | High-fidelity parsing of CNR, classification, filing/registration dates, case stage, court/judge | M2 | R1, AC |
| 6 | Party Roster & Advocate Extraction | Parsing `ul.petitioner-advocate-list` and `ul.respondent-advocate-list` into structured records | M2 | R1, AC |
| 7 | Statutory Acts & Sections Extraction | Parsing `table#act_table` into structured acts and sections | M2 | R1, AC |
| 8 | Hearing History Extraction | Parsing `table.history_table` into chronological hearings (business date, next hearing date, purpose, judge) | M2 | R1, AC |
| 9 | Edge Case & Malformed HTML Handling | Resilient parsing of unassigned advocates, missing sections, IA table fallback, duplicate IDs | M2 | R1, AC |
| 10 | Normalized Persistent Storage | Relational SQLite tables (`cases`, `parties`, `acts`, `hearings`, `tasks`) with CNR primary key deduplication | M3 | R2, AC |
| 11 | Resilient Task Queue & Atomic Claim | 4-state lifecycle (`queued`, `running`, `completed`, `failed`) with atomic SQL claims | M3 | R2, AC |
| 12 | Exponential Backoff & Retry Logic | Automatic retries with exponential backoff + jitter upon transient/CAPTCHA failures | M3 | R2, AC |
| 13 | Abandoned Task Watchdog | Automatic reaper reclaiming stale `running` jobs after lease timeout | M3 | R2, AC |
| 14 | Multi-Strategy CAPTCHA Engine | Pluggable solver architecture (`LocalOcrSolver`, `ManualInteractiveSolver`, `ThirdPartyApiSolver`, `MockCaptchaSolver`) | M1 | R3, AC |
| 15 | In-Band CAPTCHA Refresh Recovery | Handling CAPTCHA rejection by solving fresh image from response without failing parent job | M1, M3 | R3, AC |
| 16 | Cross-Platform Operational Safeguards | Safe temp paths on Windows/Linux; cached singleton OCR without dynamic downloads | M1 | R3, AC |
| 17 | Network Pacing & Session Guardrails | `PacedSession` with minimum 2.0s pacing, `ajax_req=true`, `app_token`, header emulation | M1 | R5, AC |
| 18 | CLI Command Suite | CLI supporting `explore`, `enqueue`, `run`, `export`, and `status` | M4 | R4, AC |
| 19 | Structured Data Export Pipeline | Producing valid nested JSON and relational/denormalized CSV matching DB records | M4 | R4, AC |
| 20 | Self-Contained HAR Replay Adapter | Intercepting HTTP calls to simulate live eCourts portal using `services.ecourts.gov.in 2.har` offline | M1, M5 | R5, AC |
| 21 | Full Programmatic E2E Test Suite | 100% offline verification across Tiers 1-4 passing without internet access | M5 | R5, AC |
| 22 | Adversarial Coverage Hardening | Tier 5 white-box stress testing and forensic audit integrity verification | M5 | R5, AC |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Network, Guardrails & CAPTCHA Subsystem | `ecourts/core/client.py`, `ecourts/core/captcha.py`, `ecourts/testing/har_replay.py` | None | DONE |
| M2 | Hierarchy Exploration & Deep Case Parsers | `ecourts/parser/hierarchy.py`, `ecourts/parser/search.py`, `ecourts/parser/case_details.py` | M1 | DONE |
| M3 | Persistent Storage & Resilient Task Queue | `ecourts/storage/db.py`, `ecourts/queue/worker.py` | M1, M2 | DONE |
| M4 | CLI Interface & Structured Export Pipeline | `ecourts/cli.py`, `ecourts/export/exporter.py` | M1, M2, M3 | DONE |
| M5 | E2E Integration, Full Test Suite & Verification | Pass 100% of E2E test suite (Tiers 1-4), Tier 5 adversarial hardening, Forensic Audit | M1, M2, M3, M4 | DONE |

---

## Code Layout

```
F:/Project/Ecourts-Scrapper/
├── ecourts/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── client.py           # PacedSession, HTTP headers, delay guardrails
│   │   └── captcha.py          # BaseCaptchaSolver, LocalOcrSolver, Manual, Mock
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── hierarchy.py        # State, District, Complex, Establishment, CaseType
│   │   ├── search.py           # Search form submission & result table parsing
│   │   └── case_details.py     # High-fidelity case view & CNR parser
│   ├── storage/
│   │   ├── __init__.py
│   │   └── db.py               # SQLite WAL database & normalized schema
│   ├── queue/
│   │   ├── __init__.py
│   │   └── worker.py           # Atomic task queue & worker execution loop
│   ├── export/
│   │   ├── __init__.py
│   │   └── exporter.py         # CSV (relational & flat) and JSON export pipelines
│   ├── testing/
│   │   ├── __init__.py
│   │   └── har_replay.py       # Offline HAR fixture replay adapter
│   └── cli.py                  # CLI commands (explore, enqueue, run, export, status)
├── tests/
│   ├── __init__.py
│   ├── test_client.py          # PacedSession & pacing tests
│   ├── test_captcha.py         # OCR, manual, mock solver tests
│   ├── test_hierarchy.py       # Hierarchy cascade parser tests
│   ├── test_search.py          # Search query & result table parser tests
│   ├── test_case_details.py    # Deep case record parser tests
│   ├── test_storage.py         # Database schema, deduplication & relation tests
│   ├── test_queue.py           # Atomic state transitions & retry backoff tests
│   ├── test_cli.py             # CLI command execution tests
│   ├── test_export.py          # CSV & JSON export validation tests
│   └── test_e2e_offline.py     # End-to-end workflow replay against HAR fixtures
├── fixtures/
│   ├── services.ecourts.gov.in 2.har
│   └── sample_case_detail.html
├── run_tests.py                # Standalone test runner (unittest based)
├── setup.py                    # Packaging setup
└── pyproject.toml              # Project metadata & configuration
```

---

## Interface Contracts

### 1. `PacedSession` ↔ Scraper / Handlers
```python
class PacedSession:
    def __init__(self, min_delay: float = 2.0, base_url: str = "https://services.ecourts.gov.in/ecourtindia_v6/"): ...
    def post(self, endpoint: str, data: dict = None, **kwargs) -> requests.Response: ...
    def get(self, endpoint: str, params: dict = None, **kwargs) -> requests.Response: ...
```

### 2. `BaseCaptchaSolver` ↔ Scraper / Handlers
```python
class BaseCaptchaSolver(abc.ABC):
    @abc.abstractmethod
    def solve(self, image_bytes: bytes) -> str: ...

class CaptchaHandler:
    def __init__(self, session: PacedSession, solver: BaseCaptchaSolver): ...
    def get_and_solve_captcha(self) -> Tuple[str, str]: ... # returns (captcha_code, image_url)
    def solve_from_div(self, div_html: str) -> Tuple[str, str]: ...
```

### 3. Parsers ↔ Data Models
```python
@dataclass
class CaseDetail:
    cnr_number: str
    case_type: str
    filing_number: str
    filing_date: str
    registration_number: str
    registration_date: str
    cnr_number: str
    case_status: str
    stage: str
    court_name: str
    coram: str
    parties: List[Dict[str, str]]   # [{'type': 'petitioner'|'respondent', 'name': str, 'advocate': str}]
    acts: List[Dict[str, str]]      # [{'act': str, 'section': str}]
    hearings: List[Dict[str, str]]  # [{'date': str, 'next_date': str, 'purpose': str, 'judge': str}]
```

### 4. Storage ↔ Worker
```python
class Database:
    def __init__(self, db_path: str): ...
    def save_case(self, case: CaseDetail) -> bool: ... # True if inserted/updated
    def get_case_by_cnr(self, cnr_number: str) -> Optional[Dict[str, Any]]: ...
    def enqueue_task(self, task_type: str, params: dict, max_retries: int = 3) -> int: ...
    def claim_next_task(self, worker_id: str) -> Optional[Dict[str, Any]]: ... # Atomic claim
    def complete_task(self, task_id: int, result_summary: dict = None): ...
    def fail_task(self, task_id: int, error_message: str, retryable: bool = True): ...
```
