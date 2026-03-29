import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
BASE_DIR = Path(__file__).parent


# ── Password gate ─────────────────────────────────────────────────────────────

def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.title("💸 Spending Dashboard")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        if password == st.secrets["auth"]["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_credentials():
    """Build Credentials from Streamlit secrets, falling back to local token.json."""
    token_file = BASE_DIR / "token.json"

    if "google" in st.secrets and "token" in st.secrets["google"]:
        token_info = json.loads(st.secrets["google"]["token"])
    elif token_file.exists():
        token_info = json.loads(token_file.read_text())
    else:
        st.error("No Google credentials found. Add them to Streamlit secrets or run email_parser.py first.")
        st.stop()

    creds = Credentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=token_info.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


@st.cache_resource
def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials())


def get_sheet_id():
    if "google" in st.secrets and "sheet_id" in st.secrets["google"]:
        return st.secrets["google"]["sheet_id"]
    sheet_id_file = BASE_DIR / "sheet_id.txt"
    if sheet_id_file.exists():
        return sheet_id_file.read_text().strip()
    st.error("Sheet ID not found. Add it to Streamlit secrets or run email_parser.py first.")
    st.stop()


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    sheet_id = get_sheet_id()
    service = get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="Summary!A:F")
        .execute()
    )
    rows = result.get("values", [])
    if len(rows) < 2:
        return pd.DataFrame()

    headers = rows[0]
    data = [r + [""] * (len(headers) - len(r)) for r in rows[1:]]
    df = pd.DataFrame(data, columns=headers)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Amount (IDR)"] = pd.to_numeric(df["Amount (IDR)"], errors="coerce").fillna(0)
    df = df.dropna(subset=["Date"])
    return df


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_idr(value: float) -> str:
    return "Rp {:,.0f}".format(value).replace(",", ".")


# ── App ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Spending Dashboard", page_icon="💸", layout="wide")

if not check_password():
    st.stop()

st.title("💸 Monthly Spending Dashboard")

with st.spinner("Loading data from Google Sheets..."):
    df = load_data()

if df.empty:
    st.warning("No data found. Run `email_parser.py` to populate the sheet.")
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.header("Filters")

available_months = (
    df["Date"].dt.to_period("M").drop_duplicates().sort_values(ascending=False)
)
month_options = [p.strftime("%B %Y") for p in available_months]

from datetime import date
current_month = date.today().strftime("%B %Y")
default_index = month_options.index(current_month) if current_month in month_options else 0

selected_month_str = st.sidebar.selectbox("Month", month_options, index=default_index)
selected_period = pd.Period(selected_month_str, freq="M")

filtered = df[df["Date"].dt.to_period("M") == selected_period].copy()

if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()
    st.rerun()

# ── KPIs ──────────────────────────────────────────────────────────────────────

total = filtered["Amount (IDR)"].sum()
num_tx = len(filtered)
top_source = (
    filtered.groupby("Source")["Amount (IDR)"].sum().idxmax()
    if num_tx > 0 else "-"
)

c1, c2, c3 = st.columns(3)
c1.metric("Total Spending", format_idr(total))
c2.metric("Transactions", num_tx)
c3.metric("Top Source", top_source)

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────

if filtered.empty:
    st.info(f"No transactions found for {selected_month_str}.")
else:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("By Type")
        by_type = (
            filtered.groupby("Purpose/Type")["Amount (IDR)"]
            .sum().reset_index()
            .sort_values("Amount (IDR)", ascending=False)
        )
        fig = px.pie(
            by_type, names="Purpose/Type", values="Amount (IDR)", hole=0.35,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_traces(
            textposition="inside", textinfo="percent+label",
            hovertemplate="<b>%{label}</b><br>Rp %{value:,.0f}<extra></extra>",
        )
        fig.update_layout(showlegend=True, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("By Source")
        by_source = (
            filtered.groupby("Source")["Amount (IDR)"]
            .sum().reset_index()
            .sort_values("Amount (IDR)", ascending=False)
        )
        fig2 = px.pie(
            by_source, names="Source", values="Amount (IDR)", hole=0.35,
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        fig2.update_traces(
            textposition="inside", textinfo="percent+label",
            hovertemplate="<b>%{label}</b><br>Rp %{value:,.0f}<extra></extra>",
        )
        fig2.update_layout(showlegend=True, margin=dict(t=20, b=20))
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # ── Table ─────────────────────────────────────────────────────────────────

    st.subheader("Transactions")
    display = filtered.copy()
    display["Date"] = display["Date"].dt.strftime("%Y-%m-%d")
    display["Amount (IDR)"] = display["Amount (IDR)"].apply(format_idr)
    display = display.sort_values(["Date", "Time"], ascending=[False, False]).reset_index(drop=True)
    st.dataframe(
        display[["Date", "Time", "Source", "Purpose/Type", "Amount (IDR)", "Subject"]],
        use_container_width=True,
        hide_index=True,
    )
