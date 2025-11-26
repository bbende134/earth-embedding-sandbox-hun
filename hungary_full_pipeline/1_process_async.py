"""
Step 1: Async Process Loop - Download, Convert, Load.
Monitors Google Drive for completed tiles, downloads them, processes them, and loads into Milvus.
Uses asyncio for concurrency to maximize throughput.
"""

import argparse
import asyncio
import glob
import io
import logging
import os
import shutil
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Configuration
DRIVE_FOLDER = "earth_engine_exports_hun"
LOCAL_DATA_DIR = "data_hun"
PROCESSED_DIR = "processed_hun"
MILVUS_COLLECTION = "high_res_hun"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Concurrency Settings
MAX_CONCURRENT_DOWNLOADS = 3
MAX_CONCURRENT_PROCESS = 2  # Beam is memory intensive, keep this low

# Logging setup
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


def authenticate():
    """Authenticate with Google Drive."""
    creds = None
    try:
        import google.auth

        creds, _project_id = google.auth.default(scopes=SCOPES)
        if creds and hasattr(creds, "service_account_email"):
            logger.info(f"Using Service Account: {creds.service_account_email}")
            return creds
        elif creds:
            logger.info("Using Application Default Credentials")
            return creds
    except Exception as e:
        logger.warning(f"Service Account auth failed/not found: {e}")

    logger.info("Falling back to interactive OAuth...")
    if os.path.exists("token.json"):
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        except ValueError:
            logger.warning("token.json is invalid (missing fields). Re-authenticating...")
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                logger.error("credentials.json not found!")
                return None
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=8080)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def get_drive_service(creds=None):
    if not creds:
        creds = authenticate()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find_folder(service, name):
    results = (
        service.files()
        .list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)",
        )
        .execute()
    )
    items = results.get("files", [])
    return items[0]["id"] if items else None


def list_files(service, folder_id):
    results = (
        service.files()
        .list(q=f"'{folder_id}' in parents and trashed=false", fields="files(id, name, size)")
        .execute()
    )
    return results.get("files", [])


async def download_file(service, file_id, filepath, retries=3):
    """Async wrapper for blocking download with retries."""
    loop = asyncio.get_running_loop()

    def _download():
        last_error = None
        for attempt in range(retries):
            try:
                request = service.files().get_media(fileId=file_id)
                with io.FileIO(filepath, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _status, done = downloader.next_chunk()
                return filepath
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Download attempt {attempt + 1}/{retries} failed for {filepath}: {e}"
                )
                time.sleep(2 * (attempt + 1))  # Exponential backoff

        raise last_error

    await loop.run_in_executor(None, _download)
    logger.info(f"Downloaded: {filepath}")


