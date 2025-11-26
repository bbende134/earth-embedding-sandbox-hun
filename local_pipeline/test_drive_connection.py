"""
Test Google Drive Connection and Download
"""

import os
import shutil

from dotenv import load_dotenv
from download_from_drive import download_data

# Load environment variables from .env
load_dotenv()


def test_drive():
    print("=== Testing Google Drive Connection ===")

    test_output_dir = "test_data_download"

    # Clean up previous test run
    if os.path.exists(test_output_dir):
        shutil.rmtree(test_output_dir)

    try:
        # Try to download files matching a pattern that should exist
        # We use the default folder "earth_engine_exports"
        # We use a pattern that likely matches something, or just list everything if pattern is empty
        # But download_data requires a pattern. Let's use "budapest" as it was in the example
        print(f"Attempting to download files to {test_output_dir}...")

        # We'll try to download just ONE file if possible, but download_data downloads all matching.
        # Let's just run it and see. If it works, it works.
        files = download_data(output_dir=test_output_dir)

        if files:
            print(f"\nSUCCESS: Downloaded {len(files)} files.")
            for f in files:
                print(f"  - {f}")

            # Verify file existence
            if os.path.exists(files[0]):
                print(f"\nVerified file exists on disk: {files[0]}")
            else:
                print(f"\nERROR: File reported downloaded but not found: {files[0]}")
        else:
            print(
                "\nWARNING: No files found to download. Connection might be okay, but check folder/pattern."
            )

    except Exception as e:
        print(f"\nFAILED: {e}")
    finally:
        # Cleanup
        print(f"\nCleaning up {test_output_dir}...")
        if os.path.exists(test_output_dir):
            shutil.rmtree(test_output_dir)
        print("Done.")


if __name__ == "__main__":
    test_drive()
