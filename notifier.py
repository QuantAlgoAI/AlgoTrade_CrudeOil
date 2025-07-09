import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

class NotificationManager:
    def __init__(self):
        # Telegram settings
        self.telegram_token = os.getenv('TELEGRAM_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.telegram_enabled = bool(self.telegram_token and self.telegram_chat_id)
        
        # Email settings
        self.email_user = os.getenv('GMAIL_USER')
        self.email_pass = os.getenv('GMAIL_PASS')
        self.email_enabled = bool(self.email_user and self.email_pass)
        
        self.setup_logging()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def send_telegram(self, message: str) -> bool:
        if not self.telegram_enabled:
            self.logger.debug("Telegram notifications disabled - missing credentials")
            return False

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {'chat_id': self.telegram_chat_id, 'text': message}

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            self.logger.debug("Telegram message sent successfully")
            return True
        except requests.exceptions.RequestException as e:
            self.logger.debug(f"Telegram error: {e}")
            return False

    def send_email(self, subject: str, body: str, to_email: str) -> bool:
        if not self.email_enabled:
            self.logger.debug("Email notifications disabled - missing credentials")
            return False

        msg = MIMEMultipart()
        msg['From'] = self.email_user
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(self.email_user, self.email_pass)
                server.send_message(msg)
            self.logger.debug("Email sent successfully")
            return True
        except Exception as e:
            self.logger.debug(f"Email error: {e}")
            return False

    def notify_startup(self, instrument: str, expiry: str, spot_price: float, atm_strike: float, 
                      monitored: list, strategy: str, ce_price: Optional[float] = None, 
                      pe_price: Optional[float] = None):
        """
        Notify when the trading bot starts
        
        Args:
            instrument: Trading instrument (e.g., 'CRUDEOIL')
            expiry: Expiry date (e.g., '17JUL2025')
            spot_price: Current spot price
            atm_strike: ATM strike price
            monitored: List of monitored instruments
            strategy: Strategy name
            ce_price: Current CE option price (optional)
            pe_price: Current PE option price (optional)
        """
        now = datetime.now().strftime('%H:%M:%S')
        
        # Format expiry date for symbol construction
        expiry_formatted = expiry.replace('JUL', 'JUN')  # Convert to symbol format
        expiry_no_year = expiry_formatted[:-4]  # Remove year
        year_short = expiry_formatted[-4:][-2:]  # Get last 2 digits of year
        symbol_expiry = f"{expiry_no_year}{year_short}"  # e.g., 19JUN25
        
        # Construct full symbols
        fut_symbol = f"{instrument}{symbol_expiry}FUT"
        ce_symbol = f"{instrument}{symbol_expiry}{atm_strike:.0f}CE"
        pe_symbol = f"{instrument}{symbol_expiry}{atm_strike:.0f}PE"
        
        # Format option prices with availability check
        ce_price_str = f" (ATM - LTP: ₹{ce_price:,.2f})" if ce_price is not None else " (ATM)"
        pe_price_str = f" (ATM - LTP: ₹{pe_price:,.2f})" if pe_price is not None else " (ATM)"
        
        msg = (
            f"🤖 <b>Trading Bot Started</b>\n"
            f"<b>Instrument:</b> {instrument}\n"
            f"<b>Expiry:</b> {expiry}\n"
            f"<b>Spot Price:</b> ₹{spot_price:,.2f}\n"
            f"<b>ATM Strike:</b> {atm_strike:,.0f}\n"
            f"<b>Strategy:</b> {strategy}\n"
            f"<b>Monitoring:</b>\n"
            f"• {fut_symbol} (Spot Price: ₹{spot_price:,.2f})\n"
            f"• {ce_symbol}{ce_price_str}\n"
            f"• {pe_symbol}{pe_price_str}\n"
            f"⏰ <i>{now}</i>"
        )
        
        self.send_telegram(msg)
        self.send_email("Trading Bot Started", msg, self.email_user)

    def notify_trade_entry(self, trade: Dict[str, Any], strategy: str, signal_details: str):
        """Notify when a new trade is entered"""
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"🟢 <b>Trade Entry</b>\n"
            f"<b>Strategy:</b> {strategy}\n"
            f"<b>Signal:</b> {signal_details}\n"
            f"<b>Symbol:</b> {trade['symbol']}\n"
            f"<b>Type:</b> {'Call Option (CE)' if trade.get('option_type') == 'CE' else 'Put Option (PE)'}\n"
            f"<b>Strike:</b> {trade['strike']}\n"
            f"<b>Entry Price:</b> ₹{trade['entry_price']:,.2f}\n"
            f"<b>Quantity:</b> {trade['quantity']}\n"
            f"<b>Stop Loss:</b> ₹{trade['stop_loss']:,.2f}\n"
            f"<b>Target:</b> ₹{trade['target']:,.2f}\n"
            f"⏰ <i>{now}</i>"
        )
        
        self.send_telegram(msg)
        self.send_email("Trade Entry Alert", msg, self.email_user)

    def notify_trade_exit(self, trade: Dict[str, Any], strategy: str, reason: str, signal_details: str):
        """Notify when a trade is exited"""
        now = datetime.now().strftime('%H:%M:%S')
        duration = self._format_duration(trade.get('entry_time'), trade.get('exit_time'))
        pnl = trade.get('pnl', 0)
        pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
        
        msg = (
            f"🔴 <b>Trade Exit</b>\n"
            f"<b>Strategy:</b> {strategy}\n"
            f"<b>Symbol:</b> {trade['symbol']}\n"
            f"<b>Exit Price:</b> ₹{trade['exit_price']:,.2f}\n"
            f"<b>P&L:</b> {pnl_str}\n"
            f"<b>Duration:</b> {duration}\n"
            f"<b>Reason:</b> {reason} (Signal: {signal_details})\n"
            f"⏰ <i>{now}</i>"
        )
        
        self.send_telegram(msg)
        self.send_email("Trade Exit Alert", msg, self.email_user)

    def notify_error(self, context: str, message: str):
        """Notify when an error occurs"""
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"⚠️ <b>Error</b>\n"
            f"<b>Context:</b> {context}\n"
            f"<b>Message:</b> {message}\n"
            f"⏰ <i>{now}</i>"
        )
        
        self.send_telegram(msg)
        self.send_email("Error Alert", msg, self.email_user)

    def notify_strategy_update(self, strategy: str, indicators: Dict[str, Any], regime: str):
        """Notify when strategy indicators update significantly"""
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"📊 <b>Strategy Update</b>\n"
            f"<b>Strategy:</b> {strategy}\n"
            f"<b>Market Regime:</b> {regime}\n"
            f"<b>Indicators:</b>\n"
        )
        for name, value in indicators.items():
            msg += f"• {name}: {value:.2f}\n"
        msg += f"⏰ <i>{now}</i>"
        
        self.send_telegram(msg)
        self.send_email("Strategy Update", msg, self.email_user)

    def notify_market_alert(self, alert_type: str, message: str, data: Dict[str, Any]):
        """Notify for important market events"""
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"🚨 <b>Market Alert - {alert_type}</b>\n"
            f"<b>Message:</b> {message}\n"
        )
        for key, value in data.items():
            msg += f"<b>{key}:</b> {value}\n"
        msg += f"⏰ <i>{now}</i>"
        
        self.send_telegram(msg)
        self.send_email(f"Market Alert - {alert_type}", msg, self.email_user)

    def update_settings(self, settings: Dict[str, Any]):
        """Update notification settings"""
        if 'telegram' in settings:
            self.telegram_enabled = settings['telegram'].get('enabled', self.telegram_enabled)
            self.telegram_token = settings['telegram'].get('token', self.telegram_token)
            self.telegram_chat_id = settings['telegram'].get('chat_id', self.telegram_chat_id)
        
        if 'email' in settings:
            self.email_enabled = settings['email'].get('enabled', self.email_enabled)
            self.email_user = settings['email'].get('user', self.email_user)
            self.email_pass = settings['email'].get('pass', self.email_pass)

    def test_notifications(self) -> Dict[str, bool]:
        """Recompute enabled flags and send test messages."""
        # Refresh enabled flags in case credentials were injected after init
        self.telegram_enabled = bool(self.telegram_token and self.telegram_chat_id)
        self.email_enabled = bool(self.email_user and self.email_pass)
        """Test both notification channels"""
        test_msg = (
            f"🧪 <b>Test Notification</b>\n"
            f"This is a test message from your Algo Trading Bot.\n"
            f"⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        
        telegram_success = self.send_telegram(test_msg)
        email_success = self.send_email("Test Notification", test_msg, self.email_user)
        
        return {
            'telegram': telegram_success,
            'email': email_success
        }

    @staticmethod
    def _format_duration(start: Optional[datetime], end: Optional[datetime]) -> str:
        """Format time duration between two timestamps"""
        if not start or not end:
            return "-"
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        delta = end - start
        return str(delta).split('.')[0]  # HH:MM:SS 