"""Write qualification results to Google Sheets."""

import os
from urllib.parse import urlparse

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from src.models import QualificationResult, TrafficResult

QUALIFICATION_HEADERS = [
    "URL", "Pricing", "Sign Up", "Free Trial", "Book Demo", "Talk to Sales",
    "Bot Detected",
]

TRAFFIC_HEADERS = ["URL", "Monthly Traffic"]

INPUT_SHEET = "Input"
INPUT2_SHEET = "Input2"
QUALIFICATION_SHEET = "Qualification"
TRAFFIC_SHEET = "Traffic"


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


def _qualification_row(r: QualificationResult) -> list:
    yn = lambda b: "Yes" if b else "No"
    return [
        r.url, yn(r.pricing_mentioned), yn(r.sign_up_mentioned), yn(r.free_trial_mentioned),
        yn(r.book_demo_button), yn(r.talk_to_sales_button), yn(r.bot_detected),
    ]


def _traffic_row(r: TrafficResult) -> list:
    return [r.url, r.monthly_traffic if r.monthly_traffic is not None else ""]


def _sheet_titles(sheet_id: str) -> set[str]:
    svc = _service()
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def _ensure_sheet(sheet_id: str, title: str, header: list[str] | None = None) -> None:
    svc = _service()
    sheets = svc.spreadsheets()
    if title in _sheet_titles(sheet_id):
        return
    sheets.batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    if header:
        sheets.values().update(
            spreadsheetId=sheet_id,
            range=f"{title}!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()


def read_urls(sheet_id: str, sheet: str = INPUT_SHEET) -> list[str]:
    _ensure_sheet(sheet_id, sheet, header=["URL"])
    values = (
        _service().spreadsheets().values()
        .get(spreadsheetId=sheet_id, range=f"{sheet}!A:A")
        .execute()
        .get("values", [])
    )
    skip = {"url", "website", "domain"}
    return [row[0].strip() for row in values if row and row[0].strip().lower() not in skip]


def existing_url_keys(sheet_id: str, sheet: str = QUALIFICATION_SHEET) -> set[str]:
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


def clear_results(sheet_id: str, sheet: str = QUALIFICATION_SHEET) -> None:
    """Clear all rows, keep headers only."""
    svc = _service()
    sheets = svc.spreadsheets()
    if sheet not in _sheet_titles(sheet_id):
        _ensure_sheet(sheet_id, sheet)
    cols = "G" if sheet == QUALIFICATION_SHEET else "B"
    headers = QUALIFICATION_HEADERS if sheet == QUALIFICATION_SHEET else TRAFFIC_HEADERS
    sheets.values().clear(spreadsheetId=sheet_id, range=f"{sheet}!A:{cols}").execute()
    sheets.values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet}!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def clear_traffic_results(sheet_id: str) -> None:
    clear_results(sheet_id, TRAFFIC_SHEET)


def _append_results(
    sheet_id: str,
    results: list,
    sheet: str,
    headers: list[str],
    row_fn,
    col_range: str,
) -> int:
    svc = _service()
    sheets = svc.spreadsheets()
    _ensure_sheet(sheet_id, sheet, header=headers)

    already = existing_url_keys(sheet_id, sheet)
    new_results = [r for r in results if url_key(r.url) not in already]
    if not new_results:
        return 0

    rows = [row_fn(r) for r in new_results]
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
            body={"values": [headers]},
        ).execute()

    sheets.values().append(
        spreadsheetId=sheet_id,
        range=f"{sheet}!{col_range}",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return len(rows)


def _upsert_results(
    sheet_id: str,
    results: list,
    sheet: str,
    headers: list[str],
    row_fn,
    col_range: str,
) -> int:
    svc = _service()
    sheets = svc.spreadsheets()
    _ensure_sheet(sheet_id, sheet, header=headers)

    values = (
        sheets.values().get(spreadsheetId=sheet_id, range=f"{sheet}!{col_range}")
        .execute()
        .get("values", [])
    )
    rows = values[1:] if values and values[0] else []
    updated = 0

    for result in results:
        key = url_key(result.url)
        row_data = row_fn(result)
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

    sheets.values().clear(spreadsheetId=sheet_id, range=f"{sheet}!{col_range}").execute()
    sheets.values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet}!A1",
        valueInputOption="RAW",
        body={"values": [headers] + rows},
    ).execute()
    return updated


def write_results(sheet_id: str, results: list[QualificationResult], sheet: str = QUALIFICATION_SHEET) -> int:
    """Append new qualification results only."""
    return _append_results(
        sheet_id, results, sheet, QUALIFICATION_HEADERS, _qualification_row, "A:G",
    )


def upsert_results(sheet_id: str, results: list[QualificationResult], sheet: str = QUALIFICATION_SHEET) -> int:
    """Update existing qualification rows or append new ones."""
    return _upsert_results(
        sheet_id, results, sheet, QUALIFICATION_HEADERS, _qualification_row, "A:G",
    )


def write_traffic_results(sheet_id: str, results: list[TrafficResult]) -> int:
    """Append new traffic results only."""
    return _append_results(
        sheet_id, results, TRAFFIC_SHEET, TRAFFIC_HEADERS, _traffic_row, "A:B",
    )


def upsert_traffic_results(sheet_id: str, results: list[TrafficResult]) -> int:
    """Update existing traffic rows or append new ones."""
    return _upsert_results(
        sheet_id, results, TRAFFIC_SHEET, TRAFFIC_HEADERS, _traffic_row, "A:B",
    )
