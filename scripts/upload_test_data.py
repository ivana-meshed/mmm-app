#!/usr/bin/env python3
"""
Upload test data to Google Cloud Storage.

This script uploads the generated test data to GCS bucket
while preserving the directory structure.
"""

import argparse
import logging
import os
from pathlib import Path
from typing import List, Optional

from google.cloud import storage

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def list_files_to_upload(source_dir: Path) -> List[Path]:
    """List all files to upload from source directory."""
    files = []
    for path in source_dir.rglob("*"):
        if path.is_file():
            files.append(path)
    return files


def upload_file_to_gcs(
    bucket_name: str,
    local_path: Path,
    source_dir: Path,
    prefix: str = "",
    dry_run: bool = False,
) -> None:
    """Upload a single file to GCS."""
    # Calculate relative path from source_dir
    relative_path = local_path.relative_to(source_dir)
    if prefix:
        gcs_path = os.path.join(prefix, str(relative_path)).replace("\\", "/")
    else:
        gcs_path = str(relative_path).replace("\\", "/")

    if dry_run:
        logger.info(f"[DRY RUN] Would upload: {local_path} -> {gcs_path}")
        return

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)

        # Determine content type
        content_type = None
        if local_path.suffix == ".json":
            content_type = "application/json"
        elif local_path.suffix == ".parquet":
            content_type = "application/octet-stream"
        elif local_path.suffix == ".csv":
            content_type = "text/csv"

        blob.upload_from_filename(str(local_path), content_type=content_type)
        logger.info(f"  Uploaded: {gcs_path}")
    except Exception as e:
        logger.error(f"  Failed to upload {local_path}: {e}")


def upload_directory(
    bucket_name: str,
    source_dir: Path,
    prefix: str = "",
    dry_run: bool = False,
) -> int:
    """Upload entire directory to GCS."""
    files = list_files_to_upload(source_dir)
    logger.info(f"Found {len(files)} files to upload")

    if dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE - No files will be uploaded")
        logger.info("=" * 60)

    uploaded = 0
    for file_path in files:
        upload_file_to_gcs(bucket_name, file_path, source_dir, prefix, dry_run)
        uploaded += 1

    return uploaded


def verify_gcs_access(bucket_name: str) -> bool:
    """Verify that we can access the GCS bucket."""
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        # Try to list one blob to verify access
        blobs = list(bucket.list_blobs(max_results=1))
        logger.info(f"✓ GCS access verified for bucket: {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"✗ Cannot access GCS bucket {bucket_name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Upload test data to GCS bucket"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--source-dir",
        default="test_data",
        help="Source directory with test data (default: test_data)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="GCS prefix/folder for test data (default: empty string - uploads to root paths)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without actually uploading",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    source_dir = Path(args.source_dir)

    if not source_dir.exists():
        logger.error(f"Source directory not found: {source_dir}")
        logger.error("Please run generate_test_data.py first")
        return 1

    logger.info(f"Source directory: {source_dir}")
    logger.info(f"Target bucket: {args.bucket}")
    if args.prefix:
        logger.info(f"GCS prefix: {args.prefix}")
    else:
        logger.info("GCS prefix: (none - uploading to root paths)")

    # Verify GCS access
    if not args.dry_run and not verify_gcs_access(args.bucket):
        return 1

    # Count files
    files = list_files_to_upload(source_dir)
    logger.info(f"\nReady to upload {len(files)} files")

    # Confirmation
    if not args.dry_run and not args.force:
        target_path = f"gs://{args.bucket}/{args.prefix}" if args.prefix else f"gs://{args.bucket}/"
        response = input(
            f"\nUpload {len(files)} files to {target_path}? (yes/no): "
        )
        if response.lower() not in ["yes", "y"]:
            logger.info("Upload cancelled")
            return 0

    # Upload
    logger.info("\nStarting upload...")
    uploaded = upload_directory(
        args.bucket, source_dir, args.prefix, args.dry_run
    )

    if args.dry_run:
        logger.info(f"\n[DRY RUN] Would have uploaded {uploaded} files")
        logger.info("Run without --dry-run to actually upload")
    else:
        logger.info(f"\n✅ Successfully uploaded {uploaded} files")
        target_path = f"gs://{args.bucket}/{args.prefix}/" if args.prefix else f"gs://{args.bucket}/"
        logger.info(f"   to {target_path}")


if __name__ == "__main__":
    main()
