import psycopg2
from psycopg2.extras import RealDictCursor

# Database configuration
DB_CONFIG = {
    'dbname': 'quantalgo_db',
    'user': 'postgres',
    'password': 'postgres',
    'host': 'localhost'
}

def check_instruments():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if instruments table exists
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'instruments'
        """)
        table_exists = cur.fetchone()
        
        if table_exists:
            print("Instruments table exists!")
            
            # Check crude oil instruments with correct strike prices
            cur.execute("""
                SELECT symbol, expiry, strike, lotsize, instrumenttype 
                FROM instruments 
                WHERE name = 'CRUDEOIL' AND instrumenttype = 'OPTFUT' 
                ORDER BY expiry, strike 
                LIMIT 10
            """)
            
            instruments = cur.fetchall()
            print(f"\nSample CRUDE OIL Options (first 10):")
            for inst in instruments:
                print(f"  {inst['symbol']} - Expiry: {inst['expiry']}, Strike: {inst['strike']}, Type: {inst['instrumenttype']}")
                
            # Count total instruments
            cur.execute("SELECT COUNT(*) as total FROM instruments")
            total = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as crude_count FROM instruments WHERE name = 'CRUDEOIL'")
            crude_count = cur.fetchone()['crude_count']
            
            print(f"\nTotal instruments loaded: {total}")
            print(f"Crude oil instruments: {crude_count}")
            
        else:
            print("Instruments table does not exist!")
            
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_instruments()
