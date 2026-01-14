#!/usr/bin/env python3
"""
Copy data from TEST folder to root in GCS bucket.

This script copies all files from the TEST/ folder to the root of the
mmm-app-output bucket, maintaining the same folder structure.
"""

import argparse
import logging
import os

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TestFolderCopier:
    """Copies data from TEST/ folder to bucket root"""

    def __init__(
        self, bucket_name: str, dry_run: bool = False, overwrite: bool = False
    ):
        """
        Initialize the copier

        Args:
            bucket_name: Name of the GCS bucket
            dry_run: If True, only list files without copying
            overwrite: If True, overwrite existing files in destination
        """
        self.bucket_name = bucket_name
        self.dry_run = dry_run
        self.overwrite = overwrite
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def blob_exists(self, blob_name: str) -> bool:
        """
        Check if a blob exists in GCS

        Args:
            blob_name: Name of the blob

        Returns:
            True if blob exists, False otherwise
        """
        try:
            blob = self.bucket.blob(blob_name)
            return blob.exists()
        except Exception as e:
            logger.error(f"Failed to check existence of {blob_name}: {e}")
            return False

    def copy_blob(self, source_blob_name: str, dest_blob_name: str) -> bool:
        """
        Copy a single blob within the same bucket

        Args:
            source_blob_name: Source blob path
            dest_blob_name: Destination blob path

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if destination already exists
            if not self.overwrite and self.blob_exists(dest_blob_name):
                logger.info(f"[SKIP] Already exists: {dest_blob_name}")
                return True

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Would copy: {source_blob_name} "
                    f"-> {dest_blob_name}"
                )
                return True

            source_blob = self.bucket.blob(source_blob_name)
            self.bucket.copy_blob(source_blob, self.bucket, dest_blob_name)
            logger.info(f"Copied: {source_blob_name} -> {dest_blob_name}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to copy {source_blob_name} to {dest_blob_name}: {e}"
            )
            return False

    def copy_test_to_root(self):
        """
        Copy all files from TEST/ folder to bucket root
        """
        logger.info("=" * 60)
        logger.info("Starting TEST folder copy to root")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info("Source: TEST/")
        logger.info("Destination: (root)")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"Overwrite: {self.overwrite}")
        logger.info("=" * 60)

        # List all blobs in TEST/ folder
        logger.info("Listing files in TEST/ folder...")
        test_blobs = list(
            self.client.list_blobs(self.bucket_name, prefix="TEST/")
        )

        if not test_blobs:
            logger.warning("No files found in TEST/ folder!")
            return

        logger.info(f"Found {len(test_blobs)} files in TEST/ folder")

        # Copy files
        success_count = 0
        skip_count = 0
        failure_count = 0

        for blob in test_blobs:
            source_name = blob.name

            # Skip if it's a directory marker
            if source_name.endswith("/"):
                continue

            # Calculate destination path (remove TEST/ prefix)
            if not source_name.startswith("TEST/"):
                logger.warning(f"Unexpected blob name: {source_name}")
                continue

            dest_name = source_name[5:]  # Remove "TEST/" prefix

            # Check if already exists before attempting copy
            if not self.overwrite and not self.dry_run:
                if self.blob_exists(dest_name):
                    logger.info(f"[SKIP] Already exists: {dest_name}")
                    skip_count += 1
                    continue

            if self.copy_blob(source_name, dest_name):
                success_count += 1
            else:
                failure_count += 1

        # Summary
        logger.info("=" * 60)
        logger.info("Copy complete!")
        logger.info(f"Total files processed: {len(test_blobs)}")
        logger.info(f"Successfully copied: {success_count}")
        logger.info(f"Skipped (already exist): {skip_count}")
        logger.info(f"Failed: {failure_count}")
        logger.info("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Copy data from TEST/ folder to bucket root",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (safe, only lists what would be copied)
  python scripts/copy_test_to_root.py --dry-run

  # Copy files (skip existing)
  python scripts/copy_test_to_root.py

  # Copy and overwrite existing files
  python scripts/copy_test_to_root.py --overwrite

  # Copy from different bucket
  python scripts/copy_test_to_root.py --bucket my-bucket-name
        """,
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list files without copying",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files in destination (default: False)",
    )

    args = parser.parse_args()

    copier = TestFolderCopier(
        bucket_name=args.bucket,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )
    copier.copy_test_to_root()


if __name__ == "__main__":
    main()
