"""
Step 1: Process Loop - Download, Convert, Load.
Monitors Google Drive for completed tiles, downloads them, processes them, and loads into Milvus.
"""

import argparse
import glob
import io
import os
import shutil
import subprocess
import sys
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
SCOPES = ["https://www.googleapis.com/auth/drive"]  # Read/Write to delete files

# Load environment variables from .env
from dotenv import load_dotenv

load_dotenv()


def authenticate(force_interactive=False):
    """
    Authenticate with Google Drive.
    Prioritizes Service Account (GOOGLE_APPLICATION_CREDENTIALS) if available.
    Falls back to interactive OAuth 2.0 flow.
    """
    creds = None

    # 1. Try Service Account (GOOGLE_APPLICATION_CREDENTIALS)
    if not force_interactive:
        try:
            import google.auth

            creds, project_id = google.auth.default(scopes=SCOPES)
            # Check if we actually got valid credentials (sometimes default() returns anonymous if not found)
            if creds and hasattr(creds, "service_account_email"):
                print(f"Using Service Account: {creds.service_account_email}")
                return creds
            elif creds:
                # Could be other default creds (gcloud), which is also fine
                print("Using Application Default Credentials")
                return creds
        except Exception as e:
            print(f"Service Account auth failed/not found: {e}")

    # 2. Fallback to Interactive OAuth (token.json / credentials.json)
    if force_interactive:
        print("Forcing interactive OAuth...")
    else:
        print("Falling back to interactive OAuth...")

    if os.path.exists("local_pipeline/token.json"):
        try:
            creds = Credentials.from_authorized_user_file("local_pipeline/token.json", SCOPES)
        except ValueError:
            print("Warning: local_pipeline/token.json is corrupt. Ignoring...")
            creds = None
    elif os.path.exists("token.json"):  # Check current dir too
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        except ValueError:
            print("Warning: token.json is corrupt. Ignoring...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cred_path = "local_pipeline/credentials.json"
            if not os.path.exists(cred_path):
                cred_path = "credentials.json"

            if not os.path.exists(cred_path):
                print("ERROR: No authentication method found!")
                print("1. Set GOOGLE_APPLICATION_CREDENTIALS environment variable (Recommended)")
                print(f"2. OR place 'credentials.json' in {os.getcwd()}")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            # Use local server flow (standard for desktop apps)
            # Request offline access to get a refresh token
            creds = flow.run_local_server(port=8080, access_type='offline', prompt='consent')

        # Save token for next time (only for interactive auth)
        with open("local_pipeline/token.json", "w") as token:
            token.write(creds.to_json())

    return creds


def get_drive_service():
    creds = authenticate(force_interactive=True)
    return build("drive", "v3", credentials=creds)


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


def download_file(service, file_id, filepath):
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(filepath, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Download progress: {int(status.progress() * 100)}%", end='\r')
    print(f"Downloaded: {filepath}")


def delete_file(service, file_id):
    service.files().delete(fileId=file_id).execute()
    print(f"Deleted from Drive: {file_id}")


def process_tile(tile_path):
    """Run the processing pipeline for a single tile."""
    tile_name = os.path.basename(tile_path).replace(".tif", "")
    
    # Check if this is a large tile that needs splitting (>500MB)
    file_size_mb = os.path.getsize(tile_path) / (1024 * 1024)
    if file_size_mb > 500:
        print(f"\n--- Large tile detected ({file_size_mb:.1f}MB): {tile_name} ---")
        print("Splitting into quarters to avoid Beam gRPC limit...")
        
        # Split the TIF into 4 quarters
        cmd = [
            "uv",
            "run",
            "python",
            "hungary_full_pipeline/split_large_tif.py",
            "--input",
            tile_path,
            "--output-dir",
            LOCAL_DATA_DIR,
        ]
        subprocess.run(cmd, check=True)
        
        # Process each quarter
        base_name = os.path.basename(tile_path).replace(".tif", "")
        for quadrant in ["NW", "NE", "SW", "SE"]:
            quarter_path = os.path.join(LOCAL_DATA_DIR, f"{base_name}_{quadrant}.tif")
            if os.path.exists(quarter_path):
                print(f"\nProcessing quarter: {quadrant}")
                process_single_tile(quarter_path)
        
        # Delete the original large TIF
        os.remove(tile_path)
        print(f"✓ {tile_name} (large tile) split and processed")
        return
    
    # Process normal-sized tile
    process_single_tile(tile_path)


def process_single_tile(tile_path):
    """Run the processing pipeline for a single tile (normal or quarter)."""
    tile_name = os.path.basename(tile_path).replace(".tif", "")
    zarr_raw = os.path.join(PROCESSED_DIR, f"{tile_name}_raw.zarr")
    zarr_reduced = os.path.join(PROCESSED_DIR, f"{tile_name}_reduced.zarr")

    print(f"\n--- Processing {tile_name} ---")

    # 1. Convert to Zarr (float32)
    print("Converting to Zarr...")
    cmd = [
        "uv",
        "run",
        "python",
        "local_pipeline/1_convert_to_zarr.py",
        "--input",
        tile_path,
        "--output",
        zarr_raw,
    ]
    if os.path.exists(zarr_raw):
        print(f"  {zarr_raw} exists. Skipping conversion.")
    else:
        subprocess.run(cmd, check=True)

    # 2. Consolidate
    print("Consolidating...")
    cmd = [
        "uv",
        "run",
        "python",
        "pipeline/1_consolidate.py",
        "--raw_archive",
        zarr_raw,
        "--reduced_archive",
        zarr_reduced,
    ]
    if os.path.exists(zarr_reduced) or glob.glob(f"{zarr_reduced}*"):
        print(f"  {zarr_reduced} exists. Skipping consolidation.")
    else:
        # Retry consolidation up to 3 times (it can fail due to resource exhaustion)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                subprocess.run(cmd, check=True)
                break  # Success
            except subprocess.CalledProcessError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"Consolidation failed (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Consolidation failed after {max_retries} attempts. Skipping this tile.")
                    raise  # Re-raise to be caught by outer exception handler

    # 3. Reduce (Pyramid)
    print("Reducing...")
    cmd = ["uv", "run", "python", "pipeline/2_reduce.py", "--reduced_archive", zarr_reduced]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("Warning: Reduce step failed (possibly due to small tile size). Continuing...")

    # 4. Load into Milvus
    print("Loading into Milvus...")
    # Load the base level (_z8) which should always exist
    cmd = [
        "uv",
        "run",
        "python",
        "hungary_full_pipeline/2_load_generic.py",
        "--input",
        f"{zarr_reduced}_z8",
        "--collection",
        MILVUS_COLLECTION,
    ]
    subprocess.run(cmd, check=True)

    # 5. Cleanup local files
    print("Cleaning up local files...")
    shutil.rmtree(zarr_raw, ignore_errors=True)
    # Cleanup all generated zarr levels
    for p in glob.glob(f"{zarr_reduced}*"):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
    os.remove(tile_path)
    print(f"✓ {tile_name} processed and cleaned up.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-shot", action="store_true", help="Process one file and exit")
    args = parser.parse_args()

    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    service = get_drive_service()
    folder_id = find_folder(service, DRIVE_FOLDER)

    if not folder_id:
        print(f"Waiting for folder '{DRIVE_FOLDER}' to be created by export task...")
        return

    print(f"Monitoring folder '{DRIVE_FOLDER}' (ID: {folder_id})...")
    if args.one_shot:
        print("Mode: ONE-SHOT (will exit after processing one file)")
    else:
        print("Mode: CONTINUOUS (Press Ctrl+C to stop)")

    processed_count = 0

    while True:
        try:
            files = list_files(service, folder_id)

            if not files:
                print("No files found. Waiting 60s...", end="\r")
                time.sleep(60)
                continue

            print(f"Files in folder: {[f['name'] for f in files]}")

            for f in files:
                if not f["name"].endswith(".tif"):
                    continue

                print(
                    f"\nFound new file: {f['name']} ({int(f.get('size', 0)) / 1024 / 1024:.1f} MB)"
                )

                local_path = os.path.join(LOCAL_DATA_DIR, f["name"])

                # Check if already processed (resume capability)
                tile_name = f['name'].replace('.tif', '')
                zarr_raw = os.path.join(PROCESSED_DIR, f"{tile_name}_raw.zarr")
                zarr_reduced = os.path.join(PROCESSED_DIR, f"{tile_name}_reduced.zarr")
                
                # If reduced Zarr exists, skip download and processing, go straight to load/cleanup
                if os.path.exists(zarr_reduced) or glob.glob(f"{zarr_reduced}*"):
                    print(f"Found existing processed data for {tile_name}. Resuming...")
                    local_path = os.path.join(LOCAL_DATA_DIR, f['name']) # Path needed for cleanup
                else:
                    # Download only if local file doesn't exist or is partial
                    if not os.path.exists(local_path):
                        download_file(service, f["id"], local_path)
                        # Verify download succeeded
                        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
                            print(f"ERROR: Download failed for {local_path} (File missing or empty).")
                            if os.path.exists(local_path):
                                os.remove(local_path)
                            continue
                    else:
                        print(f"File {local_path} already exists. Skipping download.")

                # Process
                try:
                    process_tile(local_path)

                    # Delete from Drive only if processing succeeded
                    delete_file(service, f["id"])

                except Exception as e:
                    print(f"ERROR processing {f['name']}: {e}")
                    
                    error_str = str(e)
                    
                    # Only delete the TIF if it's a conversion error (corrupt file)
                    # Don't delete for Beam timeout/resource errors (those are processing issues, not file issues)
                    is_conversion_error = "1_convert_to_zarr.py" in error_str
                    is_beam_timeout = "DEADLINE_EXCEEDED" in error_str or "RESOURCE_EXHAUSTED" in error_str
                    
                    if "returned non-zero exit status" in error_str and is_conversion_error and not is_beam_timeout:
                        if os.path.exists(local_path):
                            print(f"Deleting potentially corrupt file: {local_path}")
                            os.remove(local_path)
                            # Also clean up partial Zarr if it exists
                            if os.path.exists(zarr_raw):
                                shutil.rmtree(zarr_raw, ignore_errors=True)
                    elif is_beam_timeout:
                        print(f"Beam timeout/resource error - keeping file for manual retry or debugging")
                        # Clean up partial outputs but keep the source TIF
                        if os.path.exists(zarr_raw):
                            shutil.rmtree(zarr_raw, ignore_errors=True)
                        # Clean up any partial reduced zarr
                        for p in glob.glob(f"{zarr_reduced}*"):
                            if os.path.isdir(p):
                                shutil.rmtree(p, ignore_errors=True)

                    # Keep file in Drive to retry later or debug
                    if args.one_shot:
                        print("One-shot mode failed.")
                        sys.exit(1)

                if args.one_shot:
                    print("One-shot mode complete. Exiting.")
                    return

            time.sleep(10)

        except Exception as e:
            print(f"Loop error: {e}")
            # Check for RefreshError or invalid grant
            error_str = str(e)
            if "RefreshError" in error_str or "invalid_grant" in error_str or "necessary fields" in error_str:
                print("Authentication token expired or invalid. Re-authenticating...")
                try:
                    # Delete invalid token
                    if os.path.exists('local_pipeline/token.json'):
                        os.remove('local_pipeline/token.json')
                    if os.path.exists('token.json'):
                        os.remove('token.json')
                    
                    # Force re-auth
                    service = get_drive_service()
                    print("Re-authentication successful.")
                except Exception as auth_e:
                    print(f"Re-authentication failed: {auth_e}")
            
            time.sleep(60)


if __name__ == "__main__":
    main()
