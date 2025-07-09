import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import logging
from datetime import datetime

# Database configuration
DB_CONFIG = {
    'dbname': 'quantalgo_db',
    'user': 'postgres',
    'password': 'postgres',
    'host': 'localhost'
}

def setup_logging():
    """Set up logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def create_database():
    """Create the database if it doesn't exist"""
    try:
        # Connect to default database to create new database
        conn = psycopg2.connect(
            dbname='postgres',
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            host=DB_CONFIG['host']
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        # Check if database exists
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_CONFIG['dbname'],))
        if not cur.fetchone():
            cur.execute(f"CREATE DATABASE {DB_CONFIG['dbname']}")
            logging.info(f"Database {DB_CONFIG['dbname']} created successfully")
        else:
            logging.info(f"Database {DB_CONFIG['dbname']} already exists")
            
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating database: {str(e)}")
        raise

def create_tables():
    """Create all necessary tables - skip if they already exist"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Check if tables already exist - if so, skip creation
        cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name IN ('user', 'strategy', 'trade')
        """)
        existing_tables = [row[0] for row in cur.fetchall()]
        
        if len(existing_tables) >= 3:
            logging.info("Tables already exist - skipping table creation")
            logging.info(f"Found existing tables: {existing_tables}")
        else:
            # Only create tables if they don't exist (fallback for fresh installs)
            logging.info("Creating missing tables...")
            
            # Create user table (simplified for this project)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS "user" (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                api_key VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                is_active BOOLEAN DEFAULT true
            );
            """)
            
            # Create strategy table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS strategy (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                instrument VARCHAR(50) NOT NULL,
                timeframe VARCHAR(20),
                entry_condition TEXT NOT NULL,
                exit_condition TEXT NOT NULL,
                position_size DOUBLE PRECISION,
                stop_loss DOUBLE PRECISION,
                take_profit DOUBLE PRECISION,
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES "user" (id),
                UNIQUE(name)
            );
            """)
            
            # Create trade table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS trade (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(50) NOT NULL,
                strike DOUBLE PRECISION,
                option_type VARCHAR(10),
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION,
                quantity INTEGER NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                exit_time TIMESTAMP,
                pnl DOUBLE PRECISION,
                status VARCHAR(20),
                exit_reason VARCHAR(100),
                trade_type VARCHAR(20),
                user_id INTEGER NOT NULL,
                strategy_id INTEGER,
                direction VARCHAR(10),
                stop_loss DOUBLE PRECISION,
                target_price DOUBLE PRECISION,
                FOREIGN KEY (user_id) REFERENCES "user" (id),
                FOREIGN KEY (strategy_id) REFERENCES strategy (id)
            );
            """)
        
        conn.commit()
        logging.info("Table setup completed successfully")
        
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating tables: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        raise

def insert_initial_data():
    """Insert initial data into the tables - adapted for existing schema"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Insert default user using existing schema (no email column)
        cur.execute("""
        INSERT INTO "user" (username, api_key)
        VALUES ('admin', 'default_api_key')
        ON CONFLICT (username) DO UPDATE 
        SET api_key = EXCLUDED.api_key
        RETURNING id;
        """)
        user_id = cur.fetchone()[0]
        
        # Insert default strategy using existing schema
        cur.execute("""
        INSERT INTO strategy (
            name, description, instrument, timeframe,
            entry_condition, exit_condition, position_size,
            stop_loss, take_profit, is_active, user_id
        ) VALUES (
            'HighWinRate', 
            'High probability options trading strategy for MCX Crude Oil',
            'CRUDEOIL',
            '1s',
            'EMA Crossover with RSI, Volume, OI confirmation',
            'Stop loss or target hit or EMA reversal',
            50,
            -1000,
            2000,
            true,
            %s
        ) ON CONFLICT (name) DO UPDATE 
        SET description = EXCLUDED.description,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id;
        """, (user_id,))
        
        conn.commit()
        logging.info("Initial data inserted successfully with existing schema")
        
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error inserting initial data: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        raise

def main():
    """Main function to run all database setup steps"""
    setup_logging()
    logging.info("Starting database setup...")
    
    try:
        create_database()
        create_tables()
        insert_initial_data()
        logging.info("Database setup completed successfully")
    except Exception as e:
        logging.error(f"Database setup failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    import sys
    main() 