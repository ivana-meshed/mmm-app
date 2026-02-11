#!/usr/bin/env python3
"""
Manual Queue Trigger Script

Manually triggers queue processing when Cloud Scheduler is disabled.
Useful for development environments where scheduler is paused for cost savings.

Usage:
    # Trigger one queue tick (process one job)
    python scripts/trigger_queue.py

    # Trigger multiple ticks (process multiple jobs)
    python scripts/trigger_queue.py --count 5

    # Use specific queue name
    python scripts/trigger_queue.py --queue-name default-dev

    # Keep triggering until queue is empty
    python scripts/trigger_queue.py --until-empty
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Environment constants
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
REGION = os.getenv("REGION", "europe-west1")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
QUEUE_ROOT = os.getenv("QUEUE_ROOT", "robyn-queues")

try:
    from google.cloud import storage
    from google.cloud import run_v2
except ImportError:
    logger.error(
        "Google Cloud libraries not installed. "
        "Install with: pip install google-cloud-storage google-cloud-run"
    )
    sys.exit(1)


def check_queue_status(bucket_name: str, queue_name: str) -> dict:
    """
    Check current queue status.

    Returns dict with:
        - total_jobs: Total number of jobs in queue
        - pending_jobs: Number of PENDING jobs
        - running_jobs: Number of RUNNING/LAUNCHING jobs
        - completed_jobs: Number of SUCCEEDED/FAILED jobs
        - queue_running: Whether queue is set to run
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_path = f"{QUEUE_ROOT}/{queue_name}/queue.json"
    blob = bucket.blob(blob_path)

    if not blob.exists():
        return {
            "total_jobs": 0,
            "pending_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "queue_running": False,
            "exists": False,
        }

    try:
        doc = json.loads(blob.download_as_text())
        entries = doc.get("entries", [])

        status_counts = {
            "PENDING": 0,
            "RUNNING": 0,
            "LAUNCHING": 0,
            "SUCCEEDED": 0,
            "FAILED": 0,
            "ERROR": 0,
            "CANCELLED": 0,
        }

        for entry in entries:
            status = entry.get("status", "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "total_jobs": len(entries),
            "pending_jobs": status_counts["PENDING"],
            "running_jobs": status_counts["RUNNING"]
            + status_counts["LAUNCHING"],
            "completed_jobs": status_counts["SUCCEEDED"]
            + status_counts["FAILED"]
            + status_counts["ERROR"]
            + status_counts["CANCELLED"],
            "queue_running": doc.get("queue_running", True),
            "exists": True,
            "status_counts": status_counts,
        }
    except Exception as e:
        logger.error(f"Failed to check queue status: {e}")
        return {
            "total_jobs": 0,
            "pending_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "queue_running": False,
            "exists": True,
            "error": str(e),
        }


def resume_queue(bucket_name: str, queue_name: str) -> bool:
    """
    Resume a paused queue by setting queue_running to true.

    Args:
        bucket_name: GCS bucket name
        queue_name: Queue name

    Returns:
        True if successful, False otherwise
    """
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob_path = f"{QUEUE_ROOT}/{queue_name}/queue.json"
        blob = bucket.blob(blob_path)

        if not blob.exists():
            logger.error(f"Queue does not exist: {queue_name}")
            return False

        # Load current queue
        doc = json.loads(blob.download_as_text())

        # Set queue_running to true
        doc["queue_running"] = True
        doc["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Save back to GCS
        blob.upload_from_string(
            json.dumps(doc, indent=2),
            content_type="application/json",
        )

        logger.info(f"✅ Resumed queue '{queue_name}'")
        return True

    except Exception as e:
        logger.error(f"Failed to resume queue: {e}")
        return False


def trigger_queue_via_http(service_url: str, queue_name: str) -> dict:
    """
    Trigger queue processing by calling the web service endpoint.

    Args:
        service_url: Cloud Run service URL
        queue_name: Queue name to process

    Returns:
        Response dict with ok, message, changed fields
    """
    import requests
    from google.auth import default
    from google.auth.transport.requests import Request

    # Get credentials for service account
    credentials, project = default()
    credentials.refresh(Request())

    url = f"{service_url}?queue_tick=1&name={queue_name}"

    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=300,  # 5 minute timeout for queue tick
        )

        if response.status_code == 200:
            # Try to parse JSON response
            try:
                return response.json()
            except:
                return {
                    "ok": True,
                    "message": "Queue tick completed",
                    "changed": True,
                }
        else:
            return {
                "ok": False,
                "message": f"HTTP {response.status_code}: {response.text[:200]}",
                "changed": False,
            }
    except Exception as e:
        return {"ok": False, "message": f"Request failed: {e}", "changed": False}


