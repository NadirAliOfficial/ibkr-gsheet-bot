# IBKR Google Sheets Bot

Reads trade parameters from a Google Sheet and automatically places **Trailing Stop + OCA Stop-Limit bracket orders** on Interactive Brokers. Supports multiple IBKR profiles, Telegram alerts, email notifications, and continuous sync.

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)
![IBKR](https://img.shields.io/badge/Interactive%20Brokers-TWS%20API-red?style=flat)
![Google Sheets](https://img.shields.io/badge/Google%20Sheets-API-34A853?style=flat&logo=google-sheets&logoColor=white)

## How It Works

1. You fill in trade parameters in a **Google Sheet** (symbol, qty, trigger price, trailing %, stop %)
2. The bot reads the sheet every `sync_interval_seconds` (default 5 min)
3. For each row matching your `--profile`, it places two OCA-linked orders on IBKR:
   - **Trailing Stop Limit** — follows price up, triggers on reversal
   - **Stop Limit** — hard floor protection
4. Fill status is written back to the sheet and alerts sent via Telegram/Email

## Google Sheet Structure

### Trade Planner (input)

| symbol | qty | trigger price | trailing % | stop % | tif | profile |
|--------|-----|---------------|-----------|--------|-----|---------|
| AAPL   | 100 | 185.00        | 2.5       | 5.0    | GTC | Nadir   |

- **qty**: positive = sell (long exit), negative = buy (short cover)
- **tif**: `GTC`, `DAY`, `GTD`
- **profile**: must match the `--profile` argument when running

### Live Positions (auto-filled)

Timestamp, profile, symbol, qty, status, order ID — updated on every order event.

### Active Orders (auto-filled)

Timestamp, profile, symbol, qty, order type, OCA group ID.

## Setup

### 1. IBKR TWS / IB Gateway

Enable the API in TWS:
- Edit → Global Configuration → API → Settings
- ✅ Enable ActiveX and Socket Clients
- Port: `7497` (paper) or `7496` (live)

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → download JSON key
4. Save the key to `config/google_service_account.json`
5. Share your Google Sheet with the service account email (Editor access)

### 3. Environment Variables

Create a `.env` file in the project root:

```env
GOOGLE_CREDS=config/google_service_account.json
GOOGLE_SHEET_ID=your_sheet_id_here
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Config File

Edit `src/config.ini`:

```ini
[IBKR-YourProfile]
host      = 127.0.0.1
port      = 7497
client_id = 1

[GoogleSheets]
sheet_id             = YOUR_SHEET_ID
trade_planner_range  = Trade Planner!A:H
live_positions_range = Live Positions!A:F
active_orders_range  = Active Orders!A:F

[Settings]
sync_interval_seconds = 300
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `ibapi` must be installed from the IBKR TWS API package — download from [Interactive Brokers](https://interactivebrokers.github.io/) and run `python install.py`.

## Usage

```bash
cd src
python v3_trailing_orders.py --profile YourProfile
```

Run multiple profiles simultaneously:

```bash
python v3_trailing_orders.py --profile Nadir &
python v3_trailing_orders.py --profile Sohail &
```

## Project Structure

```
ibkr-gsheet-bot/
├── src/
│   ├── v3_trailing_orders.py   # Main bot — reads sheet, places orders
│   ├── google_sheets.py        # Google Sheets auth helper
│   └── config.ini              # IBKR profiles and settings
├── config/
│   ├── config.ini              # Config template
│   ├── telegram_creds.ini      # Telegram credentials (gitignored)
│   └── google_service_account.json  # GCP credentials (gitignored)
├── requirements.txt
└── .gitignore
```

## Security

- **Never commit** `google_service_account.json` or `.env` to version control
- Both are already in `.gitignore`
- Use a dedicated GCP service account with minimum permissions (Sheets + Drive read/write only)
- Use IBKR paper trading account for testing

## License

MIT
