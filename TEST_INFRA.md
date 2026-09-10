# E2E Test Infra: eCourts India Production Scraper

## Test Philosophy
- Opaque-box, requirement-driven derived strictly from ORIGINAL_REQUEST.md.
- 100% self-contained and offline-verifiable: operates with network sockets blocked, replaying verified network captures (`services.ecourts.gov.in 2.har`) and recorded database fixtures.
- Methodology: 4-Tier systematic verification (Category-Partition, Boundary Value Analysis, Pairwise Combinatorial, Real-World Workload Testing) + Tier 5 Adversarial Coverage Hardening.

## Feature Inventory & Test Mapping
| # | Feature | Requirement | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Scenario) |
|---|---------|-------------|:----------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | Hierarchy Dropdown Cascade | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 2 | Compound Token Parsing | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 3 | Case Search Execution | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 4 | Search Result & CNR Extraction | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 5 | Deep Case Record Parsing | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 6 | Party Roster & Advocate Parsing | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 7 | Acts & Sections Parsing | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 8 | Hearing History Extraction | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 9 | Edge Case / Malformed HTML Handling | R1, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 10 | Normalized Storage & CNR Deduplication | R2, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 11 | Resilient Queue & Atomic Claim | R2, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 12 | Retry with Backoff & Watchdog | R2, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 13 | Multi-Strategy CAPTCHA Engine | R3, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 14 | In-Band CAPTCHA Refresh Recovery | R3, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 15 | Network Pacing Guardrails (>=2.0s) | R5, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 16 | CLI Commands (explore, enqueue, run, export) | R4, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 17 | Structured Data Export (CSV & JSON) | R4, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |
| 18 | Offline HAR Replay Adapter | R5, AC | >= 5 tests | >= 5 tests | ✓ | ✓ |

## Test Architecture
- **Test Runner**: Standard library `python -m unittest discover -s tests -p "test_*.py"` via `run_tests.py` with zero third-party testing dependencies required.
- **Fixture Replay Adapter**: `ecourts.testing.har_replay.HarReplayAdapter` intercepts `requests.Session` calls to `services.ecourts.gov.in` and serves exact status, headers, and binary bodies from `fixtures/services.ecourts.gov.in 2.har`.
- **Socket Isolation**: Global test guard optionally monkeypatches `socket.socket.connect` to raise `RuntimeError("Network access strictly forbidden in offline test mode")` ensuring no live requests occur.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Expected Outcome |
|---|----------|--------------------|------------------|
| 1 | Full Court Hierarchy Discovery | F1, F2, F15, F18 | Complete exploration from State to Case Types; formatted CLI output |
| 2 | Offline Case Search & Enqueue | F3, F4, F10, F11, F18 | Search executed against HAR, result cases enqueued into persistent database |
| 3 | Worker Batch Processing with CAPTCHA Refresh | F5-F9, F11-F14, F18 | Worker claims jobs, handles simulated CAPTCHA refresh, parses detail views |
| 4 | Resilient Queue Crash Recovery & Retries | F10, F11, F12, F13 | Worker killed mid-task; watchdog recovers task; retry backoff triggers until completion |
| 5 | End-to-End Deep Case Export | F5-F10, F16, F17 | Extracted cases exported to relational CSV, flat CSV, and JSON matching DB records |

## Coverage Thresholds
- Tier 1: Feature Coverage (>= 5 tests per feature) -> >= 90 tests
- Tier 2: Boundary & Corner Cases (>= 5 tests per feature) -> >= 90 tests
- Tier 3: Cross-Feature Interactions -> >= 18 pairwise interaction tests
- Tier 4: Realistic Application Scenarios -> >= 5 comprehensive scenarios
- Tier 5: Adversarial Coverage Hardening -> Challenger stress tests and white-box gap closure
