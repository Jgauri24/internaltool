"""Write qualification results to Google Sheets."""

import os
from urllib.parse import urlparse

from google.oauth2.service_account import Credentials  # pyright: ignore[reportMissingImports]
from googleapiclient.discovery import build  # pyright: ignore[reportMissingImports]

from src.models import QualificationResult

HEADERS = [
    "URL", "Pricing", "Sign Up", "Free Trial", "Book Demo", "Talk to Sales",
    "Monthly Traffic", "Bot Detected",
]


def url_key(url: str) -> str:
    """Canonical key for deduping URLs (www.stripe.com == stripe.com)."""
    u = url.strip().lower()
    if not u.startswith("http"):
        u = f"https://{u}"
    p = urlparse(u)
    host = p.netloc.removeprefix("www.")
    path = p.path.rstrip("/")
    return f"{host}{path}"


def _service():
    path = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]
    creds = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def _row(r: QualificationResult) -> list:
    yn = lambda b: "Yes" if b else "No"
    return [
        r.url, yn(r.pricing_mentioned), yn(r.sign_up_mentioned), yn(r.free_trial_mentioned),
        yn(r.book_demo_button), yn(r.talk_to_sales_button),
        r.monthly_traffic or "", yn(r.bot_detected),
    ]


def read_urls(sheet_id: str, range_name: str = "Input!A:A") -> list[str]:
    values = _service().spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute().get("values", [])
    skip = {"url", "website", "domain"}
    return [row[0].strip() for row in values if row and row[0].strip().lower() not in skip]


def existing_url_keys(sheet_id: str, sheet: str = "Qualification") -> set[str]:
    """URLs already in the Qualification tab."""
    try:
        values = (
            _service().spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"{sheet}!A:A")
            .execute()
            .get("values", [])
        )
    except Exception:
        return set()

    skip = {"url", "website", "domain"}
    keys = set()
    for row in values:
        if row and row[0].strip().lower() not in skip:
            keys.add(url_key(row[0]))
    return keys


def clear_results(sheet_id: str, sheet: str = "Qualification") -> None:
    """Clear all rows, keep headers only."""
    svc = _service()
    sheets = svc.spreadsheets()
    titles = {s["properties"]["title"] for s in sheets.get(spreadsheetId=sheet_id).execute().get("sheets", [])}
    if sheet not in titles:
        sheets.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet}}}]},
        ).execute()
    sheets.values().clear(spreadsheetId=sheet_id, range=f"{sheet}!A:H").execute()
    sheets.values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()


def write_results(sheet_id: str, results: list[QualificationResult], sheet: str = "Qualification") -> int:
    """Append new results only — never remove existing rows."""
    svc = _service()
    sheets = svc.spreadsheets()

    titles = {s["properties"]["title"] for s in sheets.get(spreadsheetId=sheet_id).execute().get("sheets", [])}
    if sheet not in titles:
        sheets.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet}}}]},
        ).execute()

    already = existing_url_keys(sheet_id, sheet)
    new_results = [r for r in results if url_key(r.url) not in already]
    if not new_results:
        return 0

    rows = [_row(r) for r in new_results]

    current = (
        sheets.values().get(spreadsheetId=sheet_id, range=f"{sheet}!A1:A1")
        .execute()
        .get("values", [])
    )
    if not current:
        sheets.values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()

    sheets.values().append(
        spreadsheetId=sheet_id,
        range=f"{sheet}!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return len(rows)


def upsert_results(sheet_id: str, results: list[QualificationResult], sheet: str = "Qualification") -> int:
    """Update existing rows or append new ones."""
    svc = _service()
    sheets = svc.spreadsheets()

    titles = {s["properties"]["title"] for s in sheets.get(spreadsheetId=sheet_id).execute().get("sheets", [])}
    if sheet not in titles:
        sheets.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet}}}]},
        ).execute()

    values = (
        sheets.values().get(spreadsheetId=sheet_id, range=f"{sheet}!A:H")
        .execute()
        .get("values", [])
    )
    rows = values[1:] if values and values[0] else []
    updated = 0

    for result in results:
        key = url_key(result.url)
        row_data = _row(result)
        found = False
        for i, row in enumerate(rows):
            if row and url_key(row[0]) == key:
                rows[i] = row_data
                found = True
                updated += 1
                break
        if not found:
            rows.append(row_data)
            updated += 1

    sheets.values().clear(spreadsheetId=sheet_id, range=f"{sheet}!A:H").execute()
    sheets.values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS] + rows},
    ).execute()
    return updated
