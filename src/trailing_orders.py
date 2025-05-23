#!/usr/bin/env python3
import os
import sys
import time
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from configparser import ConfigParser
from typing import Dict, List, Optional

from ib_insync import IB, Stock, Order
import gspread
from google.oauth2.service_account import Credentials

class TrailingStopBot:
    def __init__(self):
        self.setup_paths()
        self.setup_logging()
        self.load_config()
        self.ib = IB()
        self.sheet_service = None
        self.current_positions = {}

    def setup_paths(self):
        """Initialize required directories"""
        self.base_dir = Path(__file__).parent.parent
        self.config_dir = self.base_dir / "config"
        self.log_dir = self.base_dir / "logs"
        
        self.log_dir.mkdir(exist_ok=True)
        self.config_dir.mkdir(exist_ok=True)

    def setup_logging(self):
        """Configure logging system"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.log_dir / "trailing_stops.log"),
                logging.StreamHandler()
            ]
        )
        self.log = logging.getLogger(__name__)

    def load_config(self):
        """Load and validate configuration"""
        self.config = ConfigParser()
        config_path = self.config_dir / "config.ini"
        
        if not config_path.exists():
            self.log.error(f"Config file missing: {config_path}")
            sys.exit(1)
            
        self.config.read(config_path)
        
        # Validate required sections
        required_sections = ['IBKR', 'GOOGLE']
        for section in required_sections:
            if not self.config.has_section(section):
                self.log.error(f"Missing section in config: [{section}]")
                sys.exit(1)

    def connect_ibkr(self) -> bool:
        """Connect to IBKR with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self.ib.isConnected():
                    self.ib.connect(
                        self.config.get('IBKR', 'host'),
                        self.config.getint('IBKR', 'port'),
                        clientId=self.config.getint('IBKR', 'client_id')
                    )
                    self.log.info("Connected to IBKR TWS/Gateway")
                    return True
                return True
            except Exception as e:
                self.log.error(f"Connection attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        
        self.send_alert("IBKR Connection Failed", f"Could not connect after {max_retries} attempts")
        return False

    def init_google_sheets(self):
        """Initialize Google Sheets API"""
        creds_path = self.config_dir / "google_service_account.json"
        if not creds_path.exists():
            self.log.error(f"Google service account file missing: {creds_path}")
            sys.exit(1)
            
        try:
            creds = Credentials.from_service_account_file(
                creds_path,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            self.sheet_service = gspread.authorize(creds)
            self.log.info("Google Sheets API initialized")
        except Exception as e:
            self.log.error(f"Google Sheets init failed: {str(e)}")
            sys.exit(1)

    def get_sheet_data(self) -> List[Dict]:
        """Fetch data from Google Sheet"""
        try:
            sheet = self.sheet_service.open_by_key(
                self.config.get('GOOGLE', 'sheet_id')
            ).sheet1
            return sheet.get_all_records()
        except Exception as e:
            self.log.error(f"Failed to get sheet data: {str(e)}")
            self.send_alert("Google Sheets Error", str(e))
            raise

    def validate_row(self, row: Dict) -> bool:
        """Validate a row from the spreadsheet"""
        required_fields = ['Symbol', 'Quantity', 'Avg Price', 'Trailing %', 'Limit Offset %']
        if not all(field in row for field in required_fields):
            self.log.warning(f"Missing fields in row: {row.get('Symbol', 'Unknown')}")
            return False
            
        try:
            qty = float(row['Quantity'])
            avg_price = float(row['Avg Price'])
            trail_pct = float(row['Trailing %'])
            limit_offset = float(row['Limit Offset %'])
            
            if qty == 0:
                self.log.warning("Quantity cannot be zero")
                return False
                
            if trail_pct <= 0 or limit_offset < 0:
                self.log.warning("Trailing % must be positive and Limit Offset cannot be negative")
                return False
                
            return True
        except ValueError as e:
            self.log.warning(f"Invalid number format: {str(e)}")
            return False

    def create_order(self, row: Dict) -> Optional[Order]:
        """Create IBKR order from validated row"""
        try:
            qty = float(row['Quantity'])
            avg_price = float(row['Avg Price'])
            trail_pct = float(row['Trailing %'])
            limit_offset = float(row['Limit Offset %'])
            
            order = Order()
            order.action = 'SELL' if qty > 0 else 'BUY'
            order.totalQuantity = abs(qty)
            order.orderType = 'TRAIL LIMIT'
            order.trailingPercent = trail_pct
            order.lmtPriceOffset = limit_offset
            order.tif = row.get('TIF', 'GTC')
            
            # Calculate trigger price
            trigger_price = round(avg_price * (1 + trail_pct/100), 2)
            order.auxPrice = trigger_price
            
            # Validate sell orders
            if order.action == 'SELL' and trigger_price < avg_price:
                raise ValueError(f"Trigger price {trigger_price} below avg price {avg_price}")
                
            return order
        except Exception as e:
            self.log.error(f"Order creation failed: {str(e)}")
            return None

    def process_orders(self, rows: List[Dict]):
        """Process all valid orders from sheet"""
        success_count = 0
        for row in rows:
            if not self.validate_row(row):
                continue
                
            order = self.create_order(row)
            if not order:
                continue
                
            try:
                contract = Stock(row['Symbol'], 'SMART', 'USD')
                trade = self.ib.placeOrder(contract, order)
                success_count += 1
                self.log.info(f"Submitted {order.action} {order.totalQuantity} {row['Symbol']} "
                            f"@ Trail {order.trailingPercent}%")
            except Exception as e:
                self.log.error(f"Order failed for {row['Symbol']}: {str(e)}")
                
        self.log.info(f"Order summary: {success_count} succeeded, {len(rows) - success_count} failed")
        if success_count < len(rows):
            self.send_alert(
                "Partial Order Completion",
                f"Only {success_count} of {len(rows)} orders were placed"
            )

    def send_alert(self, subject: str, body: str):
        """Send email notification if enabled"""
        if not self.config.getboolean('EMAIL', 'enabled', fallback=False):
            return
            
        try:
            msg = EmailMessage()
            msg.set_content(body)
            msg['Subject'] = f"[IBKR Bot] {subject}"
            msg['From'] = self.config.get('EMAIL', 'sender')
            msg['To'] = self.config.get('EMAIL', 'recipient')

            with smtplib.SMTP_SSL(
                self.config.get('EMAIL', 'smtp_server'),
                self.config.getint('EMAIL', 'smtp_port')
            ) as server:
                server.login(
                    self.config.get('EMAIL', 'smtp_user'),
                    self.config.get('EMAIL', 'smtp_password')
                )
                server.send_message(msg)
            self.log.info(f"Sent alert: {subject}")
        except Exception as e:
            self.log.error(f"Failed to send email: {str(e)}")

    def run(self):
        """Main execution loop"""
        self.log.info("Starting IBKR Trailing Stop Bot")
        
        try:
            # Initialize services
            if not self.connect_ibkr():
                return
            self.init_google_sheets()
            
            # Main loop
            while True:
                start_time = time.time()
                
                try:
                    # Get and process orders
                    rows = self.get_sheet_data()
                    self.process_orders(rows)
                    
                    # Wait for next cycle
                    elapsed = time.time() - start_time
                    sleep_time = max(300 - elapsed, 0)  # 5 minutes between runs
                    self.log.info(f"Next run in {sleep_time/60:.1f} minutes")
                    time.sleep(sleep_time)
                    
                except Exception as e:
                    self.log.error(f"Processing error: {str(e)}")
                    self.send_alert("Processing Error", str(e))
                    time.sleep(60)  # Wait before retry
                    
        except KeyboardInterrupt:
            self.log.info("Shutdown requested")
        finally:
            if self.ib.isConnected():
                self.ib.disconnect()
            self.log.info("Bot stopped")

if __name__ == "__main__":
    bot = TrailingStopBot()
    bot.run()