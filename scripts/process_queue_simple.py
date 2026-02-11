#!/usr/bin/env python3
"""
Simple standalone queue processor for MMM training jobs.

This script processes the queue directly without any Streamlit or app module dependencies.
It's truly standalone and can be run anywhere with Python and gcloud auth.

Usage:
    python scripts/process_queue_simple.py --queue-name default-dev
    python scripts/process_queue_simple.py --queue-name default-dev --count 5
    python scripts/process_queue_simple.py --queue-name default-dev --loop
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, List

from google.cloud import storage
from google.cloud import run_v2
from google.auth import impersonated_credentials
import google.auth

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Service account to impersonate for job execution
SERVICE_ACCOUNT = "mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com"


def get_impersonated_credentials():
    """Get credentials for the impersonated service account.
    
    This allows the script to use the service account's permissions
    regardless of GOOGLE_APPLICATION_CREDENTIALS environment variable.
    """
    # First, check if GOOGLE_APPLICATION_CREDENTIALS points to the right service account key
    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_file and os.path.exists(creds_file):
        try:
            with open(creds_file, 'r') as f:
                key_data = json.load(f)
                if key_data.get("client_email") == SERVICE_ACCOUNT:
                    logger.info(f"Using service account key file for: {SERVICE_ACCOUNT}")
                    from google.oauth2 import service_account
                    credentials = service_account.Credentials.from_service_account_file(
                        creds_file,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    return credentials
        except Exception as e:
            logger.debug(f"Could not use key file: {e}")
    
    # Try to impersonate the service account
    try:
        # Get source credentials (user's credentials)
        source_credentials, project = google.auth.default()
        
        # Create impersonated credentials
        target_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=SERVICE_ACCOUNT,
            target_scopes=target_scopes,
        )
        
        logger.info(f"Using impersonated credentials for: {SERVICE_ACCOUNT}")
        return credentials
    except Exception as e:
        error_msg = str(e)
        logger.error("=" * 80)
        logger.error("❌ Cannot authenticate as service account!")
        logger.error("=" * 80)
        
        if "iam.serviceAccounts.getAccessToken" in error_msg or "PERMISSION_DENIED" in error_msg:
            logger.error(f"Impersonation failed for: {SERVICE_ACCOUNT}")
            logger.error("")
            logger.error("This could be due to:")
            logger.error("1. IAM propagation delay (wait 2-3 minutes after granting permission)")
            logger.error("2. Source credentials don't have required OAuth scopes")
            logger.error("")
            logger.error("BEST SOLUTION: Use the service account key file directly!")
            logger.error("")
            logger.error("If you have access to the key file, point to it:")
            logger.error(f"  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/mmm-web-service-sa-key.json")
            logger.error("")
            logger.error("Then run this script again. It will use the key file directly.")
            logger.error("")
            logger.error("Alternatively, wait 2-3 minutes for IAM to propagate, then retry.")
        else:
            logger.error(f"Failed to create impersonated credentials: {e}")
            logger.error("")
            logger.error("Make sure you have run:")
            logger.error("  gcloud auth application-default login")
        
        logger.error("=" * 80)
        sys.exit(1)


def load_queue_from_gcs(bucket_name: str, queue_name: str, credentials=None) -> Dict:
    """Load queue document from GCS."""
    try:
        client = storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob_path = f"robyn-queues/{queue_name}/queue.json"
        blob = bucket.blob(blob_path)
        
        if not blob.exists():
            logger.error(f"Queue not found: gs://{bucket_name}/{blob_path}")
            return None
        
        content = blob.download_as_text()
        queue_doc = json.loads(content)
        logger.info(f"Loaded queue '{queue_name}' from GCS")
        return queue_doc
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        return None


def save_queue_to_gcs(bucket_name: str, queue_name: str, queue_doc: Dict, credentials=None) -> bool:
    """Save queue document to GCS."""
    try:
        client = storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob_path = f"robyn-queues/{queue_name}/queue.json"
        blob = bucket.blob(blob_path)
        
        queue_doc["saved_at"] = datetime.now(timezone.utc).isoformat()
        content = json.dumps(queue_doc, indent=2, default=str)
        blob.upload_from_string(content, content_type="application/json")
        
        logger.info(f"Saved queue to GCS: gs://{bucket_name}/{blob_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving queue: {e}")
        return False


def launch_cloud_run_job(
    project_id: str,
    region: str,
    job_name: str,
    config: Dict,
    params: Dict,
    credentials=None,
) -> Optional[str]:
    """Launch a Cloud Run training job."""
    try:
        client = run_v2.JobsClient(credentials=credentials)
        job_path = f"projects/{project_id}/locations/{region}/jobs/{job_name}"
        
        # Create environment variables from config
        env_vars = []
        for key, value in config.items():
            if value is not None:
                env_vars.append(
                    run_v2.EnvVar(name=str(key).upper(), value=str(value))
                )
        
        # Add params as JSON env var
        env_vars.append(
            run_v2.EnvVar(name="JOB_PARAMS", value=json.dumps(params))
        )
        
        # Create execution request
        request = run_v2.RunJobRequest(
            name=job_path,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=env_vars
                    )
                ],
            ),
        )
        
        # Execute job
        operation = client.run_job(request=request)
        execution_name = operation.metadata.name if hasattr(operation, 'metadata') else 'unknown'
        
        logger.info(f"✅ Launched job: {job_name}")
        logger.info(f"   Execution: {execution_name}")
        return execution_name
        
    except Exception as e:
        logger.error(f"❌ Failed to launch job: {e}")
        return None


def process_one_job(
    queue_doc: Dict,
    bucket_name: str,
    queue_name: str,
    project_id: str,
    region: str,
    training_job_name: str,
    credentials=None,
) -> bool:
    """Process one PENDING job from the queue."""
    
    if not queue_doc.get("queue_running", True):
        logger.warning("Queue is paused (queue_running=false)")
        return False
    
    entries = queue_doc.get("entries", [])
    
    # Find first PENDING job
    pending_job = None
    pending_idx = None
    for idx, job in enumerate(entries):
        if job.get("status") == "PENDING":
            pending_job = job
            pending_idx = idx
            break
    
    if not pending_job:
        logger.info("No PENDING jobs found")
        return False
    
    logger.info(f"Processing job {pending_idx + 1}/{len(entries)}")
    logger.info(f"  Country: {pending_job.get('params', {}).get('country', 'unknown')}")
    logger.info(f"  Revision: {pending_job.get('params', {}).get('revision', 'unknown')}")
    
    # Mark as LAUNCHING
    entries[pending_idx]["status"] = "LAUNCHING"
    entries[pending_idx]["launched_at"] = datetime.now(timezone.utc).isoformat()
    
    # Save queue
    if not save_queue_to_gcs(bucket_name, queue_name, queue_doc, credentials=credentials):
        logger.error("Failed to save queue")
        return False
    
    # Launch job
    params = pending_job.get("params", {})
    config = {
        "country": params.get("country"),
        "revision": params.get("revision"),
        "data_gcs_path": params.get("data_gcs_path"),
        "gcs_bucket": bucket_name,
    }
    
    execution_name = launch_cloud_run_job(
        project_id=project_id,
        region=region,
        job_name=training_job_name,
        config=config,
        params=params,
        credentials=credentials,
    )
    
    if execution_name:
        entries[pending_idx]["status"] = "RUNNING"
        entries[pending_idx]["execution_name"] = execution_name
        save_queue_to_gcs(bucket_name, queue_name, queue_doc, credentials=credentials)
        logger.info("✅ Job launched successfully")
        return True
    else:
        # Mark as FAILED
        entries[pending_idx]["status"] = "FAILED"
        entries[pending_idx]["error"] = "Failed to launch Cloud Run job"
        save_queue_to_gcs(bucket_name, queue_name, queue_doc, credentials=credentials)
        logger.error("❌ Job launch failed")
        return False


def process_queue(
    bucket_name: str,
    queue_name: str,
    max_jobs: int = 1,
    loop_until_empty: bool = False,
    project_id: Optional[str] = None,
    region: str = "europe-west1",
    training_job_name: str = "mmm-app-training",
    credentials=None,
) -> int:
    """
    Process jobs from the queue.
    
    Returns:
        Number of jobs processed
    """
    if not project_id:
        project_id = os.getenv("PROJECT_ID", "datawarehouse-422511")
    
    processed = 0
    
    while True:
        # Load queue
        queue_doc = load_queue_from_gcs(bucket_name, queue_name, credentials=credentials)
        if not queue_doc:
            logger.error("Failed to load queue")
            break
        
        # Resume queue if paused
        if not queue_doc.get("queue_running", True):
            logger.info("Queue is paused - resuming...")
            queue_doc["queue_running"] = True
            if not save_queue_to_gcs(bucket_name, queue_name, queue_doc, credentials=credentials):
                logger.error("Failed to resume queue")
                break
        
        # Show status
        entries = queue_doc.get("entries", [])
        pending_count = sum(1 for j in entries if j.get("status") == "PENDING")
        running_count = sum(1 for j in entries if j.get("status") == "RUNNING")
        
        logger.info(f"📊 Queue Status: {queue_name}")
        logger.info(f"  Total: {len(entries)}")
        logger.info(f"  Pending: {pending_count}")
        logger.info(f"  Running: {running_count}")
        
        if pending_count == 0:
            logger.info("✅ No more pending jobs")
            break
        
        # Process one job
        success = process_one_job(
            queue_doc=queue_doc,
            bucket_name=bucket_name,
            queue_name=queue_name,
            project_id=project_id,
            region=region,
            training_job_name=training_job_name,
            credentials=credentials,
        )
        
        if success:
            processed += 1
        
        # Check if should continue
        if not loop_until_empty:
            if processed >= max_jobs:
                logger.info(f"Processed {processed} jobs (max: {max_jobs})")
                break
        
        if not success:
            logger.warning("Failed to process job, stopping")
            break
    
    return processed


def main():
    parser = argparse.ArgumentParser(
        description="Process MMM training queue standalone"
    )
    parser.add_argument(
        "--queue-name",
        default=os.getenv("DEFAULT_QUEUE_NAME", "default-dev"),
        help="Queue name (default: from DEFAULT_QUEUE_NAME env or 'default-dev')",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: from GCS_BUCKET env or 'mmm-app-output')",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Max number of jobs to process (default: 1)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Process until queue is empty",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("PROJECT_ID"),
        help="GCP project ID (default: from PROJECT_ID env)",
    )
    parser.add_argument(
        "--region",
        default="europe-west1",
        help="Cloud Run region (default: europe-west1)",
    )
    parser.add_argument(
        "--training-job-name",
        default="mmm-app-dev-training",
        help="Cloud Run training job name (default: mmm-app-dev-training)",
    )
    
    args = parser.parse_args()
    
    # Get impersonated credentials
    credentials = get_impersonated_credentials()
    
    logger.info("=" * 60)
    logger.info("MMM Queue Processor (Standalone)")
    logger.info("=" * 60)
    logger.info(f"Queue: {args.queue_name}")
    logger.info(f"Bucket: {args.bucket}")
    logger.info(f"Project: {args.project_id}")
    logger.info(f"Region: {args.region}")
    logger.info(f"Training Job: {args.training_job_name}")
    logger.info(f"Mode: {'loop until empty' if args.loop else f'process {args.count} job(s)'}")
    logger.info("=" * 60)
    
    try:
        processed = process_queue(
            bucket_name=args.bucket,
            queue_name=args.queue_name,
            max_jobs=args.count,
            loop_until_empty=args.loop,
            project_id=args.project_id,
            region=args.region,
            training_job_name=args.training_job_name,
            credentials=credentials,
        )
        
        logger.info("=" * 60)
        logger.info(f"✅ Processed {processed} job(s)")
        logger.info("=" * 60)
        
    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
