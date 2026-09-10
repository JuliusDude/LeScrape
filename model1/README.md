274

# eCourts Case Status Scraper

A production-ready scraper for https://services.ecourts.gov.in

## File Structure

```
ecourts_scraper/
├── main.py           ← CLI entrypoint (start here)
├── scraper.py        ← Core scraper + session manager
├── captcha_solver.py ← OCR and 2captcha solver
├── parsers.py        ← HTML response parsers
├── storage.py        ← SQLite + job queue
├── worker.py         ← Queue worker (runs jobs)
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

## Quick Start

### Step 1: Explore the hierarchy (no CAPTCHA needed)

```bash
# Find district codes for Karnataka (state 13)
python main.py explore --state 13

# Drill into a district
python main.py explore --state 13 --district 7

# Drill into a complex
python main.py explore --state 13 --district 7 --complex 5

# See case types for a court
python main.py explore --state 13 --district 7 --complex 5 --establishment 12
```

### Step 2: Queue your searches

```bash
# By CNR number (most reliable)
python main.py enqueue-cnr KARB010098762023

# By case type
python main.py enqueue-case-type \
  --state 13 --district 7 --complex 5 --establishment 12 \
  --case-type CRL --case-number 123 --year 2024

# By party name
python main.py enqueue-party \
  --state 13 --district 7 --complex 5 --establishment 12 \
  --name "Sharma"
```

### Step 3: Run the worker

```bash
# Manual CAPTCHA (for testing — you type the CAPTCHA)
python main.py run --captcha manual

# OCR (free, ~75% accuracy — will retry on wrong CAPTCHA)
python main.py run --captcha ocr

# 2captcha (paid, ~99% accuracy — for production)
python main.py run --captcha 2captcha --api-key YOUR_2CAPTCHA_KEY
```

### Step 4: Export results

```bash
python main.py export --format csv --output results.csv
python main.py export --format json --output results.json
```

## CAPTCHA Strategy

| Method | Cost | Accuracy | Speed | Best For |
|--------|------|----------|-------|----------|
| `manual` | Free | 100% | Slow | Testing |
| `ocr` | Free | ~75% | Fast | Dev/small runs |
| `2captcha` | ~$1-3/1000 | ~99% | 5-15s | Production |

Get a 2captcha key at: https://2captcha.com

## Request Flow (what the scraper does)

```
1. GET  homepage              → get SERVICES_SESSID + JSESSION cookies
2. POST getCaptcha            → get CAPTCHA HTML (contains image URL)
3. GET  securimage_show.php   → download CAPTCHA PNG
4. [SOLVE CAPTCHA]
5. POST fillDistrict          → load districts for state
6. POST fillcomplex           → load complexes for district
7. POST fillCourtEstablishment → load courts for complex
8. POST fillCaseType          → load case types for court
9. POST set_data              → persist selections server-side (CRITICAL)
10. POST getCaptcha + solve   → refresh CAPTCHA before submit
11. POST submit_case_type     → actual search → parse results
```

## Known Limitations

- **CAPTCHA on every search** — no way around it
- **No bulk search API** — each search = 1 session cycle
- **Session timeout** — ~30 min idle; scraper auto-reinits
- **Rate limits** — default 3s delay between requests; increase if getting blocked
- **Site version** — currently v6; update BASE_URL if they launch v7

## Legal Note

eCourts data is public, but automated access to government infrastructure
sits in a legal grey zone in India. Keep request rates conservative and
consider applying for official API access at https://api.ecourts.gov.in
