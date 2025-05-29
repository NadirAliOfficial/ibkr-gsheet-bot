import os
import time
import logging
import threading
import random
import argparse
from datetime import datetime

import configparser
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

# ------------------------ LOAD ENV & CONFIG ------------------------
load_dotenv()  # loads .env variables

# Locate config.ini in either current or parent directory
script_dir = os.path.dirname(os.path.abspath(__file__))
search_paths = [script_dir, os.path.abspath(os.path.join(script_dir, '..'))]
config = configparser.ConfigParser()
config_file = None
for path in search_paths:
    candidate = os.path.join(path, 'config.ini')
    if os.path.isfile(candidate):
        config.read(candidate)
        config_file = candidate
        break
if not config_file:
    raise SystemExit(f"config.ini not found in {search_paths}")

# Ensure required sections
if 'GoogleSheets' not in config:
    raise SystemExit(f"Missing [GoogleSheets] section in {config_file}")
if 'Settings' not in config:
    raise SystemExit(f"Missing [Settings] section in {config_file}")

# ------------------------ PARSE ARGS ------------------------
def parse_args():
    parser = argparse.ArgumentParser(description='Trailing orders bot')
    parser.add_argument('--profile', required=True,
                        help='IBKR profile section name (e.g. IBKR)')
    return parser.parse_args()

ARGS = parse_args()
profile = ARGS.profile

# ------------------------ GOOGLE SHEETS ------------------------
SHEET_ID = config['GoogleSheets']['sheet_id']
RANGE_PLANNER = config['GoogleSheets']['trade_planner_range']
RANGE_LIVE = config['GoogleSheets']['live_positions_range']
RANGE_ACTIVE = config['GoogleSheets']['active_orders_range']
SECRETS_RANGE = config['GoogleSheets'].get('secrets_range')

# ------------------------ SETTINGS ------------------------
SYNC_INTERVAL = config['Settings'].getint('sync_interval_seconds', 300)
LOG_DIR = config['Settings'].get('log_dir', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# ------------------------ IBKR PROFILE ------------------------
# Allow direct profile section or prefixed with IBKR-
if profile in config:
    section = profile
else:
    section = f'IBKR' if profile.upper() == 'IBKR' else f'IBKR-{profile}'
if section not in config:
    raise SystemExit(f"Profile section '{section}' not found in {config_file}")
IB_HOST = config[section].get('host', '127.0.0.1')
IB_PORT = config[section].getint('port', 7497)
IB_CLIENT_ID = config[section].getint('client_id', 1)

# ------------------------ EMAIL CONFIG ------------------------
EMAIL_ENABLED = config.getboolean('EMAIL', 'enabled', fallback=False)
if EMAIL_ENABLED:
    EMAIL_HOST = config['EMAIL']['smtp_server']
    EMAIL_PORT = config['EMAIL']['smtp_port']
    EMAIL_USER = config['EMAIL']['smtp_user']
    EMAIL_PASS = config['EMAIL']['smtp_password']
    EMAIL_TO = config['EMAIL']['recipient']
else:
    EMAIL_HOST = EMAIL_PORT = EMAIL_USER = EMAIL_PASS = EMAIL_TO = None

# ------------------------ TELEGRAM CONFIG ------------------------
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# ------------------------ LOGGING SETUP ------------------------
log_file = os.path.join(
    LOG_DIR,
    f"IBKR_{profile}_{datetime.now().strftime('%Y%m%d')}.log"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

# ------------------------ ALERT UTILITIES ------------------------

def send_telegram(text: str):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': text})
        resp.raise_for_status()
        logging.info("Telegram alert sent")
    except Exception as e:
        logging.error(f"Telegram error: {e}")


def send_email(subject: str, body: str):
    if not EMAIL_ENABLED or not EMAIL_HOST:
        return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, int(EMAIL_PORT)) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        logging.info("Email alert sent")
    except Exception as e:
        logging.error(f"Email error: {e}")

# ------------------------ IBKR APP ------------------------
class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
        logging.info(f"NextValidId: {orderId}")

    def error(self, reqId, errorCode, errorString):
        logging.error(f"Error {reqId} {errorCode}: {errorString}")

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                     permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        logging.info(f"OrderStatus[{orderId}]: {status}, filled {filled} @ {avgFillPrice}")
        sheets = get_sheets_service()
        timestamp = datetime.now().isoformat()
        sheets.values().append(
            spreadsheetId=SHEET_ID,
            range=RANGE_LIVE,
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [[timestamp, profile, orderId, status, filled]]}
        ).execute()
        if status == 'Filled':
            msg = f"[{profile}] Order {orderId} filled: {filled} @ {avgFillPrice}"
            send_telegram(msg)
            send_email(f"IBKR Order Filled {orderId}", msg)

# ------------------------ GOOGLE SHEETS SERVICE ------------------------

