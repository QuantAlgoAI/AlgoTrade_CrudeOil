import asyncio
import logging
from pathlib import Path
from tqdm.asyncio import tqdm_asyncio

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Define script paths
BASE_DIR = Path(r"D:\Backup\CrudeOil005-NSSM\backup_2025-06-29_11-00-45\backtest")
scripts = [
    BASE_DIR / "crude_data_collector.py",
    BASE_DIR / "prepare_historical_data.py",
    BASE_DIR / "backtest.py"
]

async def run_script_live(script_path):
    logging.info(f"▶️ Starting {script_path.name}")
    process = await asyncio.create_subprocess_exec(
        "python", str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    # Read stdout live line by line
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        print(line.decode().rstrip())

    return_code = await process.wait()
    if return_code == 0:
        logging.info(f"✅ Completed {script_path.name}")
    else:
        logging.error(f"❌ Failed {script_path.name} (exit code {return_code})")
    return return_code == 0

async def run_pipeline():
    logging.info("🔁 Running pipeline...")
    for i, script in enumerate(scripts):
        success = await run_script_live(script)
        if not success:
            logging.error(f"🛑 Aborting pipeline after {script.name}")
            break

if __name__ == "__main__":
    asyncio.run(run_pipeline())
