#!/usr/bin/env python3
"""
Script to delete corrupt parquet files from GCS datasets/ directory.

This script identifies and deletes parquet files that were created with
database-specific types (dbdate, dbtime, etc.) before the fix was applied.
These files cannot be read by pandas and should be deleted.

Usage:
    python scripts/cleanup_corrupt_parquet.py [--bucket BUCKET] [--date DATE] [--dry-run]

Arguments:
    --bucket: GCS bucket name (default: mmm-app-output)
    --date: Date to filter files (YYYYMMDD format, default: today)
    --dry-run: List files that would be deleted without actually deleting them
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import List

from google.cloud import storage

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Delete corrupt parquet files from GCS datasets/ directory"
    )
    parser.add_argument(
        "--bucket",
        type=str,
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=datetime.now().strftime("%Y%m%d"),
        help="Date to filter files (YYYYMMDD format, default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files without deleting them",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="datasets/",
        help="GCS prefix to search (default: datasets/)",
    )
    return parser.parse_args()


def list_files_with_date(
    bucket_name: str, prefix: str, date_str: str
) -> List[str]:
    """
    List all files in GCS bucket with matching date pattern.

    Args:
        bucket_name: Name of the GCS bucket
        prefix: Prefix path to search (e.g., "datasets/")
        date_str: Date string in YYYYMMDD format

    Returns:
        List of blob names matching the date pattern
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # List all blobs with the prefix
    blobs = bucket.list_blobs(prefix=prefix)

    matching_files = []
    for blob in blobs:
        # Check if the blob name contains the date string
        # Expected pattern: datasets/{country}/{YYYYMMDD_HHMMSS}/raw.parquet
        if date_str in blob.name and blob.name.endswith(".parquet"):
            matching_files.append(blob.name)

    return matching_files


def delete_files(
    bucket_name: str, file_paths: List[str], dry_run: bool = False
):
    """
    Delete files from GCS bucket.

    Args:
        bucket_name: Name of the GCS bucket
        file_paths: List of file paths to delete
        dry_run: If True, only log what would be deleted
    """
    if not file_paths:
        logger.info("No files found to delete.")
        return

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    logger.info(f"Found {len(file_paths)} file(s) matching the criteria:")
    for path in file_paths:
        logger.info(f"  - {path}")

    if dry_run:
        logger.info("\n[DRY RUN] No files were deleted.")
        return

    # Ask for confirmation
    response = input(
        f"\nAre you sure you want to delete {len(file_paths)} file(s)? (yes/no): "
    )
    if response.lower() != "yes":
        logger.info("Deletion cancelled.")
        return

    # Delete files
    deleted_count = 0
    failed_count = 0

    for path in file_paths:
        try:
            blob = bucket.blob(path)
            blob.delete()
            logger.info(f"✓ Deleted: {path}")
            deleted_count += 1
        except Exception as e:
            logger.error(f"✗ Failed to delete {path}: {e}")
            failed_count += 1

    logger.info(
        f"\nDeletion complete: {deleted_count} deleted, {failed_count} failed"
    )


def main():
    """Main function."""
    args = parse_args()

    logger.info("=" * 80)
    logger.info("Corrupt Parquet File Cleanup Script")
    logger.info("=" * 80)
    logger.info(f"Bucket: {args.bucket}")
    logger.info(f"Prefix: {args.prefix}")
    logger.info(f"Date filter: {args.date}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info("=" * 80)

    try:
        # List files
        logger.info("\nSearching for files...")
        files = list_files_with_date(args.bucket, args.prefix, args.date)

        # Delete files
        delete_files(args.bucket, files, dry_run=args.dry_run)

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
