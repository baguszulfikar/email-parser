# Email Financial Parser

Automatically fetches financial notification emails from Gmail (Mandiri, Grab, Gojek), classifies them using Claude AI, and logs the results to a Google Sheet. A Streamlit dashboard visualizes your monthly spending.

## Features

- Fetches today's financial emails from Mandiri Livin, Grab, and Gojek
- Uses Claude AI to classify source, transaction type, and amount
- Writes results to Google Sheets (date, time, source, type, amount)
- Streamlit dashboard with pie charts, KPIs, and transaction table
- Runs automatically every day at 08:00 WIB via GitHub Actions

## Project Structure

```
email-parser/
├── email_parser.py          # Fetches, classifies, and writes emails to Google Sheets
├── dashboard.py             # Streamlit spending dashboard
├── requirements.txt         # Python dependencies
├── credentials.json         # OAuth client credentials (not committed)
├── token.json               # OAuth access token (not committed)
├── sheet_id.txt             # Google Sheet ID (not committed)
├── api_key.txt              # Anthropic API key (not committed)
└── .github/
    └── workflows/
        └── daily_parser.yml # GitHub Actions schedule
```

## Setup

### 1. Prerequisites

- Python 3.12+
- A Google Cloud project with **Gmail API**, **Google Sheets API**, and **Google Drive API** enabled
- An Anthropic API key

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Google OAuth

Download your OAuth client credentials from Google Cloud Console as `credentials.json` and place it in the project root.

### 4. Configure API key

Paste your Anthropic API key into `api_key.txt`.

### 5. First run

```bash
python email_parser.py
```

A browser will open for Gmail OAuth. After approving, `token.json` and `sheet_id.txt` are created automatically.

## Running the Dashboard

```bash
streamlit run dashboard.py
```

Opens at `http://localhost:8501`. Login with the password configured in Streamlit secrets.

## GitHub Actions (Automated Daily Run)

The parser runs every day at **08:00 WIB** via `.github/workflows/daily_parser.yml`.

Add these secrets to your GitHub repo under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GOOGLE_SHEET_ID` | Contents of `sheet_id.txt` |
| `GOOGLE_TOKEN` | Output of `python -c "import json; print(json.dumps(json.load(open('token.json'))))"` |

You can also trigger a manual run from **Actions → Daily Email Parser → Run workflow**.

## Streamlit Cloud Deployment

Add these secrets in **Streamlit Cloud → App Settings → Secrets**:

```toml
[auth]
password = "your_dashboard_password"

[google]
sheet_id = "your_google_sheet_id"
token = 'your_token_json_as_single_line_string'
```
