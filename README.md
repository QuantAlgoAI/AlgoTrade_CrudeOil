# Crude Oil Options Trading Bot v2.0

A comprehensive, production-ready automated trading system for Crude Oil options on Angel One (Angel Broking) platform. This version features a completely modernized, modular architecture with enhanced security, error handling, and monitoring capabilities.

## 🚀 What's New in v2.0

### Major Improvements
- **Modular Architecture**: Complete separation of concerns with dedicated managers
- **Enhanced Security**: Environment-based configuration with secure credential management
- **Robust Error Handling**: Circuit breakers, retry logic, and comprehensive logging
- **Database Integration**: SQLAlchemy-based persistent storage for trades and analytics
- **Real-time Dashboard**: Modern web interface with live charts and monitoring
- **Production Ready**: Comprehensive testing, deployment scripts, and monitoring

### Architecture Overview
``` 
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Web UI        │◄──►│   Main App       │◄──►│  Configuration  │
│ (Flask/SocketIO)│    │   (main.py)      │    │   (config.py)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                        │
         ▼                       ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ WebSocket Mgr   │◄──►│ Strategy Manager │◄──►│ Error Handling  │
│(websocket_mgr.py)│    │(strategy_mgr.py) │    │(error_handling) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                        │
         ▼                       ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Trade Manager  │◄──►│    Database      │◄──►│   Market Data   │
│(trade_manager)  │    │  (database.py)   │    │ (marketdata.py) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 📋 Requirements

- Python 3.8 or higher
- Windows/Linux/macOS
- Angel One trading account with API access
- 2GB RAM minimum
- Stable internet connection

## 🛠️ Quick Setup

### 1. Environment Setup
```bash
# Clone or download the project
cd CrudeOil002

# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # Linux/macOS

# Install dependencies and setup
python deploy.py setup
```

### 2. Configuration
Edit the `.env` file created during setup:

```env
# Angel One API Configuration
ANGEL_ONE_API_KEY=your_api_key_here
ANGEL_ONE_SECRET_KEY=your_secret_key_here
ANGEL_ONE_CLIENT_ID=your_client_id_here
ANGEL_ONE_TOTP_SECRET=your_totp_secret_here

# Database Configuration
DATABASE_URL=sqlite:///trading_bot.db

# Trading Configuration
TRADING_ENABLED=true
MAX_POSITION_SIZE=100000
RISK_PERCENTAGE=2.0

# Notification Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
```

### 3. Start the Application
```bash
# Run integration tests
python deploy.py test

# Start the application
python deploy.py start

# Start in debug mode
python deploy.py start --debug

# Start on custom port
python deploy.py start --port 8080
```

## 🎯 Features

### Trading Features
- **Multi-Strategy Support**: Implement and run multiple strategies simultaneously
- **Risk Management**: Position sizing, stop-loss, take-profit automation
- **Options Trading**: Specialized for Crude Oil CE/PE options
- **Market Regime Detection**: Trend, range, and volatility-based trading
- **Order Flow Analysis**: Buy/sell pressure and order imbalance indicators

### Technical Features
- **Real-time Data**: WebSocket-based market data streaming
- **Advanced Indicators**: EMA, RSI, VWAP, ATR, Bollinger Bands
- **Backtesting**: Historical strategy performance analysis
- **Database Storage**: Persistent trade history and performance metrics
- **Web Dashboard**: Live monitoring and control interface

### Safety Features
- **Emergency Stop**: Instant position closure with one click
- **Circuit Breakers**: Automatic trading halt on excessive losses
- **Connection Monitoring**: Automatic reconnection and error recovery
- **Comprehensive Logging**: Detailed audit trail for all operations
- **Position Limits**: Configurable risk controls and position sizing

## 📊 Web Interface

Access the web dashboard at `http://localhost:5000` after starting the application.

### Dashboard Tabs
1. **Dashboard**: Live market data, charts, and position overview
2. **Trade**: Active positions, trade history, and manual controls
3. **Strategy**: Strategy performance, signals, and configuration
4. **Settings**: System configuration, API settings, and notifications

### Key Features
- **Live Charts**: Real-time OHLC candlestick charts with indicators
- **Position Monitoring**: Current P&L, unrealized gains/losses
- **Signal Alerts**: Real-time strategy signals and recommendations
- **Performance Analytics**: Win rate, Sharpe ratio, maximum drawdown
- **System Status**: Connection status, component health monitoring

## 🔧 Module Overview

### Core Modules

#### `main.py` - Application Entry Point
- Coordinates all components
- Flask web server with SocketIO
- API endpoints for frontend
- System lifecycle management

#### `websocket_manager.py` - Market Data Handler
- Angel One SmartAPI WebSocket connection
- Real-time tick data processing
- Automatic reconnection logic
- Market data broadcasting

#### `trade_manager.py` - Order Execution
- Order placement and management
- Position tracking and P&L calculation
- Risk management controls
- Trade lifecycle automation

#### `strategy_manager.py` - Strategy Orchestration
- Multiple strategy coordination
- Signal generation and filtering
- Strategy performance tracking
- Parameter optimization

