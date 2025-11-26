import io
import os

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate():
    creds = None
    if os.path.exists("local_pipeline/token.json"):
        creds = Credentials.from_authorized_user_file("local_pipeline/token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "local_pipeline/credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=8080)
    return creds


def main():
    creds = authenticate()
    service = build("drive", "v3", credentials=creds)

    # Target file: hun_2024_tile_070-0000003072-0000000000.tif
    # We need to find its ID first
    filename = "hun_2024_tile_070-0000003072-0000000000.tif"
    print(f"Searching for {filename}...")

    results = (
        service.files()
        .list(q=f"name='{filename}' and trashed=false", fields="files(id, name, size)")
        .execute()
    )

    files = results.get("files", [])
    if not files:
        print("File not found on Drive!")
        return

    f = files[0]
    print(f"Found file: {f['name']} (ID: {f['id']}, Size: {f.get('size')} bytes)")

    local_path = f"debug_{filename}"
    print(f"Downloading to {local_path}...")

    request = service.files().get_media(fileId=f["id"])
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Progress: {int(status.progress() * 100)}%", end="\r")

    print("\nDownload complete.")

    if os.path.exists(local_path):
        size = os.path.getsize(local_path)
        print(f"Local file size: {size} bytes")
        if size == int(f.get("size", 0)):
            print("Size matches!")
        else:
            print("SIZE MISMATCH!")
    else:
        print("Local file missing!")


if __name__ == "__main__":
    main()
