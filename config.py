import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key-here')
    FLASK_ENV = os.getenv('FLASK_ENV', 'production')
    
    # Database settings
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///trading.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Socket.IO settings
    SOCKETIO_MESSAGE_QUEUE = os.getenv('REDIS_URL', None)
    
    # API settings
    API_KEY = os.getenv('API_KEY')
    API_SECRET = os.getenv('API_SECRET')
    
    # Email settings
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    
    # Telegram settings
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
    
    # Trading settings
    RISK_PER_TRADE = float(os.getenv('RISK_PER_TRADE', 1.0))
    MAX_POSITIONS = int(os.getenv('MAX_POSITIONS', 3))
    LOSS_LIMIT = float(os.getenv('LOSS_LIMIT', 5.0))
    TARGET_PROFIT = float(os.getenv('TARGET_PROFIT', 10.0))
    
    # Performance monitoring
    REFRESH_INTERVAL = int(os.getenv('REFRESH_INTERVAL', 5000))  # milliseconds
    
    # Backtesting
    BACKTEST_DATA_DIR = os.getenv('BACKTEST_DATA_DIR', 'backtest/data')
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/trading_bot.log')
    LOG_MAX_SIZE = os.getenv('LOG_MAX_SIZE', '10MB')
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', 5))

    # WebSocket Configuration
    WS_RECONNECT_ATTEMPTS = int(os.getenv('WS_RECONNECT_ATTEMPTS', 5))
    WS_PING_INTERVAL = int(os.getenv('WS_PING_INTERVAL', 30))
    WS_PING_TIMEOUT = int(os.getenv('WS_PING_TIMEOUT', 10))

    # Strategy Configuration
    STRATEGY_FAST_EMA = int(os.getenv('STRATEGY_FAST_EMA', 9))
    STRATEGY_SLOW_EMA = int(os.getenv('STRATEGY_SLOW_EMA', 21))
    STRATEGY_RSI_PERIOD = int(os.getenv('STRATEGY_RSI_PERIOD', 14))
    STRATEGY_ATR_PERIOD = int(os.getenv('STRATEGY_ATR_PERIOD', 14))
    STRATEGY_VWAP_PERIOD = int(os.getenv('STRATEGY_VWAP_PERIOD', 20))

    # Market Data Configuration
    MARKET_DATA_BUFFER_SIZE = int(os.getenv('MARKET_DATA_BUFFER_SIZE', 1000))
    OHLC_INTERVALS = os.getenv('OHLC_INTERVALS', '1s,5s,30s,1min,5min,15min,1h')

    # Monitoring Configuration
    HEALTH_CHECK_INTERVAL = int(os.getenv('HEALTH_CHECK_INTERVAL', 60))
    PERFORMANCE_MONITORING = os.getenv('PERFORMANCE_MONITORING', 'true').lower() == 'true'
    ERROR_ALERT_THRESHOLD = int(os.getenv('ERROR_ALERT_THRESHOLD', 5))

    # Gmail Configuration
    GMAIL_USER = os.getenv('GMAIL_USER')
    GMAIL_PASS = os.getenv('GMAIL_PASS')
