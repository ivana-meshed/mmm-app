#!/usr/bin/env python3
"""
Backfill parquet extraction for existing OutputCollect.RDS files in GCS.

This script scans GCS for existing model runs that have OutputCollect.RDS but 
don't have the extracted parquet files, then runs the R extraction script on them.
"""

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def list_model_runs_needing_extraction(bucket_name, country=None, revision=None):
    """
    List all model runs in GCS that need parquet extraction.
    
    Args:
        bucket_name: GCS bucket name
        country: Optional country filter
        revision: Optional revision filter
        
    Returns:
        List of tuples (blob_prefix, has_parquet_data)
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    
    # Build prefix based on filters
    prefix = "robyn/"
    if revision and country:
        # Both specified: robyn/{revision}/{country}/
        prefix = f"robyn/{revision}/{country.lower()}/"
    elif revision:
        # Only revision: robyn/{revision}/
        prefix = f"robyn/{revision}/"
    elif country:
        # Only country: scan all revisions, filter by country in the loop
        prefix = "robyn/"
    
    logger.info(f"Scanning GCS bucket '{bucket_name}' with prefix '{prefix}'...")
    if country and not revision:
        logger.info(f"Will filter results by country: {country}")
    
    # Find all OutputCollect.RDS files (not OutputModels.RDS!)
    model_runs = []
    blobs = bucket.list_blobs(prefix=prefix)
    
    for blob in blobs:
        if blob.name.endswith("OutputCollect.RDS"):
            # Extract the run prefix (everything before OutputCollect.RDS)
            run_prefix = blob.name.rsplit("/", 1)[0]
            
            # Apply country filter if specified and not already in prefix
            if country and not revision:
                # Extract country from path: robyn/{revision}/{country}/{timestamp}
                path_parts = run_prefix.split("/")
                if len(path_parts) >= 3:
                    run_country = path_parts[2].lower()
                    if run_country != country.lower():
                        continue
                else:
                    # Path doesn't match expected structure, skip filtering for this run
                    logger.debug(f"Skipping country filter for unexpected path: {run_prefix}")
            
            # Check if parquet data already exists
            parquet_dir = f"{run_prefix}/output_models_data/"
            has_parquet = any(
                b.name.startswith(parquet_dir) and b.name.endswith(".parquet")
                for b in bucket.list_blobs(prefix=parquet_dir, max_results=1)
            )
            
            if not has_parquet:
                model_runs.append((run_prefix, blob.name))
                
    logger.info(f"Found {len(model_runs)} model runs needing parquet extraction")
    return model_runs


def extract_parquet_for_run(bucket_name, run_prefix, rds_blob_name):
    """
    Extract parquet data for a single model run.
    
    Args:
        bucket_name: GCS bucket name
        run_prefix: GCS path prefix for the run (e.g., robyn/v1/US/123456)
        rds_blob_name: Full blob name for OutputCollect.RDS
        
    Returns:
        True if successful, False otherwise
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    
    logger.info(f"Processing: {run_prefix}")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download OutputCollect.RDS
        local_rds = os.path.join(tmpdir, "OutputCollect.RDS")
        blob = bucket.blob(rds_blob_name)
        
        try:
            logger.info(f"  Downloading {rds_blob_name}...")
            blob.download_to_filename(local_rds)
        except Exception as e:
            logger.error(f"  Failed to download {rds_blob_name}: {e}")
            return False
        
        # Create output directory
        output_dir = os.path.join(tmpdir, "output_models_data")
        os.makedirs(output_dir, exist_ok=True)
        
        # Find the R extraction script
        script_dir = Path(__file__).parent.parent / "r"
        r_script = script_dir / "extract_output_models_data.R"
        
        if not r_script.exists():
            logger.error(f"  R script not found at: {r_script}")
            return False
        
        # Run R extraction script
        cmd = [
            "Rscript",
            str(r_script),
            "--input", local_rds,
            "--output", output_dir
        ]
        
        try:
            logger.info(f"  Running R extraction script...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"  R script output:\n{result.stdout}")
            if result.stderr:
                logger.warning(f"  R script stderr:\n{result.stderr}")
        except subprocess.CalledProcessError as e:
            logger.error(f"  R script failed: {e}")
            logger.error(f"  stdout: {e.stdout}")
            logger.error(f"  stderr: {e.stderr}")
            return False
        
        # Upload parquet files to GCS
        parquet_files = list(Path(output_dir).glob("*.parquet"))
        
        if not parquet_files:
            logger.warning(f"  No parquet files were created")
            return False
        
        logger.info(f"  Uploading {len(parquet_files)} parquet files to GCS...")
        for parquet_file in parquet_files:
            gcs_path = f"{run_prefix}/output_models_data/{parquet_file.name}"
            blob = bucket.blob(gcs_path)
            
            try:
                blob.upload_from_filename(str(parquet_file))
                logger.info(f"    Uploaded: {gcs_path}")
            except Exception as e:
                logger.error(f"    Failed to upload {gcs_path}: {e}")
                return False
        
        logger.info(f"✅ Successfully processed: {run_prefix}")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Backfill parquet extraction for existing OutputCollect.RDS files"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name"
    )
    parser.add_argument(
        "--country",
        help="Filter by country code"
    )
    parser.add_argument(
        "--revision",
        help="Filter by revision"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List runs that need extraction without processing them"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of runs to process"
    )
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("OutputModels Parquet Backfill Script")
    logger.info("=" * 60)
    logger.info(f"Bucket: {args.bucket}")
    if args.country:
        logger.info(f"Country filter: {args.country}")
    if args.revision:
        logger.info(f"Revision filter: {args.revision}")
    if args.dry_run:
        logger.info("DRY RUN MODE - no changes will be made")
    if args.limit:
        logger.info(f"Limit: {args.limit} runs")
    logger.info("=" * 60)
    
    # Find runs needing extraction
    runs_to_process = list_model_runs_needing_extraction(
        args.bucket,
        country=args.country,
        revision=args.revision
    )
    
    if not runs_to_process:
        logger.info("No runs need parquet extraction. All done!")
        return 0
    
    if args.dry_run:
        logger.info("\nRuns that would be processed:")
        for run_prefix, rds_blob in runs_to_process:
            logger.info(f"  - {run_prefix}")
        logger.info(f"\nTotal: {len(runs_to_process)} runs")
        return 0
    
    # Apply limit if specified
    if args.limit:
        runs_to_process = runs_to_process[:args.limit]
        logger.info(f"Processing first {len(runs_to_process)} runs (limited)")
    
    # Process each run
    success_count = 0
    failure_count = 0
    
    for i, (run_prefix, rds_blob) in enumerate(runs_to_process, 1):
        logger.info(f"\n[{i}/{len(runs_to_process)}] Processing run...")
        
        if extract_parquet_for_run(args.bucket, run_prefix, rds_blob):
            success_count += 1
        else:
            failure_count += 1
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 60)
    logger.info(f"✅ Successful: {success_count}")
    logger.info(f"❌ Failed: {failure_count}")
    logger.info(f"📊 Total: {len(runs_to_process)}")
    logger.info("=" * 60)
    
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
