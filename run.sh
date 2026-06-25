#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Add URLs to Google Sheet tabs, then run:
#   Input   → Gemini qualification → Qualification tab
#   Input2  → DataForSEO traffic only → Traffic tab
# Requires GEMINI_API_KEY, GOOGLE_SHEET_ID, GOOGLE_SHEETS_CREDENTIALS_JSON in .env

python main.py "$@"
