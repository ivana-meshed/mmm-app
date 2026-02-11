#!/usr/bin/env python3
"""
⚠️  DEPRECATED - DO NOT USE THIS SCRIPT ⚠️

This script has Streamlit import dependencies and will not work.

USE THIS INSTEAD:
    python scripts/process_queue_simple.py --loop

The process_queue_simple.py script is self-contained and works immediately.
"""

import sys

print("=" * 80)
print("⚠️  ERROR: WRONG SCRIPT!")
print("=" * 80)
print()
print("You are trying to run: process_queue_standalone.py")
print("This script is DEPRECATED and has import errors.")
print()
print("✅ USE THIS INSTEAD:")
print()
print("    python scripts/process_queue_simple.py --loop")
print()
print("The 'simple' processor is self-contained and actually works!")
print("=" * 80)
sys.exit(1)

# Old code below - DO NOT USE
# =============================

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Add app directory to path
app_dir = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(app_dir))

from google.cloud import storage, run_v2
from app_shared import (
    _safe_tick_once,
    build_job_config_from_params,
    execute_job_from_config,
)
from app_split_helpers import prepare_and_launch_job

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def process_queue(
    bucket_name: str,
    queue_name: str,
    max_jobs: int = 1,
    project_id: str = None,
    region: str = None,
    training_job_name: str = None,
) -> dict:
    """
    Process jobs from the queue.

    Args:
        bucket_name: GCS bucket name
        queue_name: Queue name
        max_jobs: Maximum number of jobs to process
        project_id: GCP project ID
        region: GCP region
        training_job_name: Cloud Run Job name for training

    Returns:
        dict with processing results
    """
    logger.info(f"[STANDALONE] Processing queue: {queue_name}")
    logger.info(f"[STANDALONE] Bucket: {bucket_name}")
    logger.info(f"[STANDALONE] Max jobs: {max_jobs}")

    # Load queue from GCS
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    queue_path = f"robyn-queues/{queue_name}/queue.json"
    blob = bucket.blob(queue_path)

    if not blob.exists():
        logger.error(f"[STANDALONE] Queue not found: {queue_path}")
        return {"error": "Queue not found", "processed": 0}

    queue_data = json.loads(blob.download_as_text())
    logger.info(
        f"[STANDALONE] Queue loaded: {len(queue_data.get('jobs', []))} jobs"
    )

    if not queue_data.get("queue_running", False):
        logger.warning("[STANDALONE] Queue is paused (queue_running=false)")
        # Auto-resume
        queue_data["queue_running"] = True
        blob.upload_from_string(
            json.dumps(queue_data, indent=2), content_type="application/json"
        )
        logger.info("[STANDALONE] Auto-resumed queue")

    processed = 0
    failed = 0

    for _ in range(max_jobs):
        try:
            # Use existing _safe_tick_once function
            result = _safe_tick_once(
                queue_doc=queue_data,
                bucket_name=bucket_name,
                queue_name=queue_name,
                launcher=prepare_and_launch_job,
            )

            if result and result.get("ok"):
                processed += 1
                logger.info(
                    f"[STANDALONE] Job {processed} processed successfully"
                )
                # Reload queue for next iteration
                queue_data = json.loads(blob.download_as_text())
            else:
                # No more jobs or failed
                break

        except Exception as e:
            logger.error(f"[STANDALONE] Error processing job: {e}", exc_info=True)
            failed += 1
            break

    logger.info(
        f"[STANDALONE] Processing complete: {processed} processed, {failed} failed"
    )

    return {
        "processed": processed,
        "failed": failed,
        "queue": queue_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Process MMM training queue standalone"
    )
    parser.add_argument(
        "--queue-name",
        type=str,
        default=os.getenv("DEFAULT_QUEUE_NAME", "default"),
        help="Queue name to process",
    )
    parser.add_argument(
        "--bucket-name",
        type=str,
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of jobs to process",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=os.getenv("PROJECT_ID", "datawarehouse-422511"),
        help="GCP project ID",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=os.getenv("REGION", "europe-west1"),
        help="GCP region",
    )
    parser.add_argument(
        "--training-job-name",
        type=str,
        default=os.getenv("TRAINING_JOB_NAME", "mmm-app-training"),
        help="Cloud Run Job name for training",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Process until queue is empty",
    )

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("[STANDALONE] Queue Processor Starting")
    logger.info(f"[STANDALONE] Queue: {args.queue_name}")
    logger.info(f"[STANDALONE] Bucket: {args.bucket_name}")
    logger.info(f"[STANDALONE] Project: {args.project_id}")
    logger.info(f"[STANDALONE] Region: {args.region}")
    logger.info("=" * 80)

    if args.loop:
        # Process until queue is empty
        total_processed = 0
        while True:
            result = process_queue(
                bucket_name=args.bucket_name,
                queue_name=args.queue_name,
                max_jobs=1,
                project_id=args.project_id,
                region=args.region,
                training_job_name=args.training_job_name,
            )

            if result.get("processed", 0) > 0:
                total_processed += result["processed"]
            else:
                # No more jobs
                break

        logger.info(f"[STANDALONE] Total processed: {total_processed}")
    else:
        # Process specified number
        result = process_queue(
            bucket_name=args.bucket_name,
            queue_name=args.queue_name,
            max_jobs=args.count,
            project_id=args.project_id,
            region=args.region,
            training_job_name=args.training_job_name,
        )

    logger.info("[STANDALONE] Queue processor finished")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
