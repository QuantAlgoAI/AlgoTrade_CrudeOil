"""
High-Performance Database Manager for MCX Trading
- QuestDB: High-frequency tick data
- PostgreSQL: Trade logs and metadata
"""
import asyncio
import asyncpg
import pandas as pd
import logging
import os
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import threading
from queue import Queue
import time
from typing import Dict, List, Optional

# Try to import QuestDB
try:
    from questdb.ingress import Sender, Protocol, IngressError
    QUESTDB_AVAILABLE = True
except ImportError:
    QUESTDB_AVAILABLE = False

logger = logging.getLogger(__name__)

class QuestDBManager:
    """High-performance time-series database for tick data"""
    
    def __init__(self, host='localhost', port=9009, use_cloud=False):
        self.host = host
        self.port = port
        self.use_cloud = use_cloud
        self.sender = None
        self.pg_connection = None
        self.tick_queue = Queue(maxsize=10000)
        self.batch_size = 100
        self.batch_timeout = 1.0  # seconds
        self.worker_thread = None
        self.running = False
        
        # Fallback storage
        self.local_storage = []
        self.max_local_storage = 100000  # Keep last 100k ticks in memory
        
        # PostgreSQL wire protocol connection for queries
        self.pg_host = 'localhost'
        self.pg_port = 8812
        self.pg_user = 'admin'
        self.pg_password = 'quest'
        self.pg_database = 'qdb'
        
    def start(self):
        """Start the QuestDB connection and batch writer"""
        if not QUESTDB_AVAILABLE:
            logger.warning("QuestDB client library not found. Using in-memory storage only.")
            self.running = True
            return

        try:
            # 1. Connect to PostgreSQL wire protocol for queries and table management
            self._setup_pg_connection()
            if not self.pg_connection:
                raise Exception("Failed to connect to QuestDB via PostgreSQL wire protocol.")

            # 2. Setup tables
            self._setup_questdb_tables()

            # 3. Connect to ILP for high-speed ingestion
            # Use from_conf for robust connection string handling
            self.sender = Sender.from_conf(f'tcp::addr={self.host}:{self.port};')
            
            # 4. Start the background batch writer thread
            self.running = True
            self.worker_thread = threading.Thread(target=self._batch_writer, daemon=True)
            self.worker_thread.start()
            logger.info("✅ QuestDB Manager started successfully with native server connection.")

        except Exception as e:
            logger.error(f"❌ Failed to start QuestDB Manager: {e}")
            logger.warning("Falling back to in-memory storage only.")
            self.running = True # Still allow in-memory storage to work
            if self.pg_connection:
                self.pg_connection.close()
            if self.sender:
                self.sender.close()
            self.pg_connection = None
            self.sender = None
            
    def stop(self):
        """Stop the QuestDB connection"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(2.0)
        if self.sender:
            self.sender.close()
        if self.pg_connection:
            self.pg_connection.close()
            
    def queue_tick(self, tick_data: Dict):
        """Queue tick data for batch insertion or store in memory"""
        if not self.running:
            return
            
        # Always store in local memory for fast access
        self.local_storage.append(tick_data)
        if len(self.local_storage) > self.max_local_storage:
            self.local_storage = self.local_storage[-self.max_local_storage:]
            
        # Also queue for QuestDB if available
        if self.sender:
            try:
                self.tick_queue.put_nowait(tick_data)
            except:
                # Queue is full, drop oldest tick
                try:
                    self.tick_queue.get_nowait()
                    self.tick_queue.put_nowait(tick_data)
                except:
                    pass
                pass
                 
    def _batch_writer(self):
        """Background worker to batch write ticks to QuestDB"""
        batch = []
        last_flush = time.time()
        
        while self.running:
            try:
                # Wait for tick or timeout
                try:
                    tick = self.tick_queue.get(timeout=0.1)
                    batch.append(tick)
                except:
                    pass
                
                # Flush conditions
                should_flush = (
                    len(batch) >= self.batch_size or
                    (batch and time.time() - last_flush > self.batch_timeout)
                )
                
                if should_flush and batch:
                    logging.debug(f"Writer triggered flush for {len(batch)} ticks.")
                    self._flush_batch(batch)
                    batch.clear()
                    last_flush = time.time()
                    
            except Exception as e:
                logger.error(f"Batch writer error: {e}", exc_info=True)
                time.sleep(0.1)
                
    def _flush_batch(self, batch: List[Dict]):
        """Flush batch of ticks to QuestDB using modern client syntax"""
        if not self.sender or not batch:
            return
            
        try:
            # Use a DataFrame for efficient insertion
            df = pd.DataFrame(batch)
            
            # Ensure correct types before sending
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['ltp'] = df['ltp'].astype(float)
            df['volume'] = df['volume'].astype(int)
            df['oi'] = df['oi'].astype(int)

            # Correctly handle optional columns that might not be in every tick dict
            for col in ['open', 'high', 'low']:
                if col not in df.columns:
                    df[col] = 0.0  # Assign a default float value
                else:
                    # Fill any potential NaNs from dicts that had the key but with a None value
                    df[col] = df[col].fillna(0.0)
            
            # Now, safely cast to the correct type
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)

            self.sender.dataframe(
                df,
                table_name='mcx_ticks',
                symbols=['symbol', 'type'],
                at='timestamp')
                    
            self.sender.flush()
            logger.info(f"✅ Flushed {len(batch)} ticks to QuestDB")
            
        except IngressError as e:
            logger.error(f"❌ QuestDB Ingress Error: {e}")
        except Exception as e:
            logger.error(f"❌ Failed to flush batch: {e}")
            logger.error(f"Data sample that failed: {batch[0] if batch else 'N/A'}")
            
    def get_ohlc_data(self, symbol: str, contract_type: str, 
                      interval: str = '1s', limit: int = 1000) -> pd.DataFrame:
        """Get OHLC data for charting - optimized query"""
        try:
            query = f"""
            SELECT 
                timestamp,
                first(ltp) as open,
                max(ltp) as high,
                min(ltp) as low,
                last(ltp) as close,
                last(volume) as volume,
                last(oi) as oi
            FROM mcx_ticks 
            WHERE symbol = '{symbol}' AND type = '{contract_type}'
            AND timestamp > dateadd('h', -1, now())
            SAMPLE BY {interval}
            ORDER BY timestamp DESC
            LIMIT {limit}
            """
            
            # Use QuestDB REST API for fast queries
            import requests
            response = requests.get(f'http://{self.host}:9000/exec', 
                                  params={'query': query})
            
            if response.status_code == 200:
                data = response.json()
                df = pd.DataFrame(data['dataset'])
                if not df.empty:
                    df.columns = data['columns']
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df = df.set_index('timestamp')
                return df
            else:
                logger.error(f"QuestDB query failed: {response.text}")
                return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"Failed to get OHLC data: {e}")
            return pd.DataFrame()
        
    def get_recent_data(self, symbol: str = None, contract_type: str = None, 
                        limit: int = 1000) -> List[Dict]:
        """Get recent tick data from local storage"""
        if not self.local_storage:
            return []
            
        # Filter data if criteria provided
        filtered_data = self.local_storage
        
        if symbol:
            filtered_data = [tick for tick in filtered_data if tick.get('symbol') == symbol]
        if contract_type:
            filtered_data = [tick for tick in filtered_data if tick.get('type') == contract_type]
            
        # Return most recent data
        return filtered_data[-limit:] if limit else filtered_data
    
    def get_ohlc_from_memory(self, symbol: str, contract_type: str, 
                           interval_seconds: int = 5, limit: int = 100) -> pd.DataFrame:
        """Generate OHLC data from in-memory tick storage"""
        recent_data = self.get_recent_data(symbol, contract_type, limit=10000)
        
        if not recent_data:
            return pd.DataFrame()
            
        # Convert to DataFrame
        df = pd.DataFrame(recent_data)
        
        # Ensure timestamp is datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        
        # Resample to create OHLC
        interval_str = f'{interval_seconds}s'
        ohlc = df.resample(interval_str).agg({
            'ltp': ['first', 'max', 'min', 'last'],
            'volume': 'last',
            'oi': 'last'
        })
        
        # Flatten column names
        ohlc.columns = ['open', 'high', 'low', 'close', 'volume', 'oi']
        
        # Forward fill missing values
        ohlc = ohlc.ffill().tail(limit)
        
        return ohlc.reset_index()

    def _setup_pg_connection(self):
        """Setup PostgreSQL wire protocol connection for queries"""
        try:
            self.pg_connection = psycopg2.connect(
                host=self.pg_host,
                port=self.pg_port,
                user=self.pg_user,
                password=self.pg_password,
                database=self.pg_database
            )
            logger.info("✅ PostgreSQL wire protocol connection established")
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed: {e}")
            self.pg_connection = None
            
    def _setup_questdb_tables(self):
        """Create QuestDB tables for MCX data with corrected schema"""
        if not self.pg_connection:
            logger.warning("Cannot setup QuestDB tables without a PostgreSQL wire connection.")
            return
            
        try:
            with self.pg_connection.cursor() as cursor:
                # Create tick data table - CORRECTED SCHEMA
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mcx_ticks (
                        symbol SYMBOL,
                        type SYMBOL,
                        ltp DOUBLE,
                        volume LONG,
                        oi LONG,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        timestamp TIMESTAMP
                    ) timestamp(timestamp) PARTITION BY DAY WAL;
                """)

                # Create OHLC data table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mcx_ohlc (
                        symbol SYMBOL,
                        type SYMBOL,
                        timestamp TIMESTAMP,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        close DOUBLE,
                        volume LONG,
                        oi LONG
                    ) timestamp(timestamp) PARTITION BY DAY WAL;
                """)
                
                # Create trades table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mcx_trades (
                        trade_id STRING,
                        symbol SYMBOL,
                        timestamp TIMESTAMP,
                        side STRING,
                        quantity LONG,
                        price DOUBLE,
                        status STRING,
                        strategy STRING
                    ) timestamp(timestamp) PARTITION BY DAY WAL;
                """)
                
                self.pg_connection.commit()
                logger.info("✅ QuestDB tables created/verified successfully.")
            
        except Exception as e:
            logger.error(f"❌ Table setup failed: {e}")
            # If table setup fails, we probably can't proceed with DB operations
            raise e

    def stop(self):
        """Stop the QuestDB connection"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(2.0)
        if self.sender:
            self.sender.close()
        if self.pg_connection:
            self.pg_connection.close()
            
    def get_latest_ticks(self, symbol=None, limit=100):
        """Get latest tick data using PostgreSQL connection"""
        if not self.pg_connection:
            # Fallback to local storage
            if symbol:
                filtered_data = [tick for tick in self.local_storage if tick.get('symbol') == symbol]
                return filtered_data[-limit:] if filtered_data else []
            return self.local_storage[-limit:] if self.local_storage else []
            
        try:
            cursor = self.pg_connection.cursor(cursor_factory=RealDictCursor)
            if symbol:
                cursor.execute("""
                    SELECT * FROM mcx_ticks 
                    WHERE symbol = %s 
                    ORDER BY timestamp DESC 
                    LIMIT %s
                """, (symbol, limit))
            else:
                cursor.execute("""
                    SELECT * FROM mcx_ticks 
                    ORDER BY timestamp DESC 
                    LIMIT %s
                """, (limit,))
            
            result = cursor.fetchall()
            cursor.close()
            return [dict(row) for row in result]
            
        except Exception as e:
            logger.error(f"Failed to get latest ticks: {e}")
            return []

class PostgreSQLManager:
    """PostgreSQL for trade logs and metadata - using existing quantalgo_db"""
    
    def __init__(self, connection_string: str = None):
        # Use environment variables if connection string not provided
        if not connection_string:
            db_user = os.getenv('DB_USER', 'postgres')
            db_password = os.getenv('DB_PASSWORD', 'postgres') 
            db_host = os.getenv('DB_HOST', 'localhost')
            db_port = os.getenv('DB_PORT', '5432')
            db_name = os.getenv('DB_NAME', 'quantalgo_db')
            connection_string = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            
        self.connection_string = connection_string
        self.pool = None
        
    async def start(self):
        """Start PostgreSQL connection pool"""
        try:
            self.pool = await asyncpg.create_pool(self.connection_string)
            await self._create_tables()
            logger.info("PostgreSQL Manager started")
        except Exception as e:
            logger.error(f"Failed to start PostgreSQL: {e}")
            
    async def _create_tables(self):
        """Create additional tables if needed - work with existing schema"""
        # Add tick_data table for high-frequency data if QuestDB is not available
        create_tick_data_table = """
        CREATE TABLE IF NOT EXISTS tick_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            symbol VARCHAR(50) NOT NULL,
            contract_type VARCHAR(10) NOT NULL,
            token VARCHAR(20) NOT NULL,
            ltp DECIMAL(10, 4) NOT NULL,
            volume BIGINT DEFAULT 0,
            oi BIGINT DEFAULT 0,
            open_price DECIMAL(10, 4) DEFAULT 0,
            high_price DECIMAL(10, 4) DEFAULT 0,
            low_price DECIMAL(10, 4) DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_tick_data_timestamp ON tick_data(timestamp);
        CREATE INDEX IF NOT EXISTS idx_tick_data_symbol_type ON tick_data(symbol, contract_type);
        CREATE INDEX IF NOT EXISTS idx_tick_data_token ON tick_data(token);
        """
        
        # Modify existing trades table if needed (add columns that might be missing)
        alter_trades_table = """
        ALTER TABLE trade ADD COLUMN IF NOT EXISTS contract_type VARCHAR(10);
        ALTER TABLE trade ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(50);
        ALTER TABLE trade ADD COLUMN IF NOT EXISTS pnl DECIMAL(10, 2) DEFAULT 0;
        """
        
        async with self.pool.acquire() as conn:
            await conn.execute(create_tick_data_table)
            await conn.execute(alter_trades_table)
            logger.info("Database tables verified/created")
            
    async def log_trade(self, trade_data: Dict):
        """Log a trade to PostgreSQL using existing trade table"""
        try:
            query = """
            INSERT INTO trade (timestamp, symbol, contract_type, action, 
                              quantity, price, order_id, strategy_id, pnl)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT DO NOTHING
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, *trade_data.values())
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
            
    async def log_tick_data(self, tick_data: Dict):
        """Log tick data to PostgreSQL if QuestDB is not available"""
        try:
            query = """
            INSERT INTO tick_data (timestamp, symbol, contract_type, token, ltp, 
                                 volume, oi, open_price, high_price, low_price)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, 
                    tick_data['timestamp'],
                    tick_data['symbol'],
                    tick_data['type'],
                    tick_data['token'],
                    tick_data['ltp'],
                    tick_data['volume'],
                    tick_data['oi'],
                    tick_data.get('open', 0),
                    tick_data.get('high', 0),
                    tick_data.get('low', 0)
                )
        except Exception as e:
            logger.error(f"Failed to log tick data: {e}")


class OptimizedDataManager:
    """Combined manager for optimal performance"""
    
    def __init__(self, questdb_host='localhost', postgres_conn_str=None, use_native_questdb=True):
        self.questdb = QuestDBManager(questdb_host)
        self.use_native_questdb = use_native_questdb
        
        # Enable PostgreSQL with your configuration
        if not postgres_conn_str:
            postgres_conn_str = "postgresql://postgres:postgres@localhost:5432/quantalgo_db"
        self.postgres = PostgreSQLManager(postgres_conn_str)
        
        # Fast in-memory OHLC cache for 1-second charts
        self.ohlc_cache = {}
        self.cache_lock = threading.RLock()
        self.cache_size = 3600  # Keep 1 hour of 1-second bars
        
    def start(self):
        """Start databases with preference for native QuestDB"""
        if self.use_native_questdb:
            logger.info("Starting with native QuestDB preference...")
        
        self.questdb.start()
        
        # Start PostgreSQL in a separate thread to avoid blocking
        if self.postgres:
            def start_postgres():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.postgres.start())
                    logger.info("PostgreSQL Manager started successfully")
                except Exception as e:
                    logger.error(f"Failed to start PostgreSQL: {e}")
                    
            postgres_thread = threading.Thread(target=start_postgres, daemon=True)
            postgres_thread.start()
            
        logger.info("OptimizedDataManager started (PostgreSQL disabled temporarily)")
            
    def stop(self):
        """Stop both databases"""
        self.questdb.stop()
        
    def process_tick(self, tick_data: Dict):
        """Process incoming tick - optimized for speed"""
        # Queue for QuestDB (async)
        self.questdb.queue_tick(tick_data)
        
        # Update fast cache for 1-second charts
        self._update_ohlc_cache(tick_data)
        
    def _update_ohlc_cache(self, tick: Dict):
        """Update in-memory OHLC cache for 1-second intervals"""
        with self.cache_lock:
            key = f"{tick['symbol']}_{tick['type']}"
            
            # Round to second
            ts = pd.Timestamp(tick['timestamp']).floor('s')
            
            if key not in self.ohlc_cache:
                self.ohlc_cache[key] = {}
                
            cache = self.ohlc_cache[key]
            
            if ts not in cache:
                # New second bar
                cache[ts] = {
                    'open': tick['ltp'],
                    'high': tick['ltp'],
                    'low': tick['ltp'],
                    'close': tick['ltp'],
                    'volume': tick['volume'],
                    'oi': tick['oi']
                }
            else:
                # Update existing bar
                bar = cache[ts]
                bar['high'] = max(bar['high'], tick['ltp'])
                bar['low'] = min(bar['low'], tick['ltp'])
                bar['close'] = tick['ltp']
                bar['volume'] = tick['volume']
                bar['oi'] = tick['oi']
            
            # Cleanup old data
            if len(cache) > self.cache_size:
                old_keys = sorted(cache.keys())[:-self.cache_size]
                for old_key in old_keys:
                    del cache[old_key]
                    
    def get_fast_ohlc(self, symbol: str, contract_type: str, limit: int = 100, interval: str = '1s') -> pd.DataFrame:
        """Get fast OHLC data from cache, resampled to the desired interval."""
        with self.cache_lock:
            key = f"{symbol}_{contract_type}"
            
            if key not in self.ohlc_cache or not self.ohlc_cache[key]:
                return pd.DataFrame()
                
            cache = self.ohlc_cache[key]
            
            # Convert cache to DataFrame
            df = pd.DataFrame.from_dict(cache, orient='index')
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()

            if df.empty:
                return pd.DataFrame()

            # Resample if interval is not the base interval (1s)
            if interval != '1s':
                try:
                    # Resample to the target interval
                    resampled_df = df.resample(interval).agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'last',
                        'oi': 'last'
                    })
                    
                    # Drop rows with no data and forward fill to get continuous chart
                    resampled_df = resampled_df.dropna(subset=['open']).ffill()
                    
                    df = resampled_df

                except Exception as e:
                    logger.error(f"Failed to resample data for interval {interval}: {e}")
                    # Fallback to 1s data if resampling fails
                    pass

            # Apply limit and return
            return df.tail(limit)
    
    def log_trade(self, trade_data: Dict):
        """Log trade to PostgreSQL (non-blocking)"""
        if self.postgres and self.postgres.pool:
            def log_trade_async():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.postgres.log_trade(trade_data))
                except Exception as e:
                    logger.error(f"Failed to log trade: {e}")
                    
            trade_thread = threading.Thread(target=log_trade_async, daemon=True)
            trade_thread.start()
    
    def log_position_update(self, position_data: Dict):
        """Log position update to PostgreSQL"""
        if self.postgres and self.postgres.pool:
            def log_position_async():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    # Add position logging logic here
                    logger.debug(f"Position update logged: {position_data}")
                except Exception as e:
                    logger.error(f"Failed to log position: {e}")
                    
            position_thread = threading.Thread(target=log_position_async, daemon=True)
            position_thread.start()
    
    def get_database_status(self) -> Dict:
        """Get status of all database connections"""
        return {
            'questdb_running': self.questdb.running,
            'questdb_connected': self.questdb.sender is not None,
            'postgres_available': self.postgres is not None,
            'postgres_connected': self.postgres.pool is not None if self.postgres else False,
            'in_memory_cache_size': sum(len(cache) for cache in self.ohlc_cache.values()),
            'local_storage_size': len(self.questdb.local_storage)
        }
