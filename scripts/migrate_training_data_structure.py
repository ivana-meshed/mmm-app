#!/usr/bin/env python3
"""
Migration script to restructure training data on GCS.

Old structure: training_data/{country}/{timestamp}/selected_columns.json
New structure: training_data/{country}/{goal}/{timestamp}/selected_columns.json

This script:
1. Lists all files in old format
2. Reads each JSON file to extract the goal
3. Copies file to new location
4. Optionally deletes old file after successful copy
"""

import json
import logging
import sys
from typing import Dict, List, Optional

from google.cloud import storage

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def list_old_format_files(bucket_name: str) -> List[Dict[str, str]]:
    """List all training data files in old format.

    Returns list of dicts with keys: path, country, timestamp
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    prefix = "training_data/"
    blobs = bucket.list_blobs(prefix=prefix, delimiter=None)

    old_format_files = []

    for blob in blobs:
        if blob.name.endswith("selected_columns.json"):
            parts = blob.name.split("/")
            # Old format: training_data/<country>/<timestamp>/selected_columns.json (4 parts)
            # New format: training_data/<country>/<goal>/<timestamp>/selected_columns.json (5 parts)
            if len(parts) == 4:
                old_format_files.append(
                    {
                        "path": blob.name,
                        "country": parts[1],
                        "timestamp": parts[2],
                    }
                )

    return old_format_files


def extract_goal_from_json(bucket_name: str, blob_path: str) -> Optional[str]:
    """Extract the selected_goal from a JSON file on GCS."""
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        if not blob.exists():
            logger.warning(f"Blob does not exist: {blob_path}")
            return None

        content = blob.download_as_bytes()
        data = json.loads(content)

        goal = data.get("selected_goal")
        if not goal:
            logger.warning(f"No selected_goal found in {blob_path}")
            return None

        return goal
    except Exception as e:
        logger.error(f"Error extracting goal from {blob_path}: {e}")
        return None


def migrate_file(
    bucket_name: str,
    old_path: str,
    country: str,
    goal: str,
    timestamp: str,
    delete_old: bool = False,
) -> bool:
    """Migrate a single file from old to new structure.

    Args:
        bucket_name: GCS bucket name
        old_path: Old file path
        country: Country code
        goal: Goal name
        timestamp: Timestamp
        delete_old: Whether to delete old file after successful copy

    Returns:
        True if migration successful, False otherwise
    """
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        # New path structure
        new_path = (
            f"training_data/{country}/{goal}/{timestamp}/selected_columns.json"
        )

        # Check if new path already exists
        new_blob = bucket.blob(new_path)
        if new_blob.exists():
            logger.warning(
                f"New path already exists: {new_path}. Skipping migration."
            )
            return True

        # Copy file to new location
        old_blob = bucket.blob(old_path)
        bucket.copy_blob(old_blob, bucket, new_path)

        logger.info(f"✅ Migrated: {old_path} → {new_path}")

        # Delete old file if requested
        if delete_old:
            old_blob.delete()
            logger.info(f"🗑️  Deleted old file: {old_path}")

        return True
    except Exception as e:
        logger.error(f"❌ Error migrating {old_path}: {e}")
        return False


def migrate_all_training_data(
    bucket_name: str, dry_run: bool = True, delete_old: bool = False
) -> None:
    """Migrate all training data files from old to new structure.

    Args:
        bucket_name: GCS bucket name
        dry_run: If True, only show what would be done without making changes
        delete_old: If True, delete old files after successful migration
    """
    logger.info(f"Starting migration for bucket: {bucket_name}")
    logger.info(f"Dry run: {dry_run}")
    logger.info(f"Delete old files: {delete_old}")

    # List all old format files
    old_files = list_old_format_files(bucket_name)
    logger.info(f"Found {len(old_files)} files in old format")

    if not old_files:
        logger.info("No files to migrate. Migration complete.")
        return

    # Migrate each file
    successful = 0
    failed = 0
    skipped = 0

    for file_info in old_files:
        old_path = file_info["path"]
        country = file_info["country"]
        timestamp = file_info["timestamp"]

        # Extract goal from JSON
        goal = extract_goal_from_json(bucket_name, old_path)

        if not goal:
            logger.warning(f"⚠️  Skipping {old_path}: could not extract goal")
            skipped += 1
            continue

        logger.info(
            f"Processing: {old_path} (country={country}, goal={goal}, timestamp={timestamp})"
        )

        if dry_run:
            new_path = f"training_data/{country}/{goal}/{timestamp}/selected_columns.json"
            logger.info(f"  [DRY RUN] Would migrate to: {new_path}")
            successful += 1
        else:
            if migrate_file(
                bucket_name, old_path, country, goal, timestamp, delete_old
            ):
                successful += 1
            else:
                failed += 1

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total files found: {len(old_files)}")
    logger.info(f"Successfully migrated: {successful}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Skipped (no goal): {skipped}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("\nThis was a DRY RUN. No changes were made to GCS.")
        logger.info(
            "To perform the actual migration, run with --no-dry-run flag."
        )


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate training data from old to new GCS structure"
    )
    parser.add_argument(
        "--bucket",
        default="mmm-app-output",
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually perform the migration (default is dry-run mode)",
    )
    parser.add_argument(
        "--delete-old",
        action="store_true",
        help="Delete old files after successful migration",
    )

    args = parser.parse_args()

    dry_run = not args.no_dry_run

    try:
        migrate_all_training_data(
            args.bucket, dry_run=dry_run, delete_old=args.delete_old
        )
    except KeyboardInterrupt:
        logger.info("\nMigration interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
