"""
Optimized WebSocket Handler for MCX Trading System
High-performance tick data processing and real-time updates
"""
import logging
import threading
import time
from queue import Queue, Empty
from datetime import datetime
import pandas as pd
from typing import Dict, Any, Optional
import json

logger = logging.getLogger(__name__)

class OptimizedWebSocketHandler:
    """High-performance WebSocket handler for tick data and real-time updates"""
    
    def __init__(self):
        self.running = False
        self.tick_queue = Queue(maxsize=10000)
        self.ohlc_queue = Queue(maxsize=1000)
        self.workers = []
        
        # Performance counters
        self.ticks_processed = 0
        self.ohlc_updates = 0
        self.last_performance_log = time.time()
        
        # Data storage
        self.current_prices = {}
        self.ohlc_data = {}
        self.supported_intervals = ["1s", "5s", "10s", "30s", "1min", "5min"] # Default intervals
        
        logger.info("OptimizedWebSocketHandler initialized")
    
    def set_supported_intervals(self, intervals: list[str]):
        """Set the list of intervals to pre-calculate OHLC for."""
        self.supported_intervals = intervals
        logger.info(f"Supported OHLC intervals set to: {self.supported_intervals}")

    def start(self):
        """Start the optimized handler"""
        if self.running:
            return
        
        self.running = True
        
        # Start worker threads
        tick_worker = threading.Thread(target=self._tick_worker, daemon=True, name="TickWorker")
        ohlc_worker = threading.Thread(target=self._ohlc_worker, daemon=True, name="OHLCWorker")
        
        tick_worker.start()
        ohlc_worker.start()
        
        self.workers = [tick_worker, ohlc_worker]
        
        logger.info("OptimizedWebSocketHandler started with worker threads")
    
    def stop(self):
        """Stop the optimized handler"""
        self.running = False
        logger.info("OptimizedWebSocketHandler stopped")
    
    def process_tick(self, tick_data: Dict[str, Any]) -> bool:
        """Process incoming tick data"""
        try:
            if not self.tick_queue.full():
                self.tick_queue.put_nowait(tick_data)
                return True
            else:
                logger.warning("Tick queue full - dropping tick")
                return False
        except Exception as e:
            logger.error(f"Error processing tick: {e}")
            return False
    
    def process_market_data(self, tick_data: Dict[str, Any]) -> bool:
        """Process incoming market data (alias for process_tick for compatibility)"""
        return self.process_tick(tick_data)
    
    def get_current_prices(self) -> Dict[str, Any]:
        """Get current price data"""
        return self.current_prices.copy()
    
    def get_ohlc_data(self, symbol: str = None, interval: str = '1s') -> Dict[str, Any]:
        """Get OHLC data for a specific symbol and interval."""
        if symbol:
            return self.ohlc_data.get(symbol, {}).get(interval, {})
        # Return all data for a specific interval
        return {sym: data.get(interval, {}) for sym, data in self.ohlc_data.items()}

    def get_ohlc_data_smart(self, token: str, contract_type: str, limit: int = 100, interval: str = '1s') -> pd.DataFrame:
        """Smart OHLC data getter with fallback for a specific interval."""
        try:
            # Convert token-based request to symbol-based
            symbol_map = {
                'FUT': 'CRUDEOIL_FUT',
                'CE': 'CRUDEOIL_CE', 
                'PE': 'CRUDEOIL_PE'
            }
            
            symbol = symbol_map.get(contract_type, f"CRUDEOIL_{contract_type}")
            
            if symbol not in self.ohlc_data or interval not in self.ohlc_data[symbol] or not self.ohlc_data[symbol][interval]:
                # Return empty DataFrame if no data
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            
            # Convert OHLC data to DataFrame
            ohlc_dict = self.ohlc_data[symbol][interval]
            
            if not ohlc_dict:
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            
            # Sort by timestamp and get latest data
            sorted_keys = sorted(ohlc_dict.keys())[-limit:]
            
            data = []
            for key in sorted_keys:
                candle = ohlc_dict[key]
                data.append({
                    'timestamp': pd.to_datetime(key),
                    'open': candle.get('open', 0),
                    'high': candle.get('high', 0),
                    'low': candle.get('low', 0),
                    'close': candle.get('close', 0),
                    'volume': candle.get('volume', 0),
                    'oi': 0  # Add OI placeholder
                })
            
            df = pd.DataFrame(data)
            if not df.empty:
                df.set_index('timestamp', inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error getting smart OHLC data: {e}")
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics from the handler."""
        return {
            'ticks_processed': self.ticks_processed,
            'tick_queue_size': self.tick_queue.qsize(),
            'ohlc_updates': self.ohlc_updates,
            'ohlc_queue_size': self.ohlc_queue.qsize(),
        }

    def _tick_worker(self):
        """High-performance tick processing worker"""
        while self.running:
            try:
                # Get tick with timeout
                try:
                    tick = self.tick_queue.get(timeout=0.1)
                    self._process_tick_data(tick)
                    self.ticks_processed += 1
                    
                    # Performance logging
                    current_time = time.time()
                    if current_time - self.last_performance_log > 60:  # Every minute
                        logger.info(f"Processed {self.ticks_processed} ticks, {self.ohlc_updates} OHLC updates")
                        self.last_performance_log = current_time
                        
                except Empty:
                    continue
                    
            except Exception as e:
                logger.error(f"Tick worker error: {e}")
                time.sleep(0.001)
    
    def _ohlc_worker(self):
        """OHLC data processing worker"""
        while self.running:
            try:
                # Get OHLC update with timeout
                try:
                    ohlc_update = self.ohlc_queue.get(timeout=0.1)
                    self._process_ohlc_update(ohlc_update)
                    self.ohlc_updates += 1
                except Empty:
                    continue
                    
            except Exception as e:
                logger.error(f"OHLC worker error: {e}")
                time.sleep(0.001)
    
    def _process_tick_data(self, tick: Dict[str, Any]):
        """Process individual tick data"""
        try:
            # Handle both symbol-based and token-based data
            symbol = tick.get('symbol') or tick.get('trading_symbol', '')
            token = tick.get('token', '')
            tick_type = tick.get('type', 'UNKNOWN')
            
            # Create symbol from token and type if no symbol provided
            if not symbol and token and tick_type != 'UNKNOWN':
                symbol = f"CRUDEOIL_{tick_type}"
            
            price = float(tick.get('ltp', 0) or tick.get('last_price', 0))
            
            if symbol and price > 0:
                # Update current prices
                self.current_prices[symbol] = {
                    'symbol': symbol,
                    'token': token,
                    'type': tick_type,
                    'price': price,
                    'timestamp': tick.get('timestamp', datetime.now()).isoformat() if hasattr(tick.get('timestamp', datetime.now()), 'isoformat') else str(tick.get('timestamp', datetime.now())),
                    'volume': tick.get('volume', 0),
                    'oi': tick.get('oi', 0),
                    'open': tick.get('open', 0),
                    'high': tick.get('high', 0),
                    'low': tick.get('low', 0)
                }
                
                # Generate OHLC update for all supported intervals
                for interval in self.supported_intervals:
                    self._generate_ohlc_update(symbol, price, tick, interval)
                
        except Exception as e:
            logger.error(f"Error processing tick data: {e}")
    
    def _generate_ohlc_update(self, symbol: str, price: float, tick: Dict[str, Any], interval: str):
        """Generate OHLC update from tick data for a specific interval."""
        try:
            # Use tick timestamp if available, otherwise current time
            tick_time = tick.get('timestamp')
            if hasattr(tick_time, 'strftime'):
                current_time = tick_time
            else:
                current_time = datetime.now()

            # Convert pandas interval string to seconds
            if interval.endswith('s'):
                interval_seconds = int(interval[:-1])
            elif interval.endswith('min'):
                interval_seconds = int(interval[:-3]) * 60
            elif interval.endswith('h'):
                interval_seconds = int(interval[:-1]) * 3600
            else:
                interval_seconds = 1 # Default to 1 second

            # Round timestamp to the interval
            timestamp_seconds = current_time.timestamp()
            rounded_timestamp = int(timestamp_seconds / interval_seconds) * interval_seconds
            minute_key = datetime.fromtimestamp(rounded_timestamp).strftime('%Y-%m-%d %H:%M:%S')

            if symbol not in self.ohlc_data:
                self.ohlc_data[symbol] = {}
            
            if interval not in self.ohlc_data[symbol]:
                self.ohlc_data[symbol][interval] = {}

            if minute_key not in self.ohlc_data[symbol][interval]:
                # New interval candle
                self.ohlc_data[symbol][interval][minute_key] = {
                    'open': price,
                    'high': price,
                    'low': price,
                    'close': price,
                    'volume': tick.get('volume', 0),
                    'oi': tick.get('oi', 0),
                    'timestamp': minute_key
                }
            else:
                # Update existing candle
                candle = self.ohlc_data[symbol][interval][minute_key]
                candle['high'] = max(candle['high'], price)
                candle['low'] = min(candle['low'], price)
                candle['close'] = price
                
                # Update volume and OI (use latest values)
                if tick.get('volume', 0) > 0:
                    candle['volume'] = tick.get('volume', 0)
                if tick.get('oi', 0) > 0:
                    candle['oi'] = tick.get('oi', 0)
            
            # Keep only last 1000 candles per symbol/interval
            if len(self.ohlc_data[symbol][interval]) > 1000:
                # Remove oldest candles
                sorted_keys = sorted(self.ohlc_data[symbol][interval].keys())
                for old_key in sorted_keys[:-500]:  # Keep last 500
                    del self.ohlc_data[symbol][interval][old_key]
            
            # Queue OHLC update for broadcasting
            if not self.ohlc_queue.full():
                self.ohlc_queue.put_nowait({
                    'symbol': symbol,
                    'interval': interval,
                    'minute': minute_key,
                    'ohlc': self.ohlc_data[symbol][interval][minute_key].copy()
                })
                
        except Exception as e:
            logger.error(f"Error generating OHLC update for interval {interval}: {e}")
    
    def _process_ohlc_update(self, ohlc_update: Dict[str, Any]):
        """Process OHLC update (placeholder for future WebSocket broadcasting)"""
        try:
            # This is where you would broadcast OHLC updates via WebSocket
            # For now, just log the update
            symbol = ohlc_update.get('symbol', '')
            minute = ohlc_update.get('minute', '')
            
            logger.debug(f"OHLC update for {symbol} at {minute}")
            
        except Exception as e:
            logger.error(f"Error processing OHLC update: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get handler statistics"""
        return {
            'running': self.running,
            'ticks_processed': self.ticks_processed,
            'ohlc_updates': self.ohlc_updates,
            'queue_sizes': {
                'tick_queue': self.tick_queue.qsize(),
                'ohlc_queue': self.ohlc_queue.qsize()
            },
            'symbols_tracked': len(self.current_prices),
            'worker_count': len(self.workers)
        }
