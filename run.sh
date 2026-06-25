#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Add URLs to the Google Sheet "Input" tab (column A), then run this script.
# Requires GEMINI_API_KEY, GOOGLE_SHEET_ID, and GOOGLE_SHEETS_CREDENTIALS_JSON in .env

python main.py --skip-traffic "$@"
