import gspread
from google.oauth2.service_account import Credentials

# Configuration
SHEET_ID = "1ZWpA3XMZdjmlKJky84UcHNlwYFKBsshZ4yxV3bohkHE"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_sheet(sheet_name="Sheet1"):
    """Connect to specific worksheet"""
    creds = Credentials.from_service_account_file(
        'config/google_service_account.json',
        scopes=SCOPES
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(sheet_name)

def save_order_to_sheet2(order_data):
    """Save executed orders to Sheet2"""
    try:
        sheet2 = get_sheet("Sheet2")
        
        # Create headers if Sheet2 is empty
        if not sheet2.get_all_records():
            sheet2.append_row([
                "Timestamp", "Symbol", "Action", 
                "Quantity", "Price", "Status"
            ])
        
        sheet2.append_row([
            order_data["timestamp"],
            order_data["symbol"],
            order_data["action"],
            order_data["quantity"],
            order_data["price"],
            order_data["status"]
        ])
        print(f"Order saved to Sheet2: {order_data}")
    except Exception as e:
        print(f"Error saving to Sheet2: {str(e)}")
        raise

# Example usage in your main trading script:
# order_data = {
#     "timestamp": "2023-11-15 14:30:00",
#     "symbol": "AAPL",
#     "action": "BUY",
#     "quantity": 100,
#     "price": 150.25,
#     "status": "FILLED"
# }
# save_order_to_sheet2(order_data)

# import gspread
# from google.oauth2.service_account import Credentials

# # Define the scope
# SCOPES = [
#     "https://www.googleapis.com/auth/spreadsheets",
#     "https://www.googleapis.com/auth/drive"
# ]

# def get_google_sheet():
#     try:
#         # Authenticate with Google Sheets API
#         creds = Credentials.from_service_account_file(
#             'config/google_service_account.json',
#             scopes=SCOPES
#         )
        
#         # Authorize the client
#         client = gspread.authorize(creds)
        
#         # Open the sheet by ID (not URL)
#         sheet_id = "ZWpA3XMZdjmlKJky84UcHNlwYFKBsshZ4yxV3bohkHE"  # Extract from your URL
#         sheet = client.open_by_key(sheet_id).sheet1
        
#         return sheet
    
#     except Exception as e:
#         print(f"Error accessing Google Sheet: {str(e)}")
#         raise

# # Example usage
# if __name__ == "__main__":
#     sheet = get_google_sheet()
#     data = sheet.get_all_records()
#     print("Successfully accessed sheet. First row:", data[0] if data else "No data")