def get_sheets_service():
    creds = Credentials.from_service_account_file(
        os.getenv('GOOGLE_CREDS', 'credentials.json'),
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    return build('sheets', 'v4', credentials=creds).spreadsheets()

# ------------------------ READ PLANNER ------------------------
def read_planner(sheets):
    resp = sheets.values().get(spreadsheetId=SHEET_ID, range=RANGE_PLANNER).execute()
    return resp.get('values', [])

# ------------------------ VALIDATION ------------------------
def validate_row(row, idx):
    sym = row[idx['symbol']].strip().upper()
    if not sym.isalpha(): raise ValueError(f"Invalid symbol: {sym}")
    qty = int(row[idx['qty']])
    if qty == 0: raise ValueError("Quantity cannot be zero")
    trigger = float(row[idx['trigger price']])
    if trigger <= 0: raise ValueError("Trigger price must be >0")
    trail_pct = float(row[idx['trailing %']])
    stop_pct = float(row[idx['stop %']])
    for p in (trail_pct, stop_pct):
        if p < 0 or p > 100:
            raise ValueError(f"Percentage out of range: {p}")
    tif = row[idx['tif']]
    return sym, qty, trigger, trail_pct, stop_pct, tif

# ------------------------ ORDER BUILDERS ------------------------
def build_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = 'STK'
    c.currency = 'USD'
    c.exchange = 'SMART'
    return c


def build_orders(qty, trigger_price, trail_amt, stop_price, tif, oca_group):
    action = 'SELL' if qty>0 else 'BUY'
    qty_abs = abs(qty)
    trail = Order()
    trail.action = action
    trail.totalQuantity = qty_abs
    trail.orderType = 'TRAIL LIMIT'
    trail.auxPrice = round(trail_amt,2)
    trail.lmtPriceOffset = round(trigger_price - trail_amt - stop_price,2)
    trail.tif = tif
    trail.ocaGroup = oca_group
    trail.ocaType = 1
    stop = Order()
    stop.action = action
    stop.totalQuantity = qty_abs
    stop.orderType = 'STP LMT'
    stop.auxPrice = round(stop_price,2)
    stop.lmtPrice = round(stop_price,2)
    stop.tif = tif
    stop.ocaGroup = oca_group
    stop.ocaType = 1
    return trail, stop

# ------------------------ MAIN LOOP ------------------------
def run_cycle(app, sheets):
    rows = read_planner(sheets)
    if len(rows)<2:
        logging.info("No planner rows")
        return
    headers=[h.strip().lower() for h in rows[0]]
    idx={h:i for i,h in enumerate(headers)}
    required=['symbol','qty','trigger price','trailing %','stop %','tif','profile']
    if any(r not in idx for r in required):
        logging.error("Missing planner columns")
        return
    for row in rows[1:]:
        try:
            row_profile=row[idx['profile']].strip()
            if row_profile != profile: continue
            sym,qty,trigger,trail_pct,stop_pct,tif = validate_row(row,idx)
            avg_price=trigger
            trail_amt=avg_price*trail_pct/100
            stop_price=avg_price*(1-stop_pct/100)
            oca=f"OCA_{profile}_{sym}_{int(time.time())}_{random.randint(0,999)}"
            contract=build_contract(sym)
            trail,stop=build_orders(qty,trigger,trail_amt,stop_price,tif,oca)
            oid=app.nextOrderId
            app.placeOrder(oid,contract,trail)
            logging.info(f"[{profile}] Placed TRAIL {sym} id {oid}")
            app.nextOrderId+=1
            oid2=app.nextOrderId
            app.placeOrder(oid2,contract,stop)
            logging.info(f"[{profile}] Placed STOP {sym} id {oid2}")
            app.nextOrderId+=1
            ts=datetime.now().isoformat()
            sheets.values().append(spreadsheetId=SHEET_ID,range=RANGE_LIVE,valueInputOption='RAW',insertDataOption='INSERT_ROWS',body={'values':[[ts,profile,sym,qty,'PLACED',oid]]}).execute()
            sheets.values().append(spreadsheetId=SHEET_ID,range=RANGE_ACTIVE,valueInputOption='RAW',insertDataOption='INSERT_ROWS',body={'values':[[ts,profile,sym,qty,'OCA',oca]]}).execute()
        except Exception as e:
            logging.error(f"Row error: {e}")


def main():
    # Optional: load secrets from sheet
    if SECRETS_RANGE:
        try:
            ss=get_sheets_service()
            for key,val in ss.values().get(spreadsheetId=SHEET_ID,range=SECRETS_RANGE).execute().get('values',[]):
                os.environ[key]=val
        except Exception as e:
            logging.warning(f"Secrets load failed: {e}")
    app=IBApp()
    try:
        app.connect(IB_HOST,IB_PORT,IB_CLIENT_ID)
    except Exception as e:
        logging.error(f"IB connection failed: {e}")
        return
    thread=threading.Thread(target=app.run,daemon=True)
    thread.start()
    while app.nextOrderId is None:
        time.sleep(0.1)
    logging.info(f"[{profile}] Starting loop")
    sheets=get_sheets_service()
    try:
        while True:
            run_cycle(app,sheets)
            time.sleep(SYNC_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Interrupted")
    finally:
        app.disconnect()
        logging.info("Disconnected")

if __name__=='__main__':
    main()
