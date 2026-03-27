"""
Step 1: Download Multi-Modal tiles from Google Drive.
Monitors the Drive folder for completed GeoTIFF exports and downloads them locally.
Processing is handled by a separate script (to be wired in later).
"""

import argparse
import io
import os
import re
import sys
import time

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

DRIVE_FOLDER = "ee_multimodal_exports"
LOCAL_DATA_DIR = "data_multimodal"
SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate(force_interactive=False):
    creds = None

    if not force_interactive:
        try:
            import google.auth

            creds, _project_id = google.auth.default(scopes=SCOPES)
            if creds and hasattr(creds, "service_account_email"):
                print(f"Using Service Account: {creds.service_account_email}")
                return creds
            elif creds:
                print("Using Application Default Credentials")
                return creds
        except Exception as e:
            print(f"Service Account auth failed/not found: {e}")

    if force_interactive:
        print("Forcing interactive OAuth...")
    else:
        print("Falling back to interactive OAuth...")

    for token_path in ["local_pipeline/token.json", "token.json"]:
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)
                break
            except ValueError:
                print(f"Warning: {token_path} is corrupt. Ignoring...")
                creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Warning: Token refresh failed ({e}). Forcing re-authentication...")
                creds = None

        if not creds or not creds.valid:
            cred_path = "local_pipeline/credentials.json"
            if not os.path.exists(cred_path):
                cred_path = "credentials.json"
            if not os.path.exists(cred_path):
                print("ERROR: No authentication method found!")
                print("1. Set GOOGLE_APPLICATION_CREDENTIALS env var (recommended)")
                print(f"2. OR place 'credentials.json' in {os.getcwd()}")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

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
            print(f"Download progress: {int(status.progress() * 100)}%", end="\r")
    print(f"\nDownloaded: {filepath}")


def process_tile(tile_path):
    """Run TerraMind inference and load into Milvus. Date is parsed from filename."""
    import subprocess

    cmd = [
        "uv",
        "run",
        "python",
        "hungary_full_pipeline/2_process_multimodal.py",
        "--input",
        tile_path,
    ]
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-shot", action="store_true", help="Download one file and exit")
    parser.add_argument(
        "--no-delete", action="store_true", help="Keep files in Drive after download"
    )
    parser.add_argument("--download-only", action="store_true", help="Download without processing")
    args = parser.parse_args()

    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)

    service = get_drive_service()
    folder_id = None
    while not folder_id:
        folder_id = find_folder(service, DRIVE_FOLDER)
        if not folder_id:
            print(f"Folder '{DRIVE_FOLDER}' not in Drive yet. Waiting 60s...", end="\r")
            time.sleep(60)

    print(f"Monitoring Drive folder '{DRIVE_FOLDER}' (ID: {folder_id})...")
    if args.one_shot:
        print("Mode: ONE-SHOT (exits after first file)")
    else:
        print("Mode: CONTINUOUS (Ctrl+C to stop)")

    while True:
        try:
            files = list_files(service, folder_id)

            if not files:
                print("No files found. Waiting 60s...", end="\r")
                time.sleep(60)
                continue

            tif_files = [f for f in files if f["name"].endswith(".tif")]
            if not tif_files:
                print("No .tif files yet. Waiting 60s...", end="\r")
                time.sleep(60)
                continue

            valid_files = [
                f
                for f in tif_files
                if re.match(r"^(s2l2a|s1grd)_\d{4}-\d{2}-\d{2}_tile_\d+\.tif$", f["name"])
            ]
            stale_files = [f for f in tif_files if f not in valid_files]
            for f in stale_files:
                print(f"Skipping old-format file: {f['name']} — deleting from Drive.")
                service.files().delete(fileId=f["id"]).execute()
            tif_files = valid_files

            if not tif_files:
                print("No valid files yet. Waiting 60s...", end="\r")
                time.sleep(60)
                continue

            print(f"Files available: {[f['name'] for f in tif_files]}")

            for f in tif_files:
                local_path = os.path.join(LOCAL_DATA_DIR, f["name"])
                size_mb = int(f.get("size", 0)) / 1024 / 1024

                print(f"\nFound: {f['name']} ({size_mb:.1f} MB)")

                if os.path.exists(local_path):
                    actual_size = os.path.getsize(local_path)
                    expected_size = int(f.get("size", 0))
                    if actual_size == expected_size:
                        print(f"Already downloaded: {local_path}. Proceeding to process + delete.")
                    else:
                        msg = (
                            f"Partial download ({actual_size} vs {expected_size} bytes). "
                            "Re-downloading..."
                        )
                        print(msg)
                        os.remove(local_path)
                        download_file(service, f["id"], local_path)
                else:
                    download_file(service, f["id"], local_path)

                if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
                    print(f"ERROR: Download failed or empty for {local_path}. Skipping.")
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    if args.one_shot:
                        sys.exit(1)
                    continue

                if not args.download_only:
                    try:
                        process_tile(local_path)
                    except Exception as e:
                        print(f"ERROR processing {f['name']}: {e}")
                        if args.one_shot:
                            sys.exit(1)
                        continue

                if not args.no_delete:
                    service.files().delete(fileId=f["id"]).execute()
                    print(f"Deleted from Drive: {f['name']}")

                os.remove(local_path)
                print(f"Cleaned up local file: {local_path}")

                if args.one_shot:
                    print("One-shot complete. Exiting.")
                    return

            time.sleep(10)

        except Exception as e:
            print(f"Loop error: {e}")
            error_str = str(e)
            if any(k in error_str for k in ("RefreshError", "invalid_grant", "necessary fields")):
                print("Auth token expired. Re-authenticating...")
                try:
                    for p in ["local_pipeline/token.json", "token.json"]:
                        if os.path.exists(p):
                            os.remove(p)
                    service = get_drive_service()
                    print("Re-authentication successful.")
                except Exception as auth_e:
                    print(f"Re-authentication failed: {auth_e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
