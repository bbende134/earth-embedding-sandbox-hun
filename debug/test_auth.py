import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def main():
    cred_path = "credentials.json"
    if not os.path.exists(cred_path):
        cred_path = "local_pipeline/credentials.json"

    if not os.path.exists(cred_path):
        print(f"Credentials not found at {cred_path}")
        return

    print(f"Using credentials from: {cred_path}")
    print(f"Requesting scopes: {SCOPES}")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
        print("Flow created successfully.")
        print("Attempting console auth...")
        flow.run_console()
        print("Authentication successful!")
    except Exception as e:
        print(f"Authentication failed: {e}")


if __name__ == "__main__":
    main()
