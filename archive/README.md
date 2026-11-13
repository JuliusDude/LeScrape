# archive

Earlier models and scripts that are no longer used. They are kept for reference only and are not maintained or tested.

| Item | What it is |
|---|---|
| `model1/`, `Model2/`, `Model3/`, `Model_BackUP/` | Early standalone scraper prototypes (main/scraper/parsers/storage/captcha_solver). `Model_BackUP/index.html` is an unrelated page. |
| `run_ep_flow.py`, `scrape_100.py` | One-off run scripts built on the root `ecourts` package. |
| `cases_100_export.*`, `execution_petitions_20.*` | Outputs of those scripts. |
| `ecourts.db` | Local database from an earlier run (git-ignored). |

These scripts import the root `ecourts` package. To run one, start it from the repo root with that root on the import path, for example `PYTHONPATH=. python archive/scrape_100.py`. Without `PYTHONPATH`, `import ecourts` fails, because Python only adds `archive/` to the path.

The maintained tools are `V1/` (deep case-history extractor) and `V2/` (full autonomous scraper).
