#!/usr/bin/env python3
"""
Upload test data to GCS bucket mmm-app-output.

This script uploads downloaded test data back to GCS:
- Maintains the same folder/subfolder/naming structure
- Skips files that already exist on GCS (no overwrite)
"""

import argparse
import logging
import os
from pathlib import Path
from typing import List

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TestDataUploader:
    """Uploads test data to GCS bucket"""

    def __init__(
        self,
        bucket_name: str,
        input_dir: str = "./test_data",
        dry_run: bool = False,
        skip_existing: bool = True,
        prefix: str = "",
    ):
        """
        Initialize the uploader

        Args:
            bucket_name: Name of the GCS bucket
            input_dir: Local directory containing files to upload
            dry_run: If True, only list files without uploading
            skip_existing: If True, skip files that already exist on GCS
            prefix: Optional prefix to prepend to all uploaded files
        """
        self.bucket_name = bucket_name
        self.input_dir = Path(input_dir)
        self.dry_run = dry_run
        self.skip_existing = skip_existing
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
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

    def upload_file(self, local_path: Path, blob_name: str) -> bool:
        """
        Upload a single file to GCS

        Args:
            local_path: Local file path
            blob_name: Destination blob name in GCS

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if blob already exists
            if self.skip_existing and self.blob_exists(blob_name):
                logger.info(f"[SKIP] Already exists: {blob_name}")
                return True

            if self.dry_run:
                logger.info(f"[DRY RUN] Would upload: {blob_name}")
                return True

            blob = self.bucket.blob(blob_name)
            blob.upload_from_filename(str(local_path))
            logger.info(f"Uploaded: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload {blob_name}: {e}")
            return False

    def find_local_files(self) -> List[Path]:
        """
        Find all files in the input directory

        Returns:
            List of file paths
        """
        if not self.input_dir.exists():
            logger.error(f"Input directory does not exist: {self.input_dir}")
            return []

        files = []
        for root, _, filenames in os.walk(self.input_dir):
            for filename in filenames:
                file_path = Path(root) / filename
                files.append(file_path)

        return files

    def upload_test_data(self):
        """
        Upload all test data to GCS
        """
        logger.info("=" * 60)
        logger.info("Starting test data upload")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info(f"Input directory: {self.input_dir}")
        logger.info(f"Prefix: {self.prefix if self.prefix else '(root)'}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"Skip existing: {self.skip_existing}")
        logger.info("=" * 60)

        # Find all local files
        local_files = self.find_local_files()
        logger.info(f"Found {len(local_files)} local files to upload")

        if not local_files:
            logger.warning("No files found to upload!")
            return

        # Upload files
        success_count = 0
        skip_count = 0
        failure_count = 0

        for local_path in sorted(local_files):
            # Calculate blob name (relative path from input_dir)
            relative_path = local_path.relative_to(self.input_dir)
            blob_name = self.prefix + str(relative_path).replace(os.sep, "/")

            # Check if already exists before attempting upload
            if self.skip_existing and not self.dry_run:
                if self.blob_exists(blob_name):
                    logger.info(f"[SKIP] Already exists: {blob_name}")
                    skip_count += 1
                    continue

            if self.upload_file(local_path, blob_name):
                if self.skip_existing and not self.dry_run:
                    # Already counted as skip if it existed
                    if not self.blob_exists(blob_name):
                        success_count += 1
                else:
                    success_count += 1
            else:
                failure_count += 1

        # Summary
        logger.info("=" * 60)
        logger.info("Upload complete!")
        logger.info(f"Total files processed: {len(local_files)}")
        logger.info(f"Successfully uploaded: {success_count}")
        logger.info(f"Skipped (already exist): {skip_count}")
        logger.info(f"Failed: {failure_count}")
        logger.info("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Upload test data to GCS bucket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (safe, only lists what would be uploaded)
  python scripts/upload_test_data.py --dry-run

  # Upload with skipping existing files (default)
  python scripts/upload_test_data.py

  # Upload to TEST folder
  python scripts/upload_test_data.py --prefix TEST

  # Force upload (overwrite existing files)
  python scripts/upload_test_data.py --no-skip-existing

  # Upload from custom directory
  python scripts/upload_test_data.py --input-dir /path/to/data
        """,
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--input-dir",
        default="./test_data",
        help="Input directory containing files to upload "
        "(default: ./test_data)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional prefix to prepend to all uploaded files "
        "(e.g., TEST to upload to TEST/ folder)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list files without uploading",
    )
    parser.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=True,
        help="Skip files that already exist on GCS (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Overwrite files that already exist on GCS",
    )

    args = parser.parse_args()

    uploader = TestDataUploader(
        bucket_name=args.bucket,
        input_dir=args.input_dir,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        prefix=args.prefix,
    )
    uploader.upload_test_data()


if __name__ == "__main__":
    main()
