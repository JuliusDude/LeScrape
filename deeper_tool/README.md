# deeper_tool

Production-grade deep extraction model and parser for eCourts India case details, capturing advanced sections often missed by standard scrapers:
- **Processes** (`Process ID`, `Process Title`, `Process Date`)
- **Main Matters** (`Main Case No`, `Main CNR`, `Main Filing No`)
- **Final Orders & Judgments** (`Order Number`, `Order Date`, `Order Details`, `displayPdf` arguments)
- **Case Status & Disposal Metadata** (`Decision Date`, `Nature of Disposal`, `First Hearing Date`, `Court Number and Judge`)
- **Statutory Acts & Sections**
- **Petitioner & Respondent Rosters** with assigned advocates
- **Chronological Hearing History**

---

## Directory Structure

```
deeper_tool/
├── __init__.py           # Package exports
├── models.py             # Rich dataclass models (DeeperCaseDetail, ProcessItem, MainMatterItem, OrderItem, etc.)
├── parser.py             # High-fidelity HTML parser for home/viewHistory
├── db.py                 # SQLite relational database (deeper_ecourts.db) with foreign key cascades
├── exporter.py           # Multi-sheet styled Excel (.xlsx), nested JSON, and summary CSV exporters
├── scraper.py            # End-to-end scraper engine with dynamic anti-scraping session handshake
├── run_single_case.py    # Standalone execution script extracting EX cases under City Civil Court Complex
├── test_deeper.py        # Automated test suite (100% offline against captured HTML fixture)
└── fixtures/
    └── target_case_view.html  # Live verified HTML fixture of EX/2/2023 (KABC010342242022)
```

---

## Extracted Case Reference

- **Jurisdiction**: Karnataka -> BENGALURU
- **Court Complex**: City Civil Court Complex, Bangalore (`1030135`)
- **Establishment**: PRL. CITY CIVIL AND SESSIONS JUDGE (`3`)
- **Case Type**: `EX - Execution Petition Under Order` (`23^3`)
- **Case Number**: `EX/2/2023`
- **Filing Details**: `2786/2022` filed on `17-12-2022`
- **Registration Details**: `2/2023` registered on `02-01-2023`
- **CNR Number**: `KABC010342242022`
- **Case Status**: `Case disposed`
- **Nature of Disposal**: `Uncontested--DISMISSED`
- **Decision Date**: `06th December 2025`
- **Coram**: `1147-CCH64 LXIII ADDL. CITY CIVIL AND SESSIONS JUDGE`
- **Petitioner**: `KRISHNAMURTHY G` (Advocate: `DINESH J S`)
- **Respondent**: `SATHISH M`
- **Statutory Act**: `U/O 21 RULE 11 OF CPC`
- **Process**: `PKABC010342242022_1_1` (*Notice to show cause why execution should not issue [O. 21, R. 16]*)
- **Main Matter**: `O.S./0004555/2020` (CNR: `KABC010166632020`, Filing: `6849`)
- **Chronological Hearings**: 32 distinct hearings
- **Final Orders**: Judgment delivered on `06-12-2025`

---

## Generated Artifacts

- **Multi-sheet Styled Excel**: [`case_EX_2_2023.xlsx`](case_EX_2_2023.xlsx)
  - Sheet 1: `Case Overview`
  - Sheet 2: `Processes`
  - Sheet 3: `Main Matters`
  - Sheet 4: `Parties & Advocates`
  - Sheet 5: `Statutory Acts`
  - Sheet 6: `Hearing History`
  - Sheet 7: `Orders & Judgments`
- **Nested JSON**: [`case_EX_2_2023.json`](case_EX_2_2023.json)
- **Summary Flat CSV**: [`case_EX_2_2023.csv`](case_EX_2_2023.csv)
- **Relational SQLite DB**: [`deeper_ecourts.db`](deeper_ecourts.db)

---

## Running the Extractor & Tests

```bash
# Run extraction
python deeper_tool/run_single_case.py

# Run test suite
python deeper_tool/test_deeper.py
```
