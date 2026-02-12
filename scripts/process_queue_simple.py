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
from typing import Dict, List, Optional

import google.auth
from google.auth import impersonated_credentials
from google.cloud import run_v2, storage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Service account to impersonate for job execution
SERVICE_ACCOUNT = (
    "mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com"
)


def get_impersonated_credentials():
    """Get credentials for the impersonated service account.

    This allows the script to use the service account's permissions
    regardless of GOOGLE_APPLICATION_CREDENTIALS environment variable.
    """
    # First, check if GOOGLE_APPLICATION_CREDENTIALS points to the right service account key
    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_file and os.path.exists(creds_file):
        try:
            with open(creds_file, "r") as f:
                key_data = json.load(f)
                if key_data.get("client_email") == SERVICE_ACCOUNT:
                    logger.info(
                        f"Using service account key file for: {SERVICE_ACCOUNT}"
                    )
                    from google.oauth2 import service_account

                    credentials = (
                        service_account.Credentials.from_service_account_file(
                            creds_file,
                            scopes=[
                                "https://www.googleapis.com/auth/cloud-platform"
                            ],
                        )
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

        if (
            "iam.serviceAccounts.getAccessToken" in error_msg
            or "PERMISSION_DENIED" in error_msg
        ):
            logger.error(f"Impersonation failed for: {SERVICE_ACCOUNT}")
            logger.error("")
            logger.error("This could be due to:")
            logger.error(
                "1. IAM propagation delay (wait 2-3 minutes after granting permission)"
            )
            logger.error(
                "2. Source credentials don't have required OAuth scopes"
            )
            logger.error("")
            logger.error(
                "BEST SOLUTION: Use the service account key file directly!"
            )
            logger.error("")
            logger.error("If you have access to the key file, point to it:")
            logger.error(
                f"  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/mmm-web-service-sa-key.json"
            )
            logger.error("")
            logger.error(
                "Then run this script again. It will use the key file directly."
            )
            logger.error("")
            logger.error(
                "Alternatively, wait 2-3 minutes for IAM to propagate, then retry."
            )
        else:
            logger.error(f"Failed to create impersonated credentials: {e}")
            logger.error("")
            logger.error("Make sure you have run:")
            logger.error("  gcloud auth application-default login")

        logger.error("=" * 80)
        sys.exit(1)


def load_queue_from_gcs(
    bucket_name: str, queue_name: str, credentials=None
) -> Dict:
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


def save_queue_to_gcs(
    bucket_name: str, queue_name: str, queue_doc: Dict, credentials=None
) -> bool:
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
        from google.cloud import storage
        
        # Build complete job config JSON like Streamlit app does
        timestamp = config.get("timestamp", "")
        bucket_name = config.get("gcs_bucket", "mmm-app-output")
        
        # Build job config from params (similar to app_shared.py)
        job_config = {
            "country": params.get("country", ""),
            "iterations": int(params.get("iterations", 2000)),
            "trials": int(params.get("trials", 5)),
            "train_size": params.get("train_size", [0.7, 0.9]),
            "revision": params.get("revision", "default"),
            "date_input": params.get("date_input", ""),
            "start_date": params.get("start_date", "2024-01-01"),
            "end_date": params.get("end_date", ""),
            "gcs_bucket": bucket_name,
            "data_gcs_path": params.get("data_gcs_path", ""),
            "annotations_gcs_path": "",
            "paid_media_spends": params.get("paid_media_spends", []),
            "paid_media_vars": params.get("paid_media_vars", []),
            "context_vars": params.get("context_vars", []),
            "factor_vars": params.get("factor_vars", []),
            "organic_vars": params.get("organic_vars", []),
            "timestamp": timestamp,
            "output_timestamp": timestamp,  # For consistent result paths
            "dep_var": params.get("dep_var", "UPLOAD_VALUE"),
            "dep_var_type": params.get("dep_var_type", "revenue"),
            "date_var": params.get("date_var", "date"),
            "adstock": params.get("adstock", "geometric"),
            "hyperparameter_preset": params.get("hyperparameter_preset", "Meshed recommend"),
            "resample_freq": params.get("resample_freq", "none"),
            "use_parquet": True,
            "parallel_processing": True,
        }
        
        # Add optional fields
        if "custom_hyperparameters" in params:
            job_config["custom_hyperparameters"] = params["custom_hyperparameters"]
        if "column_agg_strategies" in params:
            job_config["column_agg_strategies"] = params["column_agg_strategies"]
        
        # Upload job config to GCS
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            json.dump(job_config, tmp, indent=2)
            tmp_path = tmp.name
        
        try:
            client_storage = storage.Client(credentials=credentials)
            bucket = client_storage.bucket(bucket_name)
            
            # Upload to timestamped location
            config_blob_path = f"training-configs/{timestamp}/job_config.json"
            blob = bucket.blob(config_blob_path)
            blob.upload_from_filename(tmp_path, content_type="application/json")
            config_gcs_path = f"gs://{bucket_name}/{config_blob_path}"
            
            # Also upload to "latest" for fallback
            latest_blob = bucket.blob("training-configs/latest/job_config.json")
            latest_blob.upload_from_filename(tmp_path, content_type="application/json")
            
            logger.info(f"   Uploaded job config to: {config_gcs_path}")
        finally:
            import os
            os.unlink(tmp_path)
        
        client = run_v2.JobsClient(credentials=credentials)
        job_path = f"projects/{project_id}/locations/{region}/jobs/{job_name}"

        # Set JOB_CONFIG_GCS_PATH environment variable (this is what R script reads!)
        env_vars = [
            run_v2.EnvVar(name="JOB_CONFIG_GCS_PATH", value=config_gcs_path)
        ]
        
        # Also set individual vars for backward compatibility (but R script ignores these)
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
        execution_name = (
            operation.metadata.name
            if hasattr(operation, "metadata")
            else "unknown"
        )

        logger.info(f"✅ Launched job: {job_name}")
        logger.info(f"   Execution: {execution_name}")
        return execution_name

    except Exception as e:
        logger.error(f"❌ Failed to launch job: {e}")
        logger.error(f"   Error details: {str(e)}", exc_info=True)
        return None


def verify_results_exist(
    result_path: str, credentials=None, timeout_seconds: int = 30
) -> dict:
    """
    Verify that results exist at the expected GCS path.
    
    Args:
        result_path: GCS path like gs://bucket/path/to/results/
        credentials: GCP credentials
        timeout_seconds: How long to wait for results to appear
        
    Returns:
        dict with keys:
            - exists: bool
            - files_found: list of filenames
            - message: str
    """
    import time
    from google.cloud import storage
    
    if not result_path.startswith("gs://"):
        return {"exists": False, "files_found": [], "message": "Invalid GCS path"}
    
    try:
        # Extract bucket and path
        path_parts = result_path[5:].split("/", 1)
        if len(path_parts) != 2:
            return {"exists": False, "files_found": [], "message": "Invalid path format"}
        
        bucket_name, gcs_path = path_parts
        # Ensure path ends with / for prefix search
        if not gcs_path.endswith("/"):
            gcs_path += "/"
        
        client = storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        
        # Wait for results to appear (with timeout)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            blobs = list(bucket.list_blobs(prefix=gcs_path, max_results=100))
            if blobs:
                filenames = [blob.name.split("/")[-1] for blob in blobs if blob.name.split("/")[-1]]
                return {
                    "exists": True,
                    "files_found": filenames,
                    "message": f"Found {len(filenames)} files"
                }
            time.sleep(2)  # Wait 2 seconds before retry
        
        return {
            "exists": False,
            "files_found": [],
            "message": f"No files found after {timeout_seconds}s timeout"
        }
    
    except Exception as e:
        return {
            "exists": False,
            "files_found": [],
            "message": f"Error checking GCS: {str(e)}"
        }


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

    params = pending_job.get("params", {})
    country = params.get("country", "unknown")
    revision = params.get("revision", "unknown")
    benchmark_variant = params.get("benchmark_variant", "")
    benchmark_test = params.get("benchmark_test", "")

    # Generate unique timestamp for this job
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[
        :19
    ]  # YYYYMMDD_HHMMSS_mmm

    logger.info(f"Processing job {pending_idx + 1}/{len(entries)}")
    logger.info(f"  Country: {country}")
    logger.info(f"  Revision: {revision}")
    if benchmark_variant:
        logger.info(f"  Benchmark variant: {benchmark_variant}")
    if benchmark_test:
        logger.info(f"  Benchmark test: {benchmark_test}")
    logger.info(f"  Job ID: {pending_job.get('job_id', 'N/A')}")

    # Log expected result location
    result_path = f"gs://{bucket_name}/robyn/{revision}/{country}/{timestamp}/"
    logger.info(f"📂 Results will be saved to:")
    logger.info(f"   {result_path}")
    logger.info(
        f"   Key files: model_summary.json, best_model_plots.png, console.log"
    )
    
    # Store benchmark info in job entry for better tracking
    if benchmark_variant:
        pending_job["benchmark_variant"] = benchmark_variant
    if benchmark_test:
        pending_job["benchmark_test"] = benchmark_test

    # Mark as LAUNCHING
    entries[pending_idx]["status"] = "LAUNCHING"
    entries[pending_idx]["launched_at"] = datetime.now(timezone.utc).isoformat()
    entries[pending_idx]["timestamp"] = timestamp
    entries[pending_idx]["expected_result_path"] = result_path

    # Save queue
    if not save_queue_to_gcs(
        bucket_name, queue_name, queue_doc, credentials=credentials
    ):
        logger.error("Failed to save queue")
        return False

    # Launch job with explicit timestamp
    config = {
        "country": params.get("country"),
        "revision": params.get("revision"),
        "data_gcs_path": params.get("data_gcs_path"),
        "gcs_bucket": bucket_name,
        "timestamp": timestamp,  # Pass explicit timestamp to R script
        "output_timestamp": timestamp,  # Pass for consistent result paths
    }
    
    # Log what we're passing to the job
    logger.info(f"📋 Job configuration:")
    logger.info(f"   country: {config['country']}")
    logger.info(f"   revision: {config['revision']}")
    logger.info(f"   timestamp: {timestamp}")
    logger.info(f"   data_gcs_path: {config['data_gcs_path']}")
    if benchmark_variant:
        logger.info(f"   benchmark_variant: {benchmark_variant}")

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
        save_queue_to_gcs(
            bucket_name, queue_name, queue_doc, credentials=credentials
        )
        logger.info("✅ Job launched successfully")
        logger.info(f"   Execution ID: {execution_name}")
        logger.info(f"")
        logger.info(f"💡 To check results when job completes:")
        logger.info(f"   gsutil ls {result_path}")
        logger.info(f"   gsutil cat {result_path}model_summary.json")
        return True
    else:
        # Mark as FAILED
        entries[pending_idx]["status"] = "FAILED"
        entries[pending_idx]["error"] = "Failed to launch Cloud Run job"
        save_queue_to_gcs(
            bucket_name, queue_name, queue_doc, credentials=credentials
        )
        logger.error("❌ Job launch failed")
        return False


def check_job_completion(
    execution_name: str,
    project_id: str,
    region: str,
    credentials=None,
) -> Optional[str]:
    """
    Check if a Cloud Run job execution has completed.

    Returns:
        "SUCCEEDED" if completed successfully
        "FAILED" if failed
        "RUNNING" if still running
        None if status cannot be determined
    """
    try:
        client = run_v2.ExecutionsClient(credentials=credentials)
        execution_path = execution_name

        execution = client.get_execution(name=execution_path)

        # Check completion condition
        if hasattr(execution, "completion_time") and execution.completion_time:
            # Job has completed
            if (
                hasattr(execution, "succeeded_count")
                and execution.succeeded_count > 0
            ):
                return "SUCCEEDED"
            else:
                return "FAILED"
        else:
            return "RUNNING"

    except Exception as e:
        logger.debug(f"Could not check execution status: {e}")
        return None


def cleanup_completed_jobs(
    queue_doc: Dict,
    bucket_name: str,
    queue_name: str,
    project_id: str,
    region: str,
    credentials=None,
    keep_count: int = 10,
) -> int:
    """
    Remove old completed/failed jobs from queue, keeping most recent ones.

    Returns:
        Number of jobs removed
    """
    entries = queue_doc.get("entries", [])

    # Separate by status
    completed = [
        e for e in entries if e.get("status") in ("COMPLETED", "FAILED")
    ]
    other = [
        e for e in entries if e.get("status") not in ("COMPLETED", "FAILED")
    ]

    if len(completed) <= keep_count:
        logger.info(
            f"No cleanup needed: {len(completed)} completed jobs (keep_count={keep_count})"
        )
        return 0

    # Sort completed by completion time (newest first)
    completed.sort(
        key=lambda x: x.get("completed_at", x.get("launched_at", "")),
        reverse=True,
    )

    # Keep only recent completed jobs
    to_keep = completed[:keep_count]
    to_remove = completed[keep_count:]

    # Update queue
    queue_doc["entries"] = other + to_keep

    if save_queue_to_gcs(
        bucket_name, queue_name, queue_doc, credentials=credentials
    ):
        logger.info(f"🧹 Cleaned up {len(to_remove)} old completed jobs")
        return len(to_remove)
    else:
        logger.error("Failed to save queue after cleanup")
        return 0


def update_running_jobs_status(
    queue_doc: Dict,
    bucket_name: str,
    queue_name: str,
    project_id: str,
    region: str,
    credentials=None,
) -> int:
    """
    Check status of RUNNING jobs and update to COMPLETED/FAILED if done.

    Returns:
        Number of jobs updated
    """
    entries = queue_doc.get("entries", [])
    updated = 0

    for idx, job in enumerate(entries):
        if job.get("status") != "RUNNING":
            continue

        execution_name = job.get("execution_name")
        if not execution_name:
            continue

        status = check_job_completion(
            execution_name, project_id, region, credentials
        )

        # Get job name for logging - check params for benchmark info
        params = job.get("params", {})
        job_name = (
            params.get("benchmark_variant")
            or job.get("benchmark_variant")
            or params.get("job_id")
            or job.get("job_id")
            or f"{params.get('country', 'unknown')}_{params.get('revision', 'unknown')}"
        )

        if status == "SUCCEEDED":
            entries[idx]["status"] = "COMPLETED"
            entries[idx]["completed_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            logger.info(f"✅ Job completed: {job_name}")
            if "expected_result_path" in entries[idx]:
                result_path = entries[idx]['expected_result_path']
                logger.info(f"   Results at: {result_path}")
                
                # Verify results exist
                logger.info(f"   Verifying results in GCS...")
                verification = verify_results_exist(
                    result_path, 
                    credentials=credentials,
                    timeout_seconds=10  # Quick check
                )
                
                if verification["exists"]:
                    logger.info(f"   ✓ Results verified: {verification['message']}")
                    key_files = ["model_summary.json", "best_model_plots.png", "console.log"]
                    found_key_files = [f for f in verification["files_found"] if f in key_files]
                    if found_key_files:
                        logger.info(f"   ✓ Key files found: {', '.join(found_key_files)}")
                    missing_key_files = [f for f in key_files if f not in verification["files_found"]]
                    if missing_key_files:
                        logger.warning(f"   ⚠️  Expected files missing: {', '.join(missing_key_files)}")
                else:
                    logger.warning(f"   ⚠️  {verification['message']}")
                    logger.warning(f"   ⚠️  Results may still be uploading or job may have failed")
                    logger.info(f"   💡 Manually check: gsutil ls {result_path}")
            else:
                logger.warning(f"   ⚠️  No expected_result_path recorded for this job")
            updated += 1
        elif status == "FAILED":
            entries[idx]["status"] = "FAILED"
            entries[idx]["completed_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            entries[idx]["error"] = "Cloud Run job execution failed"
            logger.warning(f"❌ Job failed: {job_name}")
            updated += 1

    if updated > 0:
        save_queue_to_gcs(
            bucket_name, queue_name, queue_doc, credentials=credentials
        )
        logger.info(f"Updated {updated} job status(es)")

    return updated


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
        queue_doc = load_queue_from_gcs(
            bucket_name, queue_name, credentials=credentials
        )
        if not queue_doc:
            logger.error("Failed to load queue")
            break

        # Resume queue if paused
        if not queue_doc.get("queue_running", True):
            logger.info("Queue is paused - resuming...")
            queue_doc["queue_running"] = True
            if not save_queue_to_gcs(
                bucket_name, queue_name, queue_doc, credentials=credentials
            ):
                logger.error("Failed to resume queue")
                break

        # Show status
        entries = queue_doc.get("entries", [])
        pending_count = sum(1 for j in entries if j.get("status") == "PENDING")
        running_count = sum(1 for j in entries if j.get("status") == "RUNNING")
        completed_count = sum(
            1 for j in entries if j.get("status") == "COMPLETED"
        )
        failed_count = sum(1 for j in entries if j.get("status") == "FAILED")

        logger.info(f"📊 Queue Status: {queue_name}")
        logger.info(f"  Total: {len(entries)}")
        logger.info(f"  Pending: {pending_count}")
        logger.info(f"  Running: {running_count}")
        logger.info(f"  Completed: {completed_count}")
        logger.info(f"  Failed: {failed_count}")

        # Update status of running jobs before proceeding
        if running_count > 0:
            logger.info("🔍 Checking status of running jobs...")
            update_running_jobs_status(
                queue_doc=queue_doc,
                bucket_name=bucket_name,
                queue_name=queue_name,
                project_id=project_id,
                region=region,
                credentials=credentials,
            )
            # Reload queue after updates
            queue_doc = load_queue_from_gcs(
                bucket_name, queue_name, credentials=credentials
            )
            entries = queue_doc.get("entries", [])
            pending_count = sum(
                1 for j in entries if j.get("status") == "PENDING"
            )

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
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Clean up old completed/failed jobs from queue",
    )
    parser.add_argument(
        "--keep-completed",
        type=int,
        default=10,
        help="Number of completed jobs to keep when cleaning up (default: 10)",
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
    logger.info(
        f"Mode: {'loop until empty' if args.loop else f'process {args.count} job(s)'}"
    )
    if args.cleanup:
        logger.info(
            f"Cleanup: Yes (keep {args.keep_completed} recent completed jobs)"
        )
    logger.info("=" * 60)

    try:
        # Perform cleanup if requested
        if args.cleanup:
            logger.info("🧹 Performing cleanup...")
            queue_doc = load_queue_from_gcs(
                args.bucket, args.queue_name, credentials=credentials
            )
            if queue_doc:
                cleanup_completed_jobs(
                    queue_doc=queue_doc,
                    bucket_name=args.bucket,
                    queue_name=args.queue_name,
                    project_id=args.project_id,
                    region=args.region,
                    credentials=credentials,
                    keep_count=args.keep_completed,
                )
            logger.info("")

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
