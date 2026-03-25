"""
Check if Earth Engine export tasks for the Hungary pipeline are finished.
Returns exit code 0 if all tasks are COMPLETED or FAILED (none RUNNING/READY).
Returns exit code 1 if tasks are still RUNNING or READY.
"""

import sys

import ee

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()


def check_tasks():
    print("Checking Earth Engine task status...")
    tasks = ee.data.getTaskList()

    # Filter for our tasks
    pipeline_tasks = [t for t in tasks if t["description"].startswith("hun_2021_tile_")]

    if not pipeline_tasks:
        print("No pipeline tasks found.")
        return 0  # Treat as "finished" (nothing running)

    running = [t for t in pipeline_tasks if t["state"] in ["RUNNING", "READY"]]
    failed = [t for t in pipeline_tasks if t["state"] == "FAILED"]
    completed = [t for t in pipeline_tasks if t["state"] == "COMPLETED"]

    print(
        f"Tasks Status: {len(running)} RUNNING/READY, {len(completed)} COMPLETED, {len(failed)} FAILED"
    )

    if running:
        print("Pipeline is still running.")
        for t in running:
            print(f"  - {t['description']} ({t['state']})")
        return 1
    else:
        print("All tasks finished.")
        return 0


if __name__ == "__main__":
    sys.exit(check_tasks())
