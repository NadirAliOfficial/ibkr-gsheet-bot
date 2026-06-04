import os
import configparser
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_google_sheet(sheet_id: str = None, credentials_path: str = None):
    if sheet_id is None:
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if sheet_id is None:
        raise ValueError("Sheet ID not provided. Set GOOGLE_SHEET_ID env var or pass sheet_id argument.")

    if credentials_path is None:
        credentials_path = os.getenv("GOOGLE_CREDS", "config/google_service_account.json")

    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


if __name__ == "__main__":
    sheet = get_google_sheet()
    data = sheet.get_all_records()
    print("Successfully accessed sheet. Rows:", len(data))
    if data:
        print("First row:", data[0])
