
import os
import time
import logging
import threading
import random
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
load_dotenv()  # loads .env

config = configparser.ConfigParser()
config.read('config.ini')

# Google Sheets
SHEET_ID = config['GoogleSheets']['sheet_id']
RANGE_PLANNER = config['GoogleSheets']['trade_planner_range']
RANGE_LIVE = config['GoogleSheets']['live_positions_range']
RANGE_ACTIVE = config['GoogleSheets']['active_orders_range']

# IBKR Connection
IB_HOST = config['IBKR'].get('host', '127.0.0.1')
IB_PORT = config['IBKR'].getint('port', 7497)
IB_CLIENT_ID = config['IBKR'].getint('client_id', 1)

# Alerts
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = os.getenv('EMAIL_PORT')
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASS = os.getenv('EMAIL_PASS')
EMAIL_TO = os.getenv('EMAIL_TO')

# Settings
SYNC_INTERVAL = config['Settings'].getint('sync_interval_seconds', 300)
LOG_FILE = config['Settings'].get('log_file', 'trailing_orders.log')

# ------------------------ LOGGING SETUP ------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# ------------------------ ALERT UTILITIES ------------------------

def send_telegram(text: str):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': text})
        resp.raise_for_status()
        logging.info("Telegram alert sent")
    except Exception as e:
        logging.error(f"Telegram error: {e}")


def send_email(subject: str, body: str):
    if not EMAIL_HOST: return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    try:
        with smtplib.SMTP(EMAIL_HOST, int(EMAIL_PORT)) as server:
            server.starttls()
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
        if status == 'Filled':
            msg = f"Order {orderId} filled: {filled} @ {avgFillPrice}"
            send_telegram(msg)
            send_email(f"IBKR Order Filled {orderId}", msg)

# ------------------------ GOOGLE SHEETS ------------------------

def get_sheets_service():
    creds = Credentials.from_service_account_file(
        os.getenv('GOOGLE_CREDS', 'credentials.json'),
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    return build('sheets', 'v4', credentials=creds).spreadsheets()

def read_planner(sheets):
    resp = sheets.values().get(spreadsheetId=SHEET_ID, range=RANGE_PLANNER).execute()
    return resp.get('values', [])

def log_to_sheet(sheets, data, range_name):
    sheets.values().append(
        spreadsheetId=SHEET_ID,
        range=range_name,
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': data}
    ).execute()

# ------------------------ ORDER BUILDERS ------------------------
def build_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = 'STK'
    c.currency = 'USD'
    c.exchange = 'SMART'
    return c


def build_orders(symbol, qty, trigger_price, trail_amt, stop_price, tif, oca_group):
    # trailing limit order
    trail = Order()
    trail.action         = 'SELL' if qty > 0 else 'BUY'
    trail.totalQuantity  = abs(qty)
    trail.orderType      = 'TRAIL LIMIT'
    trail.auxPrice       = round(trail_amt, 2)                         # trailing amount
    trail.lmtPriceOffset = round(trigger_price - trail_amt - stop_price, 2)
    trail.tif            = tif
    trail.ocaGroup       = oca_group
    trail.ocaType        = 1
    # disable unsupported flags on trail leg
    trail.eTradeOnly     = False
    trail.firmQuoteOnly  = False
    trail.allOrNone      = False

    # fallback stop-limit order (STP LMT)
    stop = Order()
    stop.action         = trail.action
    stop.totalQuantity  = abs(qty)
    stop.orderType      = 'STP LMT'                                   # stop-limit
    stop.auxPrice       = round(stop_price, 2)                         # trigger price
    stop.lmtPrice       = round(stop_price, 2)                         # limit price
    stop.tif            = tif
    stop.ocaGroup       = oca_group
    stop.ocaType        = 1
    # disable unsupported flags on stop leg
    stop.eTradeOnly     = False
    stop.firmQuoteOnly  = False
    stop.allOrNone      = False

    return trail, stop


# ------------------------ MAIN LOOP ------------------------
def run_cycle(app, sheets):
    rows = read_planner(sheets)
    if len(rows) < 2:
        logging.info('No planner data.')
        return
    headers = [h.strip().lower() for h in rows[0]]
    idx = {h: i for i, h in enumerate(headers)}
    required = ['symbol','qty','trigger price','trailing %','stop %','tif']
    if any(r not in idx for r in required):
        logging.error('Missing columns in planner')
        return

    for row in rows[1:]:
        try:
            sym = row[idx['symbol']].upper()
            qty = int(row[idx['qty']])
            trigger = float(row[idx['trigger price']])
            trail_pct = float(row[idx['trailing %']])
            stop_pct = float(row[idx['stop %']])
            tif = row[idx['tif']]
            avg_price = trigger  # assume trigger equals avg buy for demo
        except Exception as e:
            logging.error(f'Parse error {row}: {e}')
            continue

        trail_amt = avg_price * trail_pct/100
        stop_price = avg_price * (1 - stop_pct/100)
        oca = f"OCA_{sym}_{int(time.time())}_{random.randint(0,999)}"
        contract = build_contract(sym)
        trail, stop = build_orders(sym, qty, trigger, trail_amt, stop_price, tif, oca)

        # place both orders
        oid = app.nextOrderId
        app.placeOrder(oid, contract, trail)
        logging.info(f'Placed TRAIL LIMIT {sym} id {oid}')
        app.nextOrderId += 1
        oid2 = app.nextOrderId
        app.placeOrder(oid2, contract, stop)
        logging.info(f'Placed STOP {sym} id {oid2} (OCO group {oca})')
        app.nextOrderId += 1

        # log to sheets
        timestamp = datetime.now().isoformat()
        live_data = [[timestamp, sym, qty, 'PLACED', oid]]
        active_data = [[timestamp, sym, qty, 'OCA', oca]]
        log_to_sheet(sheets, live_data, RANGE_LIVE)
        log_to_sheet(sheets, active_data, RANGE_ACTIVE)


def main():
    sheets = get_sheets_service()
    app = IBApp()
    app.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    while app.nextOrderId is None:
        time.sleep(0.1)

    logging.info('Starting main sync loop')
    try:
        while True:
            run_cycle(app, sheets)
            time.sleep(SYNC_INTERVAL)
    except KeyboardInterrupt:
        logging.info('Interrupted, disconnecting')
    finally:
        app.disconnect()
        logging.info('Disconnected')

if __name__ == '__main__':
    main()