#### `database.py` - Data Persistence
- SQLAlchemy models for trades, market data
- Performance metrics storage
- Historical data management
- Database connection pooling

#### `error_handling.py` - Reliability
- Circuit breaker pattern implementation
- Retry logic with exponential backoff
- Comprehensive logging system
- Error recovery procedures

#### `config.py` - Configuration Management
- Environment-based configuration
- Secure credential handling
- Configuration validation
- Runtime parameter updates

#### `marketdata.py` - Data Processing
- Tick data standardization
- Technical indicator calculations
- Market regime detection
- Historical data analysis

### Legacy Modules
- `mcx.py` - Original monolithic implementation (deprecated)
- `strategy.py` - Original strategy implementation
- `notifier.py` - Notification system

## 📈 Trading Strategies

### Built-in Strategies

#### High Win Rate Strategy
- **Focus**: Consistent small profits with high success rate
- **Indicators**: EMA crossover, RSI, VWAP
- **Risk Management**: Tight stop-losses, quick profit taking
- **Best For**: Range-bound markets, high-frequency trading

#### Trend Following Strategy
- **Focus**: Capturing medium to long-term trends
- **Indicators**: Moving averages, ATR, trend strength
- **Risk Management**: Trailing stops, position scaling
- **Best For**: Trending markets, momentum plays

#### Mean Reversion Strategy
- **Focus**: Trading oversold/overbought conditions
- **Indicators**: Bollinger Bands, RSI, volatility
- **Risk Management**: Multiple entry/exit points
- **Best For**: Range-bound markets, high volatility

### Custom Strategy Development
Create custom strategies by extending the base strategy class:

```python
from strategy_manager import BaseStrategy, TradingSignal, SignalType

class MyCustomStrategy(BaseStrategy):
    def process_tick(self, tick_data):
        # Your custom logic here
        if self.should_buy(tick_data):
            return TradingSignal(
                signal_type=SignalType.BUY,
                confidence=0.8,
                price=tick_data.ltp
            )
        return None
```

## 🔒 Security Features

### API Security
- Environment variable storage for credentials
- TOTP-based two-factor authentication
- Secure WebSocket connections
- Rate limiting and request validation

### Risk Controls
- Maximum position size limits
- Daily loss limits
- Drawdown-based trading halt
- Emergency stop functionality

### Data Protection
- Encrypted database storage
- Secure session management
- Audit logging for all operations
- Regular security updates

## 📊 Monitoring & Analytics

### Performance Metrics
- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Gross profit / Gross loss ratio
- **Sharpe Ratio**: Risk-adjusted return measurement
- **Maximum Drawdown**: Largest peak-to-trough decline
- **Average Trade Duration**: Time in market per trade

### System Monitoring
- **WebSocket Connection Status**: Real-time connectivity
- **Trade Execution Latency**: Order processing speed
- **Strategy Performance**: Individual strategy metrics
- **Error Rates**: System reliability indicators
- **Resource Usage**: CPU, memory, disk utilization

## 🚨 Troubleshooting

### Common Issues

#### Connection Problems
```bash
# Check network connectivity
python deploy.py test

# Verify API credentials
python -c "from config import config; print(config.angel_one.api_key)"

# Check system status
python deploy.py status
```

#### Database Issues
```bash
# Backup current database
python deploy.py backup

# Reset database (caution: loses data)
rm trading_bot.db
python main.py  # Will recreate database
```

#### Performance Issues
```bash
# Clean old logs
python deploy.py clean

# Check system resources
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"
```

### Log Analysis
Logs are stored in the `logs/` directory with daily rotation:
- `system.log` - General application logs
- `websocket.log` - Market data connection logs
- `trade.log` - Order execution logs
- `strategy.log` - Strategy signal logs
- `error.log` - Error and exception logs

## 🔄 Updates & Maintenance

### Regular Maintenance
```bash
# Update dependencies
pip install -r requirements.txt --upgrade

# Backup database
python deploy.py backup

# Clean old logs (weekly)
python deploy.py clean

# Check system health
python deploy.py status
```

### Version Updates
1. Backup current installation
2. Update source code
3. Update dependencies: `pip install -r requirements.txt --upgrade`
4. Run tests: `python deploy.py test`
5. Restart application: `python deploy.py start`

## 📞 Support & Contact

### Documentation
- Code documentation: See inline comments and docstrings
- API reference: Available at `/api/docs` when running (if enabled)
- Strategy guides: Check `docs/` folder for detailed guides

### Support
- Create issues for bugs or feature requests
- Check logs for troubleshooting information
- Review configuration for common setup issues

## 📄 License & Disclaimer

**Trading Disclaimer**: This software is for educational and research purposes. Trading involves significant financial risk. Past performance does not guarantee future results. Use at your own risk.

**License**: This project is provided as-is without warranty. Commercial use requires proper licensing.

---

## Quick Start Commands

```bash
# Complete setup
python deploy.py setup

# Start trading
python deploy.py start

# Run tests
python deploy.py test

# Check status
python deploy.py status

# Emergency backup
python deploy.py backup
```
    
**Happy Trading! 🚀📈** 
