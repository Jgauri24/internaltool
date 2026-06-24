"""Write qualification results to Google Sheets."""

import os

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from src.models import QualificationResult

HEADERS = [
    "URL", "Pricing", "Sign Up", "Free Trial", "Book Demo", "Talk to Sales",
    "Monthly Traffic",
]


def _service():
    path = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]
    creds = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def _row(r: QualificationResult) -> list:
    yn = lambda b: "Yes" if b else "No"
    return [
        r.url, yn(r.pricing_mentioned), yn(r.sign_up_mentioned), yn(r.free_trial_mentioned),
        yn(r.book_demo_button), yn(r.talk_to_sales_button),
        r.monthly_traffic or "",
    ]


def read_urls(sheet_id: str, range_name: str = "Input!A:A") -> list[str]:
    values = _service().spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute().get("values", [])
    skip = {"url", "website", "domain"}
    return [row[0].strip() for row in values if row and row[0].strip().lower() not in skip]


def write_results(sheet_id: str, results: list[QualificationResult], sheet: str = "Qualification") -> None:
    svc = _service()
    sheets = svc.spreadsheets()

    titles = {s["properties"]["title"] for s in sheets.get(spreadsheetId=sheet_id).execute().get("sheets", [])}
    if sheet not in titles:
        sheets.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet}}}]},
        ).execute()

    sheets.values().update(
        spreadsheetId=sheet_id, range=f"{sheet}!A1", valueInputOption="RAW", body={"values": [HEADERS]},
    ).execute()

    rows = [_row(r) for r in results]
    if rows:
        sheets.values().append(
            spreadsheetId=sheet_id, range=f"{sheet}!A:G", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": rows},
        ).execute()
