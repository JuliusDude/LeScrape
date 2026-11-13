# LeScrape

LeScrape scrapes eCourts India district-court case data (services.ecourts.gov.in).

| Folder | What it is | Start here |
|---|---|---|
| [`V2/`](V2/README.md) | **Current scraper.** Standalone and autonomous (CAPTCHA OCR, retries, firewall back-off). For each case it captures details, parties, hearings with their daily business text, orders with the PDF file and its text, IA, and the acknowledgement page. Live-tested and independently verified. | `python -m V2 run ...`, `python -m V2.view <CNR> --out <run>` |
| [`V1/`](V1/README.md) | First-generation deep extractor (formerly `deeper_tool`). It parses the case-history page into a relational DB. It is self-contained and does not use `ecourts`. | `python V1/batch_extract_20.py` |
| `ecourts/` | Original base library (CAPTCHA solver, parsers, storage, queue, CLI), plus its tests in `tests/` and HAR fixtures in `fixtures/`. Neither V1 nor V2 depends on it; the archived scripts do. Described in `PROJECT.md`, `TEST_INFRA.md` and `TEST_READY.md`. | — |
| [`archive/`](archive/README.md) | Unused earlier prototypes (model1, Model2, Model3, Model_BackUP) and one-off scripts and outputs. | — |

Other files:
- `context.md`: full working history and live-test findings.
- `RULES_.md`: agent delegation rules.
- `ORIGINAL_REQUEST.md`: the original brief.

## Tests

```bash
python run_tests.py -q        # ecourts + V1 + V2, all offline (445 tests)
```

## Notes

- Live runs hit a public government portal. Keep V2 at `--workers 2 --max-rps 1.5` or lower: the portal blocks the IP above about 2 requests per second.
- `V2/output/` holds real court PDFs and party names. It is git-ignored; do not commit it.
