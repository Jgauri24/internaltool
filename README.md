# ICP Website Qualifier

Qualifies B2B SaaS landing pages from a list of URLs. For each site it:

1. Captures a full-page screenshot and extracts page text + button/link labels
2. Detects CTAs (pricing, sign up, free trial, book demo, talk to sales) via LLM using page text
3. Detects live-chat widgets from the screenshot
4. Optionally fetches estimated monthly traffic from DataForSEO
5. Writes results to stdout, JSON, and/or Google Sheets

## Prerequisites

- Python 3.11+
- One vision-capable LLM API key: **Gemini** (preferred) or **Groq**
- Optional: DataForSEO account (traffic), Google service account (Sheets)

## Setup

```bash
# Clone / enter the project
cd internaltool

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (required for screenshots)
playwright install chromium

# Configure environment variables
cp .env.example .env
# Edit .env and fill in your API keys (see below)
```

## Environment variables

Copy `.env.example` to `.env` and set:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | One of Gemini/Groq | Google Gemini API key. Used for CTA + bot detection when set. |
| `GEMINI_MODEL` | No | Default: `gemini-2.5-flash` |
| `GROQ_API_KEY` | One of Gemini/Groq | Groq API key. Used only if `GEMINI_API_KEY` is not set. |
| `GROQ_MODEL` | No | Default: `meta-llama/llama-4-scout-17b-16e-instruct` |
| `DATAFORSEO_LOGIN` | No | DataForSEO login email (for monthly traffic) |
| `DATAFORSEO_PASSWORD` | No | DataForSEO password |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | No | Path to Google service account JSON (for Sheets export) |
| `TOKEN_LOG_PATH` | No | Token usage log file (default: `logs/token_usage.jsonl`) |

### Google Sheets setup (optional)

1. Create a Google Cloud service account and download the JSON key.
2. Save it to `credentials/service-account.json` (this folder is gitignored).
3. Share your Google Sheet with the service account email (Editor access).
4. Your sheet should have:
   - **Input** tab — column A: URLs to qualify (optional, if using `--read-urls-from-sheet`)
   - **Qualification** tab — results are written here automatically

Set in `.env`:

```bash
GOOGLE_SHEETS_CREDENTIALS_JSON=./credentials/service-account.json
```

## Usage

All commands assume the virtual environment is activated:

```bash
source .venv/bin/activate
```

### Qualify URLs directly

```bash
python main.py https://stripe.com https://notion.so
```

### Qualify URLs from a file (one URL per line)

```bash
python main.py -f sample_urls.txt
python main.py -f icp_urls.txt
```

### Save results to JSON

```bash
python main.py -f sample_urls.txt --json-out output/results.json
```

### Skip traffic lookup (faster, no DataForSEO needed)

```bash
python main.py -f sample_urls.txt --skip-traffic
```

### Write results to Google Sheets

```bash
python main.py -f icp_urls.txt --sheet-id YOUR_SHEET_ID
```

Append only new URLs (skips URLs already in the Qualification tab):

```bash
python main.py -f icp_urls.txt --sheet-id YOUR_SHEET_ID
```

Re-run and update existing rows:

```bash
python main.py -f icp_urls.txt --sheet-id YOUR_SHEET_ID --force
```

Clear the Qualification tab first, then write fresh results:

```bash
python main.py -f icp_urls.txt --sheet-id YOUR_SHEET_ID --clear-sheet
```

Read URLs from the sheet's **Input** tab and write results:

```bash
python main.py --sheet-id YOUR_SHEET_ID --read-urls-from-sheet
```

Combine flags as needed:

```bash
python main.py --sheet-id YOUR_SHEET_ID --read-urls-from-sheet --force --skip-traffic --json-out output/results.json
```

## CLI reference

| Flag | Description |
|---|---|
| `urls` | One or more URLs as positional arguments |
| `-f`, `--file` | Text file with one URL per line |
| `-o`, `--output-dir` | Screenshot output directory (default: `output/screenshots`) |
| `--json-out` | Save all results to a JSON file |
| `--sheet-id` | Google Sheet ID to write/read results |
| `--read-urls-from-sheet` | Read URLs from `Input!A:A` tab |
| `--skip-traffic` | Skip DataForSEO traffic lookup |
| `--force` | Re-qualify URLs already in the sheet (upsert rows) |
| `--clear-sheet` | Clear Qualification tab before writing |

## Output fields

Each result is a JSON object:

```json
{
  "url": "https://example.com",
  "pricing_mentioned": true,
  "sign_up_mentioned": true,
  "free_trial_mentioned": false,
  "book_demo_button": true,
  "talk_to_sales_button": false,
  "monthly_traffic": 125000,
  "bot_detected": true
}
```

| Field | How it's detected |
|---|---|
| `pricing_mentioned` | Page text + button/link labels (LLM) |
| `sign_up_mentioned` | Page text + button/link labels (LLM) |
| `free_trial_mentioned` | Page text + button/link labels (LLM) |
| `book_demo_button` | Page text + button/link labels (LLM) |
| `talk_to_sales_button` | Page text + button/link labels (LLM) |
| `monthly_traffic` | DataForSEO estimated monthly visits |
| `bot_detected` | Screenshot vision (live-chat widget) |

## Token usage logs

Every LLM call (CTA analysis + bot detection) appends one JSON line to `logs/token_usage.jsonl`:

```json
{
  "timestamp": "2025-06-25T10:30:00.123456+00:00",
  "url": "https://example.com",
  "call_type": "cta_analysis",
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "input_tokens": 4521,
  "output_tokens": 42,
  "total_tokens": 4563
}
```

| Field | Description |
|---|---|
| `call_type` | `cta_analysis` or `bot_detection` |
| `input_tokens` | Prompt / input tokens |
| `output_tokens` | Completion / output tokens |
| `total_tokens` | Total tokens for that call |

Each URL typically generates 1–2 log lines (CTA always; bot only when vision is used — skipped if chat widget is detected from HTML).

View recent entries:

```bash
tail -f logs/token_usage.jsonl
```

Sum total tokens across a run:

```bash
python3 -c "import json; print(sum(json.loads(l)['total_tokens'] or 0 for l in open('logs/token_usage.jsonl')))"
```

## Project structure

```
internaltool/
├── main.py              # CLI entry point
├── requirements.txt
├── .env.example         # Template for environment variables
├── sample_urls.txt      # Example URL list
├── src/
│   ├── qualifier.py     # Screenshot capture, LLM analysis, traffic
│   ├── sheets.py        # Google Sheets read/write
│   ├── token_log.py     # LLM token usage logging
│   └── models.py        # Result schema
├── logs/
│   └── token_usage.jsonl  # LLM token usage (appended per call)
├── credentials/         # Service account JSON (gitignored)
└── output/
    └── screenshots/     # Captured screenshots (gitignored)
```

## Troubleshooting

**`ModuleNotFoundError`** — Activate the venv and run `pip install -r requirements.txt`.

**Playwright browser missing** — Run `playwright install chromium`.

**All fields false for a URL** — Site may be blocking headless browsers (CAPTCHA). Check the screenshot in `output/screenshots/`.

**Sheets permission error** — Share the sheet with your service account email from the JSON key file.

**No traffic data** — Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`, or use `--skip-traffic`.
