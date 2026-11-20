# ecourts_map

Supporting material for [`../ECOURTS_SITE_MAP.md`](../ECOURTS_SITE_MAP.md). Captured 2026-11-13 from the live portal, read-only: no searches and no CAPTCHA.

| File | Purpose |
|---|---|
| `crawl_pages.py` | GETs the menu pages and their JS into `raw/` (about 1 request/s). |
| `analyse_pages.py` | Extracts tabs, form fields, options and buttons per service. Writes `raw/pages_analysis.{json,txt}`. |
| `analyse_js.py` | Maps each JS function to its endpoint and POST parameters. Writes `raw/js_endpoints.{json,txt}`. |
| `enumerate_example.py` | Calls the dropdown-filling endpoints for Karnataka → Bengaluru (38 requests) through the V2 session. Writes `raw/enum/*.json` and `raw/enum/_summary.json`. |

Re-run from the repo root:
1. `python docs/ecourts_map/crawl_pages.py`
2. `cd docs/ecourts_map && python analyse_pages.py && python analyse_js.py`
3. `PYTHONPATH=. python docs/ecourts_map/enumerate_example.py`

The site's JS and the delimiter/header values change between versions. Re-crawl before trusting old captures.
