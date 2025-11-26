#!/bin/bash
# export GOOGLE_APPLICATION_CREDENTIALS="${PWD}/gen-lang-client-0291927848-14f8e1a428bd.json"
# Master script to run the full Hungary processing pipeline with SMOKE TEST

SKIP_TEST=false
for arg in "$@"
do
    if [ "$arg" == "--skip-test" ]; then
        SKIP_TEST=true
    fi
done

echo "Starting Hungary Full Processing Pipeline..."

if [ "$SKIP_TEST" = false ]; then
    # --- SMOKE TEST ---
    echo "----------------------------------------------------------------"
    echo "PHASE 1: SMOKE TEST (Single Tile)"
    echo "----------------------------------------------------------------"

    echo "[Test] Submitting single tile export..."
    uv run python hungary_full_pipeline/0_export_grid.py --test

    if [ $? -ne 0 ]; then
        echo "❌ [Test] Export submission failed!"
        exit 1
    fi

    echo "[Test] Waiting for export and processing (One-Shot)..."
    # This will wait until a file appears in Drive, process it, and exit
    uv run python hungary_full_pipeline/1_process_loop.py --one-shot

    if [ $? -ne 0 ]; then
        echo "❌ [Test] Processing failed!"
        exit 1
    fi

    echo "[Test] Verifying data in Milvus..."
    uv run python hungary_full_pipeline/3_verify_test.py

    if [ $? -ne 0 ]; then
        echo "❌ [Test] Verification failed! Aborting full run."
        exit 1
    fi

    echo "✅ [Test] Smoke test PASSED!"
    read -p "Run full production pipeline? (Y to continue): " confirm
    if [[ "$confirm" != "Y" ]]; then
        echo "Aborting full run."
        exit 0
    fi
else
    echo "Skipping Smoke Test..."
fi
echo "----------------------------------------------------------------"
echo "PHASE 2: FULL PRODUCTION RUN"
echo "----------------------------------------------------------------"

# --- PRODUCTION RUN ---

# --- PRODUCTION RUN ---

# 1. Check Task Status & Export Grid
echo "Step 1: Checking Earth Engine Task Status..."
uv run python hungary_full_pipeline/check_ee_tasks.py
TASK_STATUS=$?

if [ $TASK_STATUS -eq 0 ]; then
    echo "Tasks are finished or none found. Checking if we need to submit..."
    # Optional: We could check if we *should* submit. 
    # But for now, let's assume if no tasks are running, we might want to submit OR just process.
    # The user said "if finished, just access google drive".
    # This implies we assume exports were already submitted.
    # But if it's a fresh run, we need to submit.
    # Let's add a prompt or just submit if NO tasks found at all?
    # check_ee_tasks returns 0 if NO tasks found too.
    
    # Safe approach: Always run export script, but export script should be idempotent?
    # EE export script submits new tasks. We don't want duplicates.
    
    # Let's rely on the user's intent: "currently all the earth engine processing finished"
    # So we skip export.
    echo "Assuming exports are done. Skipping submission."
else
    echo "Tasks are currently RUNNING. Skipping submission to avoid duplicates."
fi

# If you really want to force submission, comment out the check above.
# uv run python hungary_full_pipeline/0_export_grid.py

# 2. Start Process Loop (Runs until stopped)
echo "Step 2: Starting Process Loop (Download -> Process -> Load)..."
echo "This will run indefinitely monitoring Google Drive."
uv run python hungary_full_pipeline/1_process_loop.py
