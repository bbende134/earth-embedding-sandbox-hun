"""
Download files from Google Drive earth_engine_exports folder.
Supports both Service Account (recommended) and OAuth 2.0 Client ID authentication.
"""

import argparse
import io
import os

import google.auth
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Load environment variables from .env
load_dotenv()

# Scopes required
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Use local server flow (standard for desktop apps)
# Request offline access to get a refresh token
import socket
import subprocess


def _kill_process_on_port(port):
    try:
        # Try to bind to check if port is free
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            return  # Port is free
    except OSError as e:
        if "Address already in use" not in str(e):
            raise  # Re-raise if it's not the "address already in use" error

        print(f"Port {port} is in use. Attempting to kill process...")
        if os.name == "posix":  # Linux/macOS
            try:
                pid_output = subprocess.check_output(["lsof", "-t", f"-i:{port}"]).decode().strip()
                if pid_output:
                    pid = int(pid_output.splitlines()[0])
                    print(f"Killing process {pid} on port {port}...")
                    os.kill(pid, 9)  # SIGKILL
                    print(f"Process {pid} killed.")
                else:
                    print(f"Could not find process on port {port} using lsof.")
            except (subprocess.CalledProcessError, ValueError) as e:
                print(f"Error finding/killing process on port {port} (lsof): {e}")
        elif os.name == "nt":  # Windows
            try:
                output = subprocess.check_output(["netstat", "-ano"]).decode()
                pid = None
                for line in output.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.strip().split()
                        if len(parts) > 4:
                            pid = int(parts[4])
                            break
                if pid:
                    print(f"Killing process {pid} on port {port}...")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True)
                    print(f"Process {pid} killed.")
                else:
                    print(f"Could not find process on port {port} using netstat.")
            except (subprocess.CalledProcessError, ValueError) as e:
                print(f"Error finding/killing process on port {port} (netstat/taskkill): {e}")
        else:
            print(f"Warning: Cannot automatically kill process on port {port} on this OS.")


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
            creds, _project_id = google.auth.default(scopes=SCOPES)
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

    # if os.path.exists("local_pipeline/token.json"):
    #     creds = Credentials.from_authorized_user_file("local_pipeline/token.json", SCOPES)
    # elif os.path.exists("token.json"):  # Check current dir too
    #     creds = Credentials.from_authorized_user_file("token.json", SCOPES)

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

            _kill_process_on_port(8080)
            creds = flow.run_local_server(port=8080)

        # Save token for next time (only for interactive auth)
        with open("local_pipeline/token.json", "w") as token:
            token.write(creds.to_json())

    return creds


def find_folder(service, folder_name):
    """Find folder by name."""
    results = (
        service.files()
        .list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)",
        )
        .execute()
    )

    items = results.get("files", [])
    return items[0]["id"] if items else None


def list_files(service, folder_id, pattern=None):
    """List files in folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    if pattern:
        query += f" and name contains '{pattern}'"

    results = service.files().list(q=query, fields="files(id, name, size)").execute()

    return results.get("files", [])


def download_file(service, file_id, file_name, output_dir):
    """Download a file."""
    request = service.files().get_media(fileId=file_id)

    output_path = os.path.join(output_dir, file_name)

    # Check if file already exists and has same size?
    # For now, just overwrite as requested, but maybe print a message
    if os.path.exists(output_path):
        print(f"  Overwriting existing file: {file_name}")

    with io.FileIO(output_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"  Progress: {int(status.progress() * 100)}%", end="\r")
        print()  # New line after progress

    return output_path


def download_data(
    folder_name="earth_engine_exports",
    pattern="budapest_2024",
    output_dir="data",
    force_interactive=False,
):
    """Main function to be called by other scripts."""

    # Authenticate
    print("Authenticating with Google Drive...")
    creds = authenticate(force_interactive=force_interactive)
    if not creds:
        raise Exception("Authentication failed")

    service = build("drive", "v3", credentials=creds)

    # Find folder
    print(f"Looking for folder: {folder_name}")
    folder_id = find_folder(service, folder_name)
    if not folder_id:
        raise Exception(
            f"Folder '{folder_name}' not found! Ensure it is shared with the Service Account."
        )

    print(f"✓ Found folder (ID: {folder_id})")

    # List files
    print(f"\nSearching for files matching: {pattern}")
    files = list_files(service, folder_id, pattern)

    if not files:
        print(f"No files found matching '{pattern}'")
        return []

    print(f"\nFound {len(files)} file(s):")
    total_size = 0
    for f in files:
        size_mb = int(f.get("size", 0)) / (1024 * 1024)
        total_size += size_mb
        print(f"  - {f['name']} ({size_mb:.1f} MB)")

    print(f"\nTotal size: {total_size:.1f} MB")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Download files
    print(f"\nDownloading to {output_dir}/...")
    downloaded_files = []
    for f in files:
        print(f"\nDownloading: {f['name']}")
        output_path = download_file(service, f["id"], f["name"], output_dir)
        print(f"  ✓ Saved to: {output_path}")
        downloaded_files.append(output_path)

    print("\n✓ All files downloaded successfully!")
    return downloaded_files


def main():
    parser = argparse.ArgumentParser(description="Download files from Google Drive")
    parser.add_argument(
        "--folder", type=str, default="earth_engine_exports", help="Google Drive folder name"
    )
    parser.add_argument(
        "--pattern", type=str, default="budapest_2024", help="File name pattern to match"
    )
    parser.add_argument("--output", type=str, default="data", help="Output directory")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force interactive OAuth authentication (ignore Service Account)",
    )
    args = parser.parse_args()

    try:
        download_data(args.folder, args.pattern, args.output, force_interactive=args.interactive)

        print("\nNext steps:")
        print("  uv run python local_pipeline/0_merge_tiles.py")
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
