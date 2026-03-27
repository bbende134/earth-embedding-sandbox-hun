"""
Step 4: Verify completeness of the multimodal pipeline run.

Checks three layers against the manifest:
  1. Earth Engine task status  (COMPLETED / FAILED / RUNNING / UNKNOWN)
  2. Local download presence   (file exists in data_multimodal/)
  3. Milvus ingestion          (tile_id present in dyn_terra for all expected modalities)

Prints a per-tile status table and exits non-zero if anything is missing.
"""

import argparse
import json
import os
import sys

import ee
from pymilvus import Collection, connections, utility

MANIFEST_PATH = "data_multimodal/manifest.json"
LOCAL_DATA_DIR = "data_multimodal"
MILVUS_COLLECTION = "dyn_terra"
EXPECTED_MODALITIES = {"untok_sen2rgb@224", "untok_sen1grd@224", "untok_dem@224"}


def check_ee_tasks(tile_manifest: dict) -> dict:
    """Returns {tile_name: ee_status} for all tiles."""
    try:
        ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize()

    statuses = {}

    try:
        all_tasks = ee.data.listOperations()
    except Exception as e:
        print(f"Warning: Could not fetch EE tasks: {e}")
        return {name: "UNKNOWN" for name in tile_manifest}

    task_map = {t["name"].split("/")[-1]: t for t in all_tasks}

    for tile_name, info in tile_manifest.items():
        tid = info["task_id"]
        if tid in task_map:
            state = task_map[tid].get("metadata", {}).get("state", "UNKNOWN")
            statuses[tile_name] = state
        else:
            statuses[tile_name] = "NOT_FOUND"

    return statuses


def check_local(tile_manifest: dict) -> dict:
    """Returns {tile_name: bool} — whether the TIF exists locally."""
    return {
        name: os.path.exists(os.path.join(LOCAL_DATA_DIR, f"{name}.tif")) for name in tile_manifest
    }


def check_milvus(tile_names: list, host: str, port: str) -> dict:
    """Returns {tile_name: set_of_modalities_present}."""
    connections.connect("default", host=host, port=port)

    if not utility.has_collection(MILVUS_COLLECTION):
        return {name: set() for name in tile_names}

    coll = Collection(MILVUS_COLLECTION)
    coll.load()

    result = {}
    for name in tile_names:
        rows = coll.query(
            expr=f'tile_id == "{name}"',
            output_fields=["modality"],
        )
        result[name] = {r["modality"] for r in rows}

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--milvus-host", default="localhost")
    parser.add_argument("--milvus-port", default="19530")
    parser.add_argument("--skip-ee", action="store_true", help="Skip EE task status check")
    parser.add_argument(
        "--resubmit-failed",
        action="store_true",
        help="Print names of EE-FAILED tiles (re-submission not automatic)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.manifest):
        print(f"ERROR: Manifest not found at {args.manifest}")
        print("Run 0_export_multimodal.py first to generate it.")
        sys.exit(1)

    with open(args.manifest) as f:
        manifest = json.load(f)

    tile_manifest = manifest["tiles"]
    tile_names = list(tile_manifest.keys())
    n = len(tile_names)

    print(f"Manifest: {args.manifest}")
    print(f"  submitted: {manifest.get('submitted_at', '?')}")
    print(f"  geojson:   {manifest.get('geojson', '?')}")
    print(f"  dates:     {manifest.get('start')} → {manifest.get('end')}")
    print(f"  tiles:     {n}")
    print()

    # --- EE status ---
    if args.skip_ee:
        ee_status = {name: "SKIPPED" for name in tile_names}
    else:
        print("Checking Earth Engine task statuses...")
        ee_status = check_ee_tasks(tile_manifest)

    # --- Local files ---
    local_status = check_local(tile_manifest)

    # --- Milvus ---
    print("Checking Milvus...")
    milvus_status = check_milvus(tile_names, args.milvus_host, args.milvus_port)

    # --- Report ---
    col_w = max(len(n) for n in tile_names) + 2
    print()
    print(f"{'TILE':<{col_w}} {'EE':<12} {'LOCAL':<8} {'MILVUS'}")
    print("-" * (col_w + 35))

    missing_ee = []
    missing_local = []
    missing_milvus = []

    for name in sorted(tile_names):
        ee_s = ee_status[name]
        local = "✓" if local_status[name] else "✗"
        mods = milvus_status[name]
        missing_mods = EXPECTED_MODALITIES - mods
        if missing_mods:
            milvus_s = f"✗ missing: {', '.join(sorted(missing_mods))}"
        else:
            milvus_s = f"✓ ({len(mods)}/{len(EXPECTED_MODALITIES)} modalities)"

        print(f"{name:<{col_w}} {ee_s:<12} {local:<8} {milvus_s}")

        if ee_s not in ("COMPLETED", "SKIPPED"):
            missing_ee.append(name)
        if not local_status[name]:
            missing_local.append(name)
        if missing_mods:
            missing_milvus.append(name)

    print()
    print(f"Summary: {n} tiles total")
    print(f"  EE issues:       {len(missing_ee)}")
    print(f"  Not downloaded:  {len(missing_local)}")
    print(f"  Milvus gaps:     {len(missing_milvus)}")

    if args.resubmit_failed and missing_ee:
        print()
        print("Tiles needing re-export:")
        for name in missing_ee:
            print(f"  {name}  (EE status: {ee_status[name]})")

    any_issue = missing_ee or missing_local or missing_milvus
    sys.exit(1 if any_issue else 0)


if __name__ == "__main__":
    main()