def get_service_url() -> str:
    """Get the Cloud Run service URL."""
    # Try to get from environment first
    service_url = os.getenv("WEB_SERVICE_URL")
    if service_url:
        logger.info("Using WEB_SERVICE_URL from environment")
        return service_url

    # Otherwise, try to query Cloud Run API
    logger.info("WEB_SERVICE_URL not set, attempting to query Cloud Run API...")
    try:
        client = run_v2.ServicesClient()
        service_name = f"projects/{PROJECT_ID}/locations/{REGION}/services/mmm-app-dev"

        try:
            service = client.get_service(name=service_name)
            return service.uri
        except Exception as e:
            # Try production name
            logger.debug(f"Dev service not found: {e}")
            service_name = f"projects/{PROJECT_ID}/locations/{REGION}/services/mmm-app"
            service = client.get_service(name=service_name)
            return service.uri

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to get service URL: {error_msg}")
        
        # Check if it's a permission error
        if "403" in error_msg or "Permission" in error_msg or "denied" in error_msg:
            logger.error("")
            logger.error("⚠️  Permission denied when querying Cloud Run API")
            logger.error("You need 'run.services.get' permission OR set WEB_SERVICE_URL environment variable.")
            logger.error("")
            logger.error("To fix, run one of these commands:")
            logger.error("")
            logger.error("Option 1 - Get URL and set environment variable:")
            logger.error(f"  gcloud run services describe mmm-app-dev --region={REGION} --format='value(status.url)'")
            logger.error("  # or for production:")
            logger.error(f"  gcloud run services describe mmm-app --region={REGION} --format='value(status.url)'")
            logger.error("  # Then set it:")
            logger.error("  export WEB_SERVICE_URL=<the-url-from-above>")
            logger.error("")
            logger.error("Option 2 - Get URL from Cloud Console:")
            logger.error(f"  https://console.cloud.google.com/run?project={PROJECT_ID}")
            logger.error("  Copy the URL and run: export WEB_SERVICE_URL=<url>")
            logger.error("")
        
        raise RuntimeError(
            "Cannot get Cloud Run service URL. "
            "Set WEB_SERVICE_URL environment variable or ensure you have Cloud Run permissions."
        ) from e


def main():
    parser = argparse.ArgumentParser(
        description="Manually trigger queue processing"
    )
    parser.add_argument(
        "--queue-name",
        type=str,
        default=os.getenv("DEFAULT_QUEUE_NAME", "default"),
        help="Queue name to process (default: from env or 'default')",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of queue ticks to trigger (default: 1)",
    )
    parser.add_argument(
        "--until-empty",
        action="store_true",
        help="Keep processing until no pending jobs remain",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=5,
        help="Delay in seconds between ticks (default: 5)",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only check queue status, don't trigger processing",
    )
    parser.add_argument(
        "--resume-queue",
        action="store_true",
        help="Automatically resume queue if it is paused",
    )

    args = parser.parse_args()

    logger.info(f"Checking queue status for '{args.queue_name}'...")

    # Check initial status
    status = check_queue_status(GCS_BUCKET, args.queue_name)

    if not status["exists"]:
        logger.error(f"Queue '{args.queue_name}' does not exist")
        sys.exit(1)

    print(f"\n📊 Queue Status: {args.queue_name}")
    print(f"  Total jobs: {status['total_jobs']}")
    print(f"  Pending: {status['pending_jobs']}")
    print(f"  Running: {status['running_jobs']}")
    print(f"  Completed: {status['completed_jobs']}")
    print(f"  Queue running: {status['queue_running']}\n")

    if status.get("status_counts"):
        print("  Status breakdown:")
        for stat, count in status["status_counts"].items():
            if count > 0:
                print(f"    {stat}: {count}")
        print()

    if args.status_only:
        return

    if status["pending_jobs"] == 0:
        logger.info("No pending jobs to process")
        return

    # Handle paused queue
    if not status["queue_running"]:
        logger.warning("⚠️  Queue is paused (queue_running=false)")

        if args.resume_queue:
            print("\n🔄 Resuming queue...")
            if resume_queue(GCS_BUCKET, args.queue_name):
                print("✅ Queue resumed successfully")
                # Refresh status
                time.sleep(1)
                status = check_queue_status(GCS_BUCKET, args.queue_name)
            else:
                logger.error("Failed to resume queue")
                sys.exit(1)
        else:
            logger.error("Jobs will not be processed until queue is resumed")
            logger.error(
                f"Run with --resume-queue flag to automatically resume, or:"
            )
            logger.error(
                f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue --until-empty"
            )
            sys.exit(1)

    # Get service URL
    logger.info("Getting Cloud Run service URL...")
    try:
        service_url = get_service_url()
        logger.info(f"✅ Service URL: {service_url}")
    except Exception as e:
        # Error already logged in get_service_url with helpful instructions
        sys.exit(1)

    # Determine how many ticks to trigger
    if args.until_empty:
        max_ticks = status["pending_jobs"]
        logger.info(
            f"Will process until queue is empty ({max_ticks} pending jobs)"
        )
    else:
        max_ticks = args.count
        logger.info(f"Will trigger {max_ticks} queue tick(s)")

    # Trigger queue ticks
    successful = 0
    for i in range(max_ticks):
        logger.info(f"\n🔄 Triggering queue tick {i + 1}/{max_ticks}...")

        result = trigger_queue_via_http(service_url, args.queue_name)

        if result.get("ok"):
            logger.info(f"✅ {result.get('message', 'Success')}")
            if result.get("changed"):
                successful += 1
        else:
            logger.error(f"❌ {result.get('message', 'Failed')}")

        # Check if we should continue
        if args.until_empty:
            time.sleep(2)  # Brief delay to let status update
            status = check_queue_status(GCS_BUCKET, args.queue_name)
            if status["pending_jobs"] == 0:
                logger.info("\n✅ Queue is now empty!")
                break

        # Delay before next tick (except on last iteration)
        if i < max_ticks - 1:
            time.sleep(args.delay)

    # Final status
    logger.info("\n📊 Final queue status:")
    final_status = check_queue_status(GCS_BUCKET, args.queue_name)
    print(f"  Pending: {final_status['pending_jobs']}")
    print(f"  Running: {final_status['running_jobs']}")
    print(f"  Completed: {final_status['completed_jobs']}")

    print(f"\n✅ Triggered {successful} queue tick(s) successfully")


if __name__ == "__main__":
    main()
