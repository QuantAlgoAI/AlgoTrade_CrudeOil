import pandas as pd
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger()

def combine_option_data():
    """Combine individual option data files into CE and PE historical data files"""
    # Get the current backtest directory
    backtest_dir = Path(__file__).parent
    data_dir = backtest_dir / 'data'
    
    if not data_dir.exists():
        logger.error(f"Data directory not found at: {data_dir}")
        return False
        
    # Lists to store CE and PE data
    ce_data = []
    pe_data = []
    
    # Process all CSV files in data directory
    for file in data_dir.glob('*.csv'):
        # Skip placeholder lists of symbols that failed to download
        if file.name.startswith('failed_'):
            logger.debug(f"Skipping placeholder file {file.name}")
            continue
        try:
            # Read the data file
            df = pd.read_csv(file)

            # Ensure the mandatory timestamp column exists
            if 'timestamp' not in df.columns:
                logger.warning(f"Skip {file.name}: no timestamp column")
                continue

            # Robust timestamp parsing: first try explicit formats then fall back
            ts_series = pd.to_datetime(df['timestamp'], format='%Y-%m-%d %H:%M:%S', errors='coerce')
            if ts_series.isna().all():
                ts_series = pd.to_datetime(df['timestamp'], format='%Y-%m-%d %H:%M', errors='coerce')
            if ts_series.isna().all():
                ts_series = pd.to_datetime(df['timestamp'], errors='coerce')  # last-resort generic
            df['timestamp'] = ts_series

            df = df[df['timestamp'].notna()]
            if df.empty:
                logger.warning(f"Skip {file.name}: empty after timestamp parse")
                continue

            # Check if it's CE or PE
            if 'CE' in file.name:
                ce_data.append(df)
            elif 'PE' in file.name:
                pe_data.append(df)

            logger.info(f"Processed {file.name}")

        except Exception as e:
            logger.error(f"Error processing {file.name}: {str(e)}")
            continue
    
    # Save files in the backtest directory
    backtest_dir = Path(__file__).parent
    
    # Combine and save CE data
    if ce_data:
        ce_combined = pd.concat(ce_data, ignore_index=True)
        ce_combined = ce_combined.sort_values('timestamp')
        ce_combined.to_csv(backtest_dir / 'historical_data_ce.csv', index=False)
        logger.info(f"Saved historical_data_ce.csv with {len(ce_combined)} records")
    else:
        logger.warning("No CE data found!")
        
    # Combine and save PE data
    if pe_data:
        pe_combined = pd.concat(pe_data, ignore_index=True)
        pe_combined = pe_combined.sort_values('timestamp')
        pe_combined.to_csv(backtest_dir / 'historical_data_pe.csv', index=False)
        logger.info(f"Saved historical_data_pe.csv with {len(pe_combined)} records")
    else:
        logger.warning("No PE data found!")
        
    return bool(ce_data and pe_data)

if __name__ == "__main__":
    if combine_option_data():
        logger.info("Successfully prepared historical data files")
    else:
        logger.error("Failed to prepare historical data files") 