async def delete_file(service, file_id):
    """Async wrapper for blocking delete."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: service.files().delete(fileId=file_id).execute())
    logger.info(f"Deleted from Drive: {file_id}")


async def run_command(cmd, description):
    """Run a shell command asynchronously."""
    logger.info(f"Starting: {description}")
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(f"Command failed: {description}\nStderr: {stderr.decode()}")
        raise Exception(f"Command failed with return code {process.returncode}")

    logger.info(f"Completed: {description}")


async def process_tile(tile_path):
    """Run the processing pipeline for a single tile."""
    tile_name = os.path.basename(tile_path).replace(".tif", "")
    zarr_raw = os.path.join(PROCESSED_DIR, f"{tile_name}_raw.zarr")
    zarr_reduced = os.path.join(PROCESSED_DIR, f"{tile_name}_reduced.zarr")

    logger.info(f"--- Processing {tile_name} ---")

    try:
        # 1. Convert to Zarr
        await run_command(
            [
                "uv",
                "run",
                "python",
                "local_pipeline/1_convert_to_zarr.py",
                "--input",
                tile_path,
                "--output",
                zarr_raw,
            ],
            f"Convert {tile_name}",
        )

        # 2. Consolidate
        await run_command(
            [
                "uv",
                "run",
                "python",
                "pipeline/1_consolidate.py",
                "--raw_archive",
                zarr_raw,
                "--reduced_archive",
                zarr_reduced,
            ],
            f"Consolidate {tile_name}",
        )

        # 3. Reduce
        try:
            await run_command(
                ["uv", "run", "python", "pipeline/2_reduce.py", "--reduced_archive", zarr_reduced],
                f"Reduce {tile_name}",
            )
        except Exception:
            logger.warning(
                f"Reduce step failed for {tile_name} (possibly small tile). Continuing..."
            )

        # 4. Load into Milvus
        await run_command(
            [
                "uv",
                "run",
                "python",
                "hungary_full_pipeline/2_load_generic.py",
                "--input",
                f"{zarr_reduced}_z8",
                "--collection",
                MILVUS_COLLECTION,
            ],
            f"Load {tile_name}",
        )

        # 5. Cleanup
        shutil.rmtree(zarr_raw, ignore_errors=True)
        for p in glob.glob(f"{zarr_reduced}*"):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        os.remove(tile_path)
        logger.info(f"✓ {tile_name} processed and cleaned up.")
        return True

    except Exception as e:
        logger.error(f"Failed to process {tile_name}: {e}")
        # Cleanup on failure to save space, or keep for debugging?
        # For now, let's keep raw files if it fails for debugging
        return False


async def download_worker(creds, download_queue, process_queue):
    # Create a dedicated service instance for this thread/worker to avoid SSL/socket issues
    service = get_drive_service(creds)
    while True:
        file_info = await download_queue.get()
        try:
            # Use ID in filename to avoid collisions with duplicates
            safe_name = f"{file_info['name'].replace('.tif', '')}_{file_info['id']}.tif"
            local_path = os.path.join(LOCAL_DATA_DIR, safe_name)

            await download_file(service, file_info["id"], local_path)
            await process_queue.put((local_path, file_info["id"]))
        except Exception as e:
            logger.error(f"Download error for {file_info['name']}: {e}")
        finally:
            download_queue.task_done()


async def process_worker(creds, process_queue):
    # Create a dedicated service instance for this thread/worker
    service = get_drive_service(creds)
    while True:
        local_path, file_id = await process_queue.get()
        try:
            success = await process_tile(local_path)
            if success:
                await delete_file(service, file_id)
        except Exception as e:
            logger.error(f"Process worker error: {e}")
        finally:
            process_queue.task_done()


async def main_async():
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-shot", action="store_true", help="Process one batch and exit")
    args = parser.parse_args()

    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    try:
        creds = authenticate()
        # Main loop service for listing files
        service = get_drive_service(creds)

        # Wait for folder to appear
        folder_id = None
        while not folder_id:
            try:
                folder_id = find_folder(service, DRIVE_FOLDER)
            except Exception as e:
                logger.warning(f"Error checking for folder: {e}")

            if not folder_id:
                logger.info(
                    f"Folder '{DRIVE_FOLDER}' not found. Waiting 30s for export tasks to create it..."
                )
                await asyncio.sleep(30)

        logger.info(f"Monitoring folder '{DRIVE_FOLDER}' (ID: {folder_id})...")

        download_queue = asyncio.Queue()
        process_queue = asyncio.Queue()

        # Start workers
        download_tasks = [
            asyncio.create_task(download_worker(creds, download_queue, process_queue))
            for _ in range(MAX_CONCURRENT_DOWNLOADS)
        ]

        process_tasks = [
            asyncio.create_task(process_worker(creds, process_queue))
            for _ in range(MAX_CONCURRENT_PROCESS)
        ]

        processed_files = set()

        while True:
            files = list_files(service, folder_id)

            new_files = [
                f for f in files if f["name"].endswith(".tif") and f["id"] not in processed_files
            ]

            if not new_files:
                if args.one_shot and download_queue.empty() and process_queue.empty():
                    logger.info("One-shot mode: No more files. Exiting.")
                    break
                logger.info("No new files. Waiting 60s...")
                await asyncio.sleep(60)
                continue

            # Check for duplicate names in the new batch
            seen_names = {}
            for f in new_files:
                if f["name"] in seen_names:
                    logger.warning(
                        f"Duplicate filename detected: {f['name']} (IDs: {seen_names[f['name']]} and {f['id']})"
                    )
                seen_names[f["name"]] = f["id"]

            for f in new_files:
                logger.info(f"Queuing {f['name']} ({int(f.get('size', 0)) / 1024 / 1024:.1f} MB)")
                processed_files.add(f["id"])
                await download_queue.put(f)

            if args.one_shot:
                # Wait for queues to drain then exit
                await download_queue.join()
                await process_queue.join()
                break

            await asyncio.sleep(10)

    except asyncio.CancelledError:
        logger.info("Shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error in main loop: {e}", exc_info=True)
        raise
    finally:
        # Cancel workers if they exist
        if "download_tasks" in locals():
            for t in download_tasks + process_tasks:
                t.cancel()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
