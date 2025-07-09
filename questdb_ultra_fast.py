"""
Ultra-Fast QuestDB Manager for MCX Trading
Supports both standard and alternative ports
"""
import logging
import threading
import time
from queue import Queue
from datetime import datetime
import pandas as pd
import pytz
from typing import Dict, List, Optional
import requests
import json

# QuestDB ingress for ultra-fast inserts
try:
    from questdb.ingress import Sender, Protocol
    QUESTDB_AVAILABLE = True
except ImportError:
    QUESTDB_AVAILABLE = False

logger = logging.getLogger(__name__)

class UltraFastQuestDBManager:
    """Ultra-high performance QuestDB manager for trading data"""
    
    def __init__(self, host='localhost', port=9009, http_port=9000, 
                 alt_port=19009, alt_http_port=19000):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.alt_port = alt_port
        self.alt_http_port = alt_http_port
        self.running = False
        
        # Try standard ports first, then alternative
        self.active_port = None
        self.active_http_port = None
        
        # Ultra-fast ingress client
        self.sender = None
        
        # High-performance batch queue
        self.tick_queue = Queue(maxsize=100000)  # 100K tick buffer
        self.batch_size = 1000  # Batch 1000 ticks at once
        self.batch_timeout = 0.1  # 100ms max batch delay
        
        # Background workers
        self.ingress_worker = None
        self.batch_processor = None
        
        # Performance counters
        self.ticks_written = 0
        self.batches_written = 0
        self.last_performance_log = time.time()
    
    def start(self):
        """Start the QuestDB manager with port detection"""
        if not QUESTDB_AVAILABLE:
            logger.warning("QuestDB ingress client not available")
            return False
        
        # Try to detect which ports QuestDB is running on
        if self._test_connection(self.http_port):
            self.active_port = self.port
            self.active_http_port = self.http_port
            logger.info(f"QuestDB detected on standard ports: {self.http_port}, {self.port}")
        elif self._test_connection(self.alt_http_port):
            self.active_port = self.alt_port
            self.active_http_port = self.alt_http_port
            logger.info(f"QuestDB detected on alternative ports: {self.alt_http_port}, {self.alt_port}")
        else:
            logger.warning("QuestDB not detected on any ports - running in fallback mode")
            return False
        
        try:
            # Create sender with detected port
            self.sender = Sender.from_conf(f"http::addr={self.host}:{self.active_port};")
            
            self.running = True
            
            # Start background worker
            self.ingress_worker = threading.Thread(target=self._ingress_worker, daemon=True)
            self.ingress_worker.start()
            
            # Create optimized tables
            self._create_tables()
            
            logger.info("🔥 Ultra-fast QuestDB manager started!")
            logger.info(f"📊 Web Console: http://{self.host}:{self.active_http_port}")
            logger.info(f"⚡ InfluxDB Protocol: {self.host}:{self.active_port}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start QuestDB manager: {e}")
            return False
    
    def _test_connection(self, http_port):
        """Test if QuestDB is running on given port"""
        try:
            response = requests.get(f"http://{self.host}:{http_port}", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def _create_tables(self):
        """Create optimized tables for MCX trading"""
        try:
            tables = [
                """
                CREATE TABLE IF NOT EXISTS tick_data (
                    timestamp TIMESTAMP,
                    token SYMBOL,
                    contract_type SYMBOL,
                    ltp DOUBLE,
                    volume LONG,
                    oi LONG,
                    open_price DOUBLE,
                    high_price DOUBLE,
                    low_price DOUBLE,
                    change_pct DOUBLE
                ) TIMESTAMP(timestamp) PARTITION BY DAY;
                """,
                """
                CREATE TABLE IF NOT EXISTS ohlc_1min (
                    timestamp TIMESTAMP,
                    token SYMBOL,
                    contract_type SYMBOL,
                    open_price DOUBLE,
                    high_price DOUBLE,
                    low_price DOUBLE,
                    close_price DOUBLE,
                    volume LONG,
                    trades LONG
                ) TIMESTAMP(timestamp) PARTITION BY DAY;
                """,
                """
                CREATE TABLE IF NOT EXISTS trades (
                    timestamp TIMESTAMP,
                    token SYMBOL,
                    side SYMBOL,
                    quantity LONG,
                    price DOUBLE,
                    pnl DOUBLE,
                    strategy SYMBOL
                ) TIMESTAMP(timestamp) PARTITION BY DAY;
                """
            ]
            
            for table_sql in tables:
                response = requests.post(
                    f"http://{self.host}:{self.active_http_port}/exec",
                    data={'query': table_sql},
                    timeout=10
                )
                
                if response.status_code != 200:
                    logger.warning(f"Failed to create table: {response.text}")
            
            logger.info("✅ QuestDB tables created/verified")
            
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
    
    def add_tick(self, tick_data: Dict) -> bool:
        """Add tick data to high-performance queue"""
        if not self.running:
            return False
        
        try:
            if not self.tick_queue.full():
                self.tick_queue.put_nowait(tick_data)
                return True
            else:
                logger.warning("Tick queue full - dropping tick")
                return False
        except Exception as e:
            logger.error(f"Error queuing tick: {e}")
            return False
    
    def _ingress_worker(self):
        """High-performance ingress worker"""
        batch = []
        last_batch_time = time.time()
        
        while self.running:
            try:
                # Get tick with timeout
                try:
                    tick = self.tick_queue.get(timeout=0.01)  # 10ms timeout
                    batch.append(tick)
                except:
                    # No tick available
                    pass
                
                current_time = time.time()
                
                # Send batch if conditions met
                should_send = (
                    len(batch) >= self.batch_size or
                    (len(batch) > 0 and current_time - last_batch_time > self.batch_timeout)
                )
                
                if should_send and batch:
                    self._send_batch(batch)
                    batch = []
                    last_batch_time = current_time
                    
            except Exception as e:
                logger.error(f"Ingress worker error: {e}")
                time.sleep(0.001)  # 1ms sleep on error
    
    def _send_batch(self, batch: List[Dict]):
        """Send batch to QuestDB with ultra-fast protocol"""
        try:
            if not self.sender:
                return
            
            for tick in batch:
                # Convert tick to QuestDB format
                timestamp = tick.get('timestamp')
                if isinstance(timestamp, str):
                    timestamp = pd.to_datetime(timestamp).timestamp() * 1000000  # microseconds
                elif isinstance(timestamp, datetime):
                    timestamp = timestamp.timestamp() * 1000000
                
                # Send using line protocol (fastest method)
                self.sender.row(
                    'tick_data',
                    symbols={
                        'token': str(tick.get('token', '')),
                        'contract_type': str(tick.get('contract_type', ''))
                    },
                    columns={
                        'ltp': float(tick.get('ltp', 0)),
                        'volume': int(tick.get('volume', 0)),
                        'oi': int(tick.get('oi', 0)),
                        'open_price': float(tick.get('open_price', 0)),
                        'high_price': float(tick.get('high_price', 0)),
                        'low_price': float(tick.get('low_price', 0)),
                        'change_pct': float(tick.get('change_pct', 0))
                    },
                    at=int(timestamp)
                )
            
            # Flush batch
            self.sender.flush()
            
            # Update performance counters
            self.ticks_written += len(batch)
            self.batches_written += 1
            
            # Log performance periodically
            current_time = time.time()
            if current_time - self.last_performance_log > 10:  # Every 10 seconds
                rate = self.ticks_written / (current_time - self.last_performance_log + 1)
                logger.info(f"🔥 QuestDB Performance: {rate:.0f} ticks/sec, {self.batches_written} batches")
                self.last_performance_log = current_time
            
        except Exception as e:
            logger.error(f"Error sending batch to QuestDB: {e}")
    
    def query(self, sql: str) -> Optional[pd.DataFrame]:
        """Execute SQL query and return DataFrame"""
        try:
            if not self.active_http_port:
                return None
            
            response = requests.post(
                f"http://{self.host}:{self.active_http_port}/exec",
                data={'query': sql},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'dataset' in data:
                    return pd.DataFrame(data['dataset'])
            
            logger.warning(f"Query failed: {response.text}")
            return None
            
        except Exception as e:
            logger.error(f"Query error: {e}")
            return None
    
    def get_latest_ticks(self, token: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Get latest ticks for a token"""
        sql = f"""
        SELECT * FROM tick_data 
        WHERE token = '{token}' 
        ORDER BY timestamp DESC 
        LIMIT {limit}
        """
        return self.query(sql)
    
    def get_ohlc(self, token: str, timeframe: str = '1min', 
                 start_time: str = None, end_time: str = None) -> Optional[pd.DataFrame]:
        """Get OHLC data for a token"""
        time_filter = ""
        if start_time and end_time:
            time_filter = f"AND timestamp BETWEEN '{start_time}' AND '{end_time}'"
        
        sql = f"""
        SELECT 
            timestamp,
            first(ltp) as open_price,
            max(ltp) as high_price,
            min(ltp) as low_price,
            last(ltp) as close_price,
            sum(volume) as volume
        FROM tick_data 
        WHERE token = '{token}' {time_filter}
        SAMPLE BY {timeframe}
        ORDER BY timestamp DESC
        """
        return self.query(sql)
    
    def stop(self):
        """Stop the QuestDB manager"""
        self.running = False
        
        if self.sender:
            try:
                self.sender.close()
            except:
                pass
        
        if self.ingress_worker and self.ingress_worker.is_alive():
            self.ingress_worker.join(timeout=2)
        
        logger.info("QuestDB manager stopped")

# Convenience function for easy integration
def create_questdb_manager(**kwargs) -> UltraFastQuestDBManager:
    """Create and start QuestDB manager"""
    manager = UltraFastQuestDBManager(**kwargs)
    if manager.start():
        return manager
    else:
        logger.warning("QuestDB not available - using fallback storage")
        return None
