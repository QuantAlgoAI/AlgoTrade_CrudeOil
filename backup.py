import os
import zipfile
import time
import asyncio
import aiofiles
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import schedule
from dotenv import load_dotenv

load_dotenv()

SOURCE_DIR = os.getenv("SOURCE_DIR", os.path.dirname(os.path.abspath(__file__)))
BACKUP_BASE_DIR = os.getenv("BACKUP_BASE_DIR", r"D:\Backup\CrudeOil_NSSM_New_003_rejuvenate")

EXCLUDED_EXTENSIONS = {'.pyc', '.log'}
EXCLUDED_DIRS = {'__pycache__', '.git', '.venv', 'venv'}

def should_exclude(file_path):
    if any(part in EXCLUDED_DIRS for part in file_path.split(os.sep)):
        return True
    _, ext = os.path.splitext(file_path)
    return ext in EXCLUDED_EXTENSIONS

def collect_files():
    files = []
    for foldername, _, filenames in os.walk(SOURCE_DIR):
        for filename in filenames:
            file_path = os.path.join(foldername, filename)
            if not should_exclude(file_path):
                arcname = os.path.relpath(file_path, SOURCE_DIR)
                files.append((file_path, arcname))
    return files

async def zip_file(zip_file, file_path, arcname, loop, executor, lock):
    try:
        async with aiofiles.open(file_path, 'rb') as f:
            data = await f.read()
        async with lock:
            await loop.run_in_executor(executor, zip_file.writestr, arcname, data)
    except Exception as e:
        print(f"Failed to zip {file_path}: {e}")

async def backup_project_async():
    start_time = time.time()
    timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
    backup_zip_path = os.path.join(BACKUP_BASE_DIR, f"backup_{timestamp}.zip")

    os.makedirs(BACKUP_BASE_DIR, exist_ok=True)
    print(f"Creating backup: {backup_zip_path}")

    files_to_backup = collect_files()
    loop = asyncio.get_event_loop()
    zip_write_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(100)

    async def limited_zip(file_path, arcname):
        async with semaphore:
            await zip_file(zipf, file_path, arcname, loop, executor, zip_write_lock)

    with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks = [
                limited_zip(file_path, arcname)
                for file_path, arcname in tqdm(files_to_backup, desc="Zipping", unit="file")
            ]
            await asyncio.gather(*tasks)

    elapsed = time.time() - start_time
    print(f"[✓] Backup completed in {elapsed:.2f} seconds at {time.strftime('%Y-%m-%d %H:%M:%S')}")

def backup_project():
    asyncio.run(backup_project_async())

def countdown_timer(minutes):
    for remaining in range(minutes * 60, 0, -1):
        mins, secs = divmod(remaining, 60)
        print(f"Next backup in {mins:02d}:{secs:02d}", end="\r")
        time.sleep(1)

def start_backup_now():
    user_input = input("\nDo you want to start a backup now? (yes/no): ").strip().lower()
    if user_input in ['yes', 'y']:
        backup_project()

if not os.path.exists(SOURCE_DIR):
    print(f"Waiting for source directory '{SOURCE_DIR}' to be created...")
    while not os.path.exists(SOURCE_DIR):
        time.sleep(5)

schedule.every(30).minutes.do(backup_project)
print("[INFO] Async Backup system running. Press Ctrl+C to stop.")

backup_project()

try:
    while True:
        schedule.run_pending()
        countdown_timer(30)
        start_backup_now()
except KeyboardInterrupt:
    print("\n[INFO] Backup process interrupted by user.")
