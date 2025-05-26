#!/usr/bin/env python3
import os
import time
import logging
import configparser
import threading
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from ib_insync import IB, Stock, MarketOrder

# ─── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

# ─── Config ─────────────────────────────────────────────────────────────────────
cfg = configparser.ConfigParser()
cfg.read(os.path.join('config', 'config.ini'))

# IBKR Settings
IB_HOST      = cfg['IBKR']['Host']
IB_PORT      = int(cfg['IBKR']['Port'])
IB_CLIENT_ID = int(cfg['IBKR']['ClientId'])

# Trade Settings
SYMBOL         = cfg['TRADE']['Symbol']
TRIGGER_PRICE  = float(cfg['TRADE']['TriggerPrice'])
TRAILING_PCT   = float(cfg['TRADE']['TrailingPercent']) / 100
STOP_LOSS_PCT  = float(cfg['TRADE']['StopLossPercent']) / 100

# Google Sheets Settings
SPREADSHEET_ID       = cfg['GOOGLE']['SpreadsheetId']
SERVICE_ACCOUNT_FILE = cfg['GOOGLE']['ServiceAccountFile']
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Telegram Settings
TELEGRAM_TOKEN   = cfg['TELEGRAM']['BotToken']
TELEGRAM_CHAT_ID = cfg['TELEGRAM']['ChatId']

# Email Settings
EMAIL_HOST = cfg['EMAIL']['Host']
EMAIL_PORT = int(cfg['EMAIL']['Port'])
EMAIL_USER = cfg['EMAIL']['User']
EMAIL_PASS = cfg['EMAIL']['Password']
EMAIL_TO   = cfg['EMAIL']['To']

# ─── Globals ───────────────────────────────────────────────────────────────────
ib = IB()
contract = Stock(SYMBOL, 'SMART', 'USD', primaryExchange='ARCA')
trigger_started = False
active_orders = []

# ─── Google Sheets Client ─────────────────────────────────────────────────────
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
sheets_service = build('sheets', 'v4', credentials=creds)
sheet = sheets_service.spreadsheets()

# ─── Notifications ─────────────────────────────────────────────────────────────
def send_telegram(message):
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': message})
    except Exception as e:
        logging.error(f'Telegram error: {e}')

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
    except Exception as e:
        logging.error(f'Email error: {e}')

# ─── IBKR Helpers ──────────────────────────────────────────────────────────────
def refresh_price():
    ib.reqMktData(contract, '', False, False)
    ib.sleep(0.5)
    return ib.ticker(contract).marketPrice()

def get_position():
    for pos in ib.positions():
        if pos.contract.symbol == SYMBOL:
            return pos.position, pos.avgCost
    return 0, 0.0

# ─── Order Logic ───────────────────────────────────────────────────────────────
def place_oco_orders():
    price = refresh_price()
    qty, avg_cost = get_position()

    # 2️⃣ Prevent selling below average cost
    if avg_cost and price < avg_cost:
        msg = f'Price {price:.2f} below avg cost {avg_cost:.2f}; skipping sell.'
        logging.info(msg)
        send_telegram(msg)
        return

    trail_price = price * (1 - TRAILING_PCT)
    stop_price  = price * (1 - STOP_LOSS_PCT)

    trail = MarketOrder('SELL', qty or 1, auxPrice=trail_price)
    stop  = MarketOrder('SELL', qty or 1, auxPrice=stop_price)

    # 3️⃣ OCO pairing
    for o in (trail, stop):
        o.parentGroup = 'OCO1'

    ib.placeOrder(contract, trail)
    ib.placeOrder(contract, stop)
    active_orders.clear()
    active_orders.extend([trail, stop])

    msg = f'OCO placed: trail @ {trail_price:.2f}, stop @ {stop_price:.2f}'
    logging.info(msg)
    send_telegram(msg)
    send_email('IBKR Bot Orders Placed', msg)

def on_tick(tick):
    global trigger_started
    price = tick.last
    if price is None:
        return

    # 1️⃣ Conditional trigger
    if not trigger_started:
        if price > TRIGGER_PRICE:
            trigger_started = True
            msg = f'Trigger hit at {price:.2f}'
            logging.info(msg)
            send_telegram(msg)
            send_email('IBKR Bot Trigger Hit', msg)
        else:
            return

    # 4️⃣ Once triggered, place OCO only once
    if not active_orders:
        place_oco_orders()

# ─── Google Sheets Sync ────────────────────────────────────────────────────────
def write_trade_planner():
    qty, avg_cost = get_position()
    last_price = refresh_price()
    status = 'Triggered' if trigger_started else 'Pending'
    values = [[SYMBOL, qty, avg_cost, TRIGGER_PRICE,
               TRAILING_PCT * 100, STOP_LOSS_PCT * 100,
               'DAY', status, last_price]]
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="'Trade Planner'!A2:I2",
        valueInputOption='RAW',
        body={'values': values}
    ).execute()

def write_live_positions():
    qty, avg_cost = get_position()
    market_price = refresh_price()
    pnl = (market_price - avg_cost) * qty
    values = [[SYMBOL, qty, avg_cost, market_price, pnl]]
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="'Live Positions'!A2:E2",
        valueInputOption='RAW',
        body={'values': values}
    ).execute()

def write_active_orders():
    values = []
    for o in active_orders:
        values.append([
            o.orderId,
            SYMBOL,
            o.action,
            o.orderType,
            getattr(o, 'auxPrice', None),
            o.status
        ])
    # Clear old rows then write current
    sheet.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range="'Active Orders'!A2:F"
    ).execute()
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="'Active Orders'!A2:F",
        valueInputOption='RAW',
        body={'values': values}
    ).execute()

def sync_sheets_loop():
    while True:
        try:
            write_trade_planner()
            write_live_positions()
            write_active_orders()
            logging.info('Sheets synced')
        except Exception as e:
            logging.error(f'Sheets sync error: {e}')
        time.sleep(30)

# ─── Main Entry Point ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    ib.qualifyContracts(contract)
    logging.info(f'Connected to IBKR: {SYMBOL}')

    # Subscribe to live ticks
    ib.reqMktData(contract, '', False, False)
    ib.pendingTickersEvent += on_tick

    # Start background sheet sync
    threading.Thread(target=sync_sheets_loop, daemon=True).start()

    logging.info('Starting event loop')
    ib.run()
