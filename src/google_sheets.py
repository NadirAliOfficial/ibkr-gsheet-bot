import gspread
from google.oauth2.service_account import Credentials

# Define the scope
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_google_sheet():
    try:
        # Authenticate with Google Sheets API
        creds = Credentials.from_service_account_file(
            'config/google_service_account.json',
            scopes=SCOPES
        )
        
        # Authorize the client
        client = gspread.authorize(creds)
        
        # Open the sheet by ID (not URL)
        sheet_id = "ZWpA3XMZdjmlKJky84UcHNlwYFKBsshZ4yxV3bohkHE"  # Extract from your URL
        sheet = client.open_by_key(sheet_id).sheet1
        
        return sheet
    
    except Exception as e:
        print(f"Error accessing Google Sheet: {str(e)}")
        raise

# Example usage
if __name__ == "__main__":
    sheet = get_google_sheet()
    data = sheet.get_all_records()
    print("Successfully accessed sheet. First row:", data[0] if data else "No data")