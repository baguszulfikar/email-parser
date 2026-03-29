import os
import json
import base64
import re
from datetime import date, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import anthropic

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"
SHEET_NAME = "Email Financial Summary"
SHEET_ID_FILE = Path(__file__).parent / "sheet_id.txt"

SEARCH_QUERY_TEMPLATE = (
    "after:{date} (from:bankmandiri OR from:mandiri OR from:livin "
    "OR from:grab.com OR from:grabrewards OR from:gojek OR from:gopay)"
)

HEADERS = ["Date", "Time", "Source", "Purpose/Type", "Amount (IDR)", "Subject"]
WIB = timezone(timedelta(hours=7))


def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Delete stale token if scopes changed
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def get_services():
    creds = get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return gmail, sheets, drive


def extract_body(payload):
    body = ""
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    elif mime == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        body = re.sub(r"<[^>]+>", " ", html)
        body = re.sub(r"\s+", " ", body).strip()
    elif "parts" in payload:
        for part in payload["parts"]:
            body = extract_body(part)
            if body:
                break
    return body


def get_today_emails(gmail):
    today_str = date.today().strftime("%Y/%m/%d")
    query = SEARCH_QUERY_TEMPLATE.format(date=today_str)
    result = gmail.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])

    emails = []
    for msg in messages:
        msg_data = gmail.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
        body = extract_body(msg_data["payload"])

        # Parse time from Date header, convert to WIB (UTC+7)
        time_str = ""
        try:
            dt = parsedate_to_datetime(headers.get("Date", ""))
            dt_wib = dt.astimezone(WIB)
            time_str = dt_wib.strftime("%H:%M")
        except Exception:
            pass

        emails.append({
            "id": msg["id"],
            "subject": headers.get("Subject", "(no subject)"),
            "sender": headers.get("From", ""),
            "body": body[:3000],
            "time": time_str,
        })
    return emails


def classify_email(client, email):
    prompt = f"""You are classifying an Indonesian financial notification email.

From: {email['sender']}
Subject: {email['subject']}
Body:
{email['body']}

Return a JSON object with exactly these fields:
- "source": one of "Mandiri", "GrabPay", "GoPay", "Grab", "Gojek", or "Unknown"
- "purpose": a short English label for the transaction type, e.g. "Food purchase", "Ride-hailing", "Top-up", "Transfer", "Bill payment", "Subscription", "Withdrawal", "Cashback"
- "amount": transaction amount as a plain integer (e.g. 45000), no currency symbol or separators — use null if not found
- "is_financial": true if this is a financial transaction notification, false otherwise

Return only the JSON, no explanation, no markdown."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"source": "Unknown", "purpose": "Unknown", "amount": None, "is_financial": False}


def parse_amount(value):
    """Convert amount to integer. Handles numeric or string like 'Rp 45.000'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    # Strip currency symbols and convert Indonesian format (dots as thousands separator)
    cleaned = re.sub(r"[^\d,.]", "", str(value))
    # If it looks like Indonesian format (e.g. "45.000"), remove dots
    cleaned = cleaned.replace(".", "").replace(",", "")
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


def get_or_create_sheet(sheets, drive):
    """Return spreadsheet ID, creating the sheet if it doesn't exist."""
    # Check if we have a saved sheet ID
    if SHEET_ID_FILE.exists():
        sheet_id = SHEET_ID_FILE.read_text().strip()
        # Verify it still exists
        try:
            sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
            return sheet_id
        except Exception:
            pass  # Sheet was deleted, create a new one

    # Create new spreadsheet
    spreadsheet = sheets.spreadsheets().create(body={
        "properties": {"title": SHEET_NAME},
        "sheets": [{"properties": {"title": "Summary"}}],
    }).execute()
    sheet_id = spreadsheet["spreadsheetId"]
    grid_id = spreadsheet["sheets"][0]["properties"]["sheetId"]

    # Write header row with formatting
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Summary!A1:F1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()

    # Bold + dark background for header
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": grid_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.18, "green": 0.25, "blue": 0.34},
                            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            # Freeze header row
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": grid_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
        ]}
    ).execute()

    SHEET_ID_FILE.write_text(sheet_id)
    print(f"Created new Google Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}")
    return sheet_id


def remove_today_rows(sheets, sheet_id):
    """Delete rows that already have today's date."""
    today_str = date.today().strftime("%Y-%m-%d")

    # Get actual grid ID
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    grid_id = meta["sheets"][0]["properties"]["sheetId"]

    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="Summary!A:A"
    ).execute()
    values = result.get("values", [])

    rows_to_delete = [
        i for i, row in enumerate(values)
        if i > 0 and row and str(row[0]).startswith(today_str)
    ]

    if not rows_to_delete:
        return 0

    requests = []
    for row_idx in sorted(rows_to_delete, reverse=True):
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": grid_id,
                    "dimension": "ROWS",
                    "startIndex": row_idx,
                    "endIndex": row_idx + 1,
                }
            }
        })
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id, body={"requests": requests}
    ).execute()
    return len(rows_to_delete)


def append_rows(sheets, sheet_id, rows):
    values = [
        [r["date"], r["time"], r["source"], r["purpose"], r["amount"], r["subject"]]
        for r in rows
    ]
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="Summary!A:F",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        key_file = Path(__file__).parent / "api_key.txt"
        if key_file.exists():
            api_key = key_file.read_text().strip()
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set and api_key.txt is empty.")

    print("Connecting to Google services...")
    gmail, sheets, drive = get_services()

    print(f"Fetching today's financial emails ({date.today()})...")
    emails = get_today_emails(gmail)
    print(f"Found {len(emails)} candidate email(s)")

    if not emails:
        print("No matching emails found for today.")
        return

    client = anthropic.Anthropic(api_key=api_key)
    rows = []

    for email in emails:
        print(f"  Classifying: {email['subject'][:60]}...")
        result = classify_email(client, email)

        if not result.get("is_financial", True):
            print("    -> Skipped (not a financial notification)")
            continue

        if "top-up" in (result.get("purpose") or "").lower() or "topup" in (result.get("purpose") or "").lower():
            print("    -> Skipped (top-up)")
            continue

        amount = parse_amount(result.get("amount"))
        rows.append({
            "date": date.today().strftime("%Y-%m-%d"),
            "time": email.get("time", ""),
            "source": result.get("source", "Unknown"),
            "purpose": result.get("purpose", "Unknown"),
            "amount": amount,
            "subject": email["subject"],
        })
        print(f"    -> {result.get('source')} | {result.get('purpose')} | {amount}")

    if not rows:
        print("No financial transactions to record.")
        return

    print("\nUpdating Google Sheet...")
    sheet_id = get_or_create_sheet(sheets, drive)
    removed = remove_today_rows(sheets, sheet_id)
    if removed:
        print(f"  Replaced {removed} existing row(s) for today")
    append_rows(sheets, sheet_id, rows)
    print(f"Done. {len(rows)} row(s) written to: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    main()
