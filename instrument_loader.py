import pandas as pd
import os
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

# Database configuration
DB_CONFIG = {
    'dbname': 'quantalgo_db',
    'user': 'postgres',
    'password': 'postgres',
    'host': 'localhost'
}

def get_db_connection():
    """Create a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {str(e)}")
        raise

def get_latest_instrument_file():
    """Get the most recent instrument file from the instruments directory"""
    try:
        instruments_dir = "instruments"
        if not os.path.exists(instruments_dir):
            logging.error(f"Instruments directory {instruments_dir} not found")
            return None
            
        # Get all CSV files in the instruments directory
        csv_files = [f for f in os.listdir(instruments_dir) if f.endswith('.csv')]
        
        if not csv_files:
            logging.error("No CSV files found in instruments directory")
            return None
            
        # Sort by filename (assuming format: YYYYMMDD_instrument_file.csv)
        csv_files.sort(reverse=True)
        latest_file = os.path.join(instruments_dir, csv_files[0])
        
        logging.info(f"Latest instrument file: {latest_file}")
        return latest_file
        
    except Exception as e:
        logging.error(f"Error finding latest instrument file: {str(e)}")
        return None

def load_instrument_file_to_db(file_path=None):
    """Load instrument file data into PostgreSQL database"""
    try:
        if not file_path:
            file_path = get_latest_instrument_file()
            
        if not file_path or not os.path.exists(file_path):
            logging.error(f"Instrument file not found: {file_path}")
            return False
            
        logging.info(f"Loading instrument file: {file_path}")
        
        # Read the CSV file with proper encoding handling
        try:
            df = pd.read_csv(file_path, low_memory=False, encoding='utf-8')
        except UnicodeDecodeError:
            try:
                df = pd.read_csv(file_path, low_memory=False, encoding='latin-1')
            except UnicodeDecodeError:
                df = pd.read_csv(file_path, low_memory=False, encoding='cp1252')
        
        logging.info(f"Loaded {len(df)} instruments from file")
        
        # Filter for MCX instruments only (MCX exchange)
        mcx_instruments = df[df['exch_seg'] == 'MCX'].copy()
        logging.info(f"Filtered to {len(mcx_instruments)} MCX instruments")
        
        # Use the filtered dataframe
        df = mcx_instruments
        
        # Connect to database
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if table exists and what columns it has
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'instruments' AND table_schema = 'public'
        """)
        existing_columns = {row[0]: row[1] for row in cur.fetchall()}
        
        if not existing_columns:
            # Table doesn't exist, create it
            logging.info("Creating new instruments table")
            cur.execute("""
                CREATE TABLE instruments (
                    token VARCHAR(20) PRIMARY KEY,
                    symbol VARCHAR(100),
                    name VARCHAR(50),
                    expiry DATE,
                    strike NUMERIC,
                    lotsize INTEGER,
                    instrumenttype VARCHAR(20),
                    exch_seg VARCHAR(10),
                    tick_size NUMERIC,
                    isin VARCHAR(20),
                    updated_date DATE DEFAULT CURRENT_DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            # Table exists, check for missing columns and add them
            logging.info(f"Found existing instruments table with columns: {list(existing_columns.keys())}")
            
            required_columns = {
                'expiry': 'DATE',
                'strike': 'DECIMAL(15,2)',
                'lotsize': 'INTEGER',
                'instrumenttype': 'VARCHAR(20)',
                'exch_seg': 'VARCHAR(10)',
                'tick_size': 'DECIMAL(10,4)',
                'updated_date': 'DATE DEFAULT CURRENT_DATE',
                'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
                'isin': 'VARCHAR(20)'
            }
            
            for col_name, col_def in required_columns.items():
                if col_name not in existing_columns:
                    try:
                        logging.info(f"Adding missing column: {col_name}")
                        cur.execute(f"ALTER TABLE instruments ADD COLUMN {col_name} {col_def}")
                    except Exception as e:
                        logging.warning(f"Could not add column {col_name}: {e}")
        
        # Clear old data for today
        try:
            cur.execute("DELETE FROM instruments WHERE updated_date = CURRENT_DATE")
            logging.info("Cleared existing data for today")
        except Exception as delete_error:
            # If updated_date column doesn't exist or other error, clear all data
            logging.warning(f"Could not delete by date: {delete_error}. Clearing all data.")
            cur.execute("DELETE FROM instruments")
        
        # Process and insert data with proper batch handling
        inserted_count = 0
        error_count = 0
        
        # Use executemany for better performance and automatic transaction handling
        batch_size = 500
        batch_data = []
        
        for idx, row in df.iterrows():
            try:
                # Handle expiry date conversion
                expiry_date = None
                if 'expiry' in row and pd.notna(row['expiry']):
                    try:
                        expiry_date = pd.to_datetime(row['expiry'], format='%d%b%Y', errors='coerce')
                        if pd.isna(expiry_date):
                            expiry_date = pd.to_datetime(row['expiry'], errors='coerce')
                    except:
                        expiry_date = None
                
                # Prepare data for insertion - handle potential None/NaN values
                # Fix strike price scaling - divide by 100 to get actual strike price
                strike_price = None
                if 'strike' in row and pd.notna(row['strike']) and str(row['strike']).strip() != '':
                    try:
                        strike_price = float(row['strike']) / 100.0  # Divide by 100 to fix scaling
                    except:
                        strike_price = None
                
                data = (
                    str(row.get('token', ''))[:20],  # Ensure it fits VARCHAR(20)
                    str(row.get('symbol', ''))[:100],  # Limit length
                    str(row.get('name', ''))[:50],
                    expiry_date if expiry_date and not pd.isna(expiry_date) else None,
                    strike_price,
                    int(row['lotsize']) if 'lotsize' in row and pd.notna(row['lotsize']) and str(row['lotsize']).strip() != '' else None,
                    str(row.get('instrumenttype', ''))[:20],
                    str(row.get('exch_seg', ''))[:10],
                    float(row['tick_size']) if 'tick_size' in row and pd.notna(row['tick_size']) and str(row['tick_size']).strip() != '' else None,
                    str(row.get('isin', ''))[:20]
                )
                
                batch_data.append(data)
                
                # Process batch when it's full or at the end
                if len(batch_data) >= batch_size or idx == len(df) - 1:
                    try:
                        # Use executemany for batch insert - more efficient
                        cur.executemany("""
                            INSERT INTO instruments (
                                token, symbol, name, expiry, strike, lotsize,
                                instrumenttype, exch_seg, tick_size, isin
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (token) DO UPDATE SET
                                symbol = EXCLUDED.symbol,
                                name = EXCLUDED.name,
                                expiry = EXCLUDED.expiry,
                                strike = EXCLUDED.strike,
                                lotsize = EXCLUDED.lotsize,
                                instrumenttype = EXCLUDED.instrumenttype,
                                exch_seg = EXCLUDED.exch_seg,
                                tick_size = EXCLUDED.tick_size,
                                isin = EXCLUDED.isin,
                                updated_date = CURRENT_DATE
                        """, batch_data)
                        
                        inserted_count += len(batch_data)
                        logging.info(f"Successfully inserted batch of {len(batch_data)} records. Total: {inserted_count}")
                        
                    except Exception as batch_error:
                        # If batch fails, try individual inserts to identify problematic rows
                        logging.warning(f"Batch insert failed: {batch_error}. Trying individual inserts...")
                        
                        for i, data_row in enumerate(batch_data):
                            try:
                                cur.execute("""
                                    INSERT INTO instruments (
                                        token, symbol, name, expiry, strike, lotsize,
                                        instrumenttype, exch_seg, tick_size, isin
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (token) DO UPDATE SET
                                        symbol = EXCLUDED.symbol,
                                        name = EXCLUDED.name,
                                        expiry = EXCLUDED.expiry,
                                        strike = EXCLUDED.strike,
                                        lotsize = EXCLUDED.lotsize,
                                        instrumenttype = EXCLUDED.instrumenttype,
                                        exch_seg = EXCLUDED.exch_seg,
                                        tick_size = EXCLUDED.tick_size,
                                        isin = EXCLUDED.isin,
                                        updated_date = CURRENT_DATE
                                """, data_row)
                                inserted_count += 1
                            except Exception as insert_error:
                                error_count += 1
                                if error_count <= 10:  # Log first 10 errors in detail
                                    logging.warning(f"Error inserting row with token {data_row[0]}: {str(insert_error)}")
                                elif error_count == 11:
                                    logging.warning(f"More than 10 insertion errors, suppressing further error logs...")
                    
                    batch_data = []  # Clear the batch
                    
                    # Commit every few batches to maintain progress
                    if inserted_count > 0 and inserted_count % (batch_size * 3) == 0:
                        try:
                            conn.commit()
                            logging.info(f"Committed progress, inserted {inserted_count} records so far")
                        except Exception as commit_error:
                            logging.warning(f"Commit error: {commit_error}")
                
            except Exception as row_error:
                error_count += 1
                if error_count <= 10:
                    logging.warning(f"Error processing row {idx}: {str(row_error)}")
                continue
        
        # Create indices for better performance (only if we have data)
        if inserted_count > 0:
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments(symbol);
                    CREATE INDEX IF NOT EXISTS idx_instruments_name ON instruments(name);
                    CREATE INDEX IF NOT EXISTS idx_instruments_expiry ON instruments(expiry);
                    CREATE INDEX IF NOT EXISTS idx_instruments_type ON instruments(instrumenttype);
                    CREATE INDEX IF NOT EXISTS idx_instruments_segment ON instruments(exch_seg);
                    CREATE INDEX IF NOT EXISTS idx_instruments_date ON instruments(updated_date);
                """)
                
                # Create view for today's active instruments
                cur.execute("""
                    CREATE OR REPLACE VIEW active_instruments AS
                    SELECT *
                    FROM instruments
                    WHERE updated_date = CURRENT_DATE
                    AND (expiry IS NULL OR expiry >= CURRENT_DATE)
                    ORDER BY name, expiry, strike;
                """)
            except Exception as index_error:
                logging.warning(f"Error creating indices or view: {index_error}")
        
        # Final commit
        try:
            conn.commit()
            logging.info(f"Successfully loaded {inserted_count} instruments into database")
            if error_count > 0:
                logging.warning(f"Encountered {error_count} errors during processing")
        except Exception as final_commit_error:
            logging.error(f"Final commit failed: {final_commit_error}")
            conn.rollback()
            return False
        
        # Log statistics
        cur.execute("SELECT COUNT(*) FROM instruments WHERE updated_date = CURRENT_DATE")
        total_today = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(DISTINCT name) FROM instruments WHERE updated_date = CURRENT_DATE")
        unique_names = cur.fetchone()[0]
        
        logging.info(f"Today's instruments: {total_today}, Unique names: {unique_names}")
        
        cur.close()
        conn.close()
        
        return True
        
    except Exception as e:
        logging.error(f"Error loading instrument file to database: {str(e)}")
        # Ensure connection is properly closed
        try:
            if 'conn' in locals() and conn:
                conn.rollback()
                conn.close()
        except:
            pass
        return False
    
    finally:
        # Always ensure connection is closed
        try:
            if 'cur' in locals() and cur:
                cur.close()
            if 'conn' in locals() and conn:
                conn.close()
        except:
            pass

def get_mcx_instruments():
    """Get MCX instruments from database"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
        SELECT * FROM instruments 
        WHERE exch_seg = 'MCX' 
        AND updated_date = CURRENT_DATE
        AND name LIKE '%CRUDE%'
        ORDER BY expiry, strike
        """
        
        cur.execute(query)
        results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return results
        
    except Exception as e:
        logging.error(f"Error getting MCX instruments: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return []

def get_instruments_by_criteria(name=None, segment=None, instrument_type=None, expiry=None):
    """Get instruments by specific criteria"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = "SELECT * FROM instruments WHERE updated_date = CURRENT_DATE"
        params = []
        
        if name:
            query += " AND name ILIKE %s"
            params.append(f"%{name}%")
            
        if segment:
            query += " AND exch_seg = %s"
            params.append(segment)
            
        if instrument_type:
            query += " AND instrumenttype = %s"
            params.append(instrument_type)
            
        if expiry:
            query += " AND expiry = %s"
            params.append(expiry)
            
        query += " ORDER BY name, expiry, strike"
        
        cur.execute(query, params)
        results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return results
        
    except Exception as e:
        logging.error(f"Error getting instruments by criteria: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return []

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Load today's instrument file
    success = load_instrument_file_to_db()
    if success:
        print("Instrument file loaded successfully!")
        
        # Test queries
        mcx_instruments = get_mcx_instruments()
        print(f"Found {len(mcx_instruments)} MCX CRUDE instruments")
        
        nifty_instruments = get_instruments_by_criteria(name="NIFTY", segment="NFO")
        print(f"Found {len(nifty_instruments)} NIFTY instruments")
        
    else:
        print("Failed to load instrument file")
