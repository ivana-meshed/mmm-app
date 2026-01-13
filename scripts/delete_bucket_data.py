#!/usr/bin/env python3
"""
Delete data from GCS bucket mmm-app-output.

This script deletes all data in the bucket EXCEPT:
- Folders in "robyn" that start with "r" (like r100, r101, etc.)

WARNING: This is a destructive operation. Use --dry-run first!
"""

import argparse
import logging
import os
from typing import List

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BucketDataCleaner:
    """Deletes data from GCS bucket with specific exclusion rules"""

    def __init__(self, bucket_name: str, dry_run: bool = True):
        """
        Initialize the cleaner

        Args:
            bucket_name: Name of the GCS bucket
            dry_run: If True, only list files without deleting
        """
        self.bucket_name = bucket_name
        self.dry_run = dry_run
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def should_keep_blob(self, blob_name: str) -> bool:
        """
        Determine if a blob should be kept (not deleted)

        Keep blobs in robyn folder with revision starting with 'r'

        Args:
            blob_name: Name of the blob

        Returns:
            True if blob should be kept, False if it should be deleted
        """
        parts = blob_name.split("/")

        # Keep blobs in robyn/{revision}/ where revision starts with 'r'
        if len(parts) >= 2 and parts[0] == "robyn":
            revision = parts[1]
            if revision.startswith("r"):
                return True

        return False

    def delete_blob(self, blob_name: str) -> bool:
        """
        Delete a single blob from GCS

        Args:
            blob_name: Name of the blob to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dry_run:
                logger.info(f"[DRY RUN] Would delete: {blob_name}")
                return True

            blob = self.bucket.blob(blob_name)
            blob.delete()
            logger.info(f"Deleted: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {blob_name}: {e}")
            return False

    def clean_bucket(self):
        """
        Clean the bucket by deleting all data except protected folders
        """
        logger.info("=" * 60)
        logger.info("Starting bucket cleanup")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)
        logger.info("Protection rules:")
        logger.info(
            "  - Keep: robyn/{revision}/* where revision starts with 'r'"
        )
        logger.info("  - Delete: Everything else")
        logger.info("=" * 60)

        if not self.dry_run:
            logger.warning("⚠️  THIS IS NOT A DRY RUN - FILES WILL BE DELETED!")
            logger.warning("⚠️  Press Ctrl+C within 5 seconds to cancel...")
            import time

            time.sleep(5)
            logger.info("Proceeding with deletion...")

        # List all blobs in the bucket
        logger.info("Listing all blobs in bucket...")
        blobs = list(self.client.list_blobs(self.bucket_name))
        total_blobs = len(blobs)
        logger.info(f"Found {total_blobs} blobs in bucket")

        # Categorize blobs
        blobs_to_keep: List[str] = []
        blobs_to_delete: List[str] = []

        for blob in blobs:
            if self.should_keep_blob(blob.name):
                blobs_to_keep.append(blob.name)
            else:
                blobs_to_delete.append(blob.name)

        logger.info("=" * 60)
        logger.info(f"Blobs to keep: {len(blobs_to_keep)}")
        logger.info(f"Blobs to delete: {len(blobs_to_delete)}")
        logger.info("=" * 60)

        # Show sample of blobs to keep
        if blobs_to_keep:
            logger.info("Sample of blobs to KEEP:")
            for blob_name in blobs_to_keep[:5]:
                logger.info(f"  ✓ {blob_name}")
            if len(blobs_to_keep) > 5:
                logger.info(f"  ... and {len(blobs_to_keep) - 5} more")
            logger.info("-" * 60)

        # Show sample of blobs to delete
        if blobs_to_delete:
            logger.info("Sample of blobs to DELETE:")
            for blob_name in blobs_to_delete[:5]:
                logger.info(f"  ✗ {blob_name}")
            if len(blobs_to_delete) > 5:
                logger.info(f"  ... and {len(blobs_to_delete) - 5} more")
            logger.info("-" * 60)

        # Perform deletion
        success_count = 0
        failure_count = 0

        for blob_name in blobs_to_delete:
            if self.delete_blob(blob_name):
                success_count += 1
            else:
                failure_count += 1

        # Summary
        logger.info("=" * 60)
        logger.info("Cleanup complete!")
        logger.info(f"Total blobs processed: {total_blobs}")
        logger.info(f"Kept (protected): {len(blobs_to_keep)}")
        logger.info(f"Successfully deleted: {success_count}")
        logger.info(f"Failed to delete: {failure_count}")
        logger.info("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Delete data from GCS bucket (except protected folders)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WARNING: This is a destructive operation!

Protected folders (will NOT be deleted):
  - robyn/{revision}/* where revision starts with 'r' (e.g., r100, r101)

Everything else WILL BE DELETED!

Always use --dry-run first to verify what will be deleted.

Examples:
  # Dry run (safe, only lists what would be deleted)
  python scripts/delete_bucket_data.py --dry-run

  # Actually delete data (destructive!)
  python scripts/delete_bucket_data.py --no-dry-run
        """,
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Only list files without deleting (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually delete files (use with caution!)",
    )

    args = parser.parse_args()

    if not args.dry_run:
        logger.warning("=" * 60)
        logger.warning("⚠️  WARNING: DRY RUN IS DISABLED!")
        logger.warning("⚠️  FILES WILL BE PERMANENTLY DELETED!")
        logger.warning("=" * 60)

    cleaner = BucketDataCleaner(bucket_name=args.bucket, dry_run=args.dry_run)
    cleaner.clean_bucket()


if __name__ == "__main__":
    main()
