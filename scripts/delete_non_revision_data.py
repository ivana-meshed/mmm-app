#!/usr/bin/env python3
"""
Delete GCS data except revisions starting with "r".

This script deletes all data from the GCS bucket except for
revision folders (e.g., r12, r24) to preserve important data.

IMPORTANT: This is a destructive operation. Always use --dry-run first!
"""

import argparse
import logging
import os
import re
from typing import List, Set

from google.cloud import storage

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def is_revision_path(blob_name: str) -> bool:
    """
    Check if blob path contains a revision folder (e.g., r12, r24).

    Returns True if path contains /rNN/ pattern where NN is one or more digits.
    """
    # Pattern: /r followed by digits followed by /
    pattern = r"/r\d+/"
    return bool(re.search(pattern, blob_name))


def list_blobs_to_delete(
    bucket_name: str, prefix: str = ""
) -> tuple[List[str], List[str]]:
    """
    List blobs to delete and blobs to keep.

    Returns:
        Tuple of (blobs_to_delete, blobs_to_keep)
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    logger.info(f"Scanning bucket: {bucket_name}")
    if prefix:
        logger.info(f"  with prefix: {prefix}")

    blobs = bucket.list_blobs(prefix=prefix)

    to_delete = []
    to_keep = []

    for blob in blobs:
        if is_revision_path(blob.name):
            to_keep.append(blob.name)
        else:
            to_delete.append(blob.name)

    return to_delete, to_keep


def delete_blobs(
    bucket_name: str, blob_names: List[str], dry_run: bool = False
) -> int:
    """
    Delete specified blobs from GCS.

    Returns number of blobs deleted (or would be deleted in dry-run mode).
    """
    if not blob_names:
        logger.info("No blobs to delete")
        return 0

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    deleted = 0
    for blob_name in blob_names:
        if dry_run:
            logger.info(f"[DRY RUN] Would delete: {blob_name}")
        else:
            try:
                blob = bucket.blob(blob_name)
                blob.delete()
                logger.info(f"  Deleted: {blob_name}")
                deleted += 1
            except Exception as e:
                logger.error(f"  Failed to delete {blob_name}: {e}")

    return deleted


def print_summary(
    to_delete: List[str], to_keep: List[str], dry_run: bool = False
) -> None:
    """Print summary of what will be deleted/kept."""
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    if dry_run:
        logger.info(f"Would DELETE: {len(to_delete)} blobs")
        logger.info(f"Would KEEP: {len(to_keep)} blobs (revision paths)")
    else:
        logger.info(f"To DELETE: {len(to_delete)} blobs")
        logger.info(f"To KEEP: {len(to_keep)} blobs (revision paths)")

    # Show examples of what will be kept
    if to_keep:
        logger.info("\nExample revision paths being KEPT:")
        for blob_name in sorted(to_keep)[:5]:
            logger.info(f"  ✓ {blob_name}")
        if len(to_keep) > 5:
            logger.info(f"  ... and {len(to_keep) - 5} more")

    # Show examples of what will be deleted
    if to_delete:
        logger.info("\nExample paths to be DELETED:")
        for blob_name in sorted(to_delete)[:10]:
            logger.info(f"  ✗ {blob_name}")
        if len(to_delete) > 10:
            logger.info(f"  ... and {len(to_delete) - 10} more")

    logger.info("=" * 60)


def extract_revision_folders(blob_names: List[str]) -> Set[str]:
    """Extract unique revision folder names from blob paths."""
    revisions = set()
    pattern = r"/r(\d+)/"

    for blob_name in blob_names:
        match = re.search(pattern, blob_name)
        if match:
            revisions.add(f"r{match.group(1)}")

    return revisions


def main():
    parser = argparse.ArgumentParser(
        description="Delete GCS data except revision folders (rNN)"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Only process blobs with this prefix (optional)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt (DANGEROUS!)",
    )
    parser.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="Additional safety flag required for non-dry-run",
    )

    args = parser.parse_args()

    # Safety check: require explicit confirmation flags for actual deletion
    if not args.dry_run and not args.yes_i_am_sure:
        logger.error("ERROR: For safety, you must include --yes-i-am-sure flag")
        logger.error("       to perform actual deletion.")
        logger.error("")
        logger.error("       Run with --dry-run first to preview changes.")
        return 1

    logger.info("GCS Data Cleanup Tool")
    logger.info("=" * 60)
    logger.info(f"Bucket: {args.bucket}")
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE DELETION'}")
    logger.info("")
    logger.info("Will KEEP: All paths containing /rNN/ (e.g., /r12/, /r24/)")
    logger.info("Will DELETE: Everything else")
    logger.info("=" * 60)

    # Verify GCS access
    try:
        client = storage.Client()
        bucket = client.bucket(args.bucket)
        # Try to list one blob to verify access
        list(bucket.list_blobs(max_results=1))
        logger.info(f"✓ GCS access verified for bucket: {args.bucket}\n")
    except Exception as e:
        logger.error(f"✗ Cannot access GCS bucket {args.bucket}: {e}")
        return 1

    # List blobs
    to_delete, to_keep = list_blobs_to_delete(args.bucket, args.prefix)

    # Show summary
    print_summary(to_delete, to_keep, args.dry_run)

    # Show revision folders being kept
    revisions = extract_revision_folders(to_keep)
    if revisions:
        logger.info(f"\nRevision folders being KEPT: {sorted(revisions)}")

    # Confirmation prompt
    if not args.dry_run and not args.force:
        logger.warning("\n⚠️  WARNING: This will permanently delete data!")
        logger.warning(f"   {len(to_delete)} blobs will be deleted")
        logger.warning(f"   {len(to_keep)} blobs will be kept")
        logger.warning("")
        response = input("Type 'DELETE' (in caps) to confirm deletion: ")
        if response != "DELETE":
            logger.info("Deletion cancelled")
            return 0

    # Perform deletion
    if to_delete:
        logger.info("\nStarting deletion...")
        deleted = delete_blobs(args.bucket, to_delete, args.dry_run)

        if args.dry_run:
            logger.info(f"\n[DRY RUN] Would have deleted {deleted} blobs")
            logger.info("Run without --dry-run to actually delete")
        else:
            logger.info(f"\n✅ Successfully deleted {deleted} blobs")
            logger.info(f"   Kept {len(to_keep)} revision blobs")
    else:
        logger.info("\n✓ No blobs to delete")

    return 0


if __name__ == "__main__":
    main()
