#!/usr/bin/env python3
"""
Download test data from GCS bucket mmm-app-output.

This script downloads specific data for testing purposes:
- Data with timestamp "20251211_115528" (or close to it)
- ~3 latest examples for countries "de", "fr", "es"
- Files in folders/subfolders "latest" and "universal"
- From "robyn" folder: only data in folders starting with "r" (like r100, r101)

The folder/subfolder/naming structure is preserved when downloading.
"""

import argparse
import logging
import os
from pathlib import Path
from typing import List, Set

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TestDataDownloader:
    """Downloads test data from GCS bucket"""

    def __init__(
        self,
        bucket_name: str,
        output_dir: str = "./test_data",
        dry_run: bool = False,
    ):
        """
        Initialize the downloader

        Args:
            bucket_name: Name of the GCS bucket
            output_dir: Local directory to download files to
            dry_run: If True, only list files without downloading
        """
        self.bucket_name = bucket_name
        self.output_dir = Path(output_dir)
        self.dry_run = dry_run
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def download_blob(self, blob_name: str) -> bool:
        """
        Download a single blob from GCS

        Args:
            blob_name: Name of the blob to download

        Returns:
            True if successful, False otherwise
        """
        try:
            local_path = self.output_dir / blob_name

            # Check if file already exists
            if local_path.exists():
                logger.info(f"[SKIP] Already exists: {blob_name}")
                return True

            local_path.parent.mkdir(parents=True, exist_ok=True)

            if self.dry_run:
                logger.info(f"[DRY RUN] Would download: {blob_name}")
                return True

            blob = self.bucket.blob(blob_name)
            blob.download_to_filename(str(local_path))
            logger.info(f"Downloaded: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to download {blob_name}: {e}")
            return False

    def find_blobs_by_timestamp(
        self,
        target_timestamp: str = "20251211_115528",
        countries: List[str] = None,
    ) -> List[str]:
        """
        Find blobs with specific timestamp (or close to it)

        Searches across multiple folder structures:
        - robyn/{revision}/{country}/{timestamp}/
          (only revisions starting with 'r')
        - datasets/{country}/{timestamp}/
        - mapped-datasets/{country}/{timestamp}/
        - metadata/{country}/{timestamp}/
        - metadata/universal/{timestamp}/

        Only downloads for specified countries or 'latest'/'universal' folders.

        Args:
            target_timestamp: Target timestamp to search for
            countries: List of countries to filter (default: ['de', 'fr', 'es'])

        Returns:
            List of blob names
        """
        if countries is None:
            countries = ["de", "fr", "es"]

        logger.info(f"Searching for blobs with timestamp: {target_timestamp}")
        logger.info(f"  Filtering for countries: {countries}")
        blobs_to_download = []

        # Define all prefixes to search
        # We'll search the entire bucket to find timestamp folders
        prefixes_to_search = [
            "robyn/",
            "datasets/",
            "mapped-datasets/",
            "metadata/",
        ]

        for prefix in prefixes_to_search:
            logger.info(f"  Searching in {prefix}...")
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)

            for blob in blobs:
                parts = blob.name.split("/")

                # Handle different folder structures
                if parts[0] == "robyn":
                    # Structure: robyn/{revision}/{country}/{timestamp}/
                    # Only include revisions starting with 'r'
                    # and only for specified countries
                    if (
                        len(parts) >= 4
                        and parts[1].startswith("r")
                        and parts[2] in countries
                        and target_timestamp in parts[3]
                    ):
                        blobs_to_download.append(blob.name)

                elif parts[0] in ["datasets", "mapped-datasets"]:
                    # Structure: datasets/{country}/{timestamp}/
                    # Structure: mapped-datasets/{country}/{timestamp}/
                    # Only download for specified countries or 'latest'
                    if len(parts) >= 3 and target_timestamp in parts[2]:
                        country_or_latest = parts[1]
                        if (
                            country_or_latest in countries
                            or country_or_latest == "latest"
                        ):
                            blobs_to_download.append(blob.name)

                elif parts[0] == "metadata":
                    # Structure: metadata/{country}/{timestamp}/
                    # Structure: metadata/universal/{timestamp}/
                    # Only download for specified countries or 'universal'
                    if len(parts) >= 3 and target_timestamp in parts[2]:
                        country_or_universal = parts[1]
                        if (
                            country_or_universal in countries
                            or country_or_universal == "universal"
                        ):
                            blobs_to_download.append(blob.name)

        logger.info(
            f"Found {len(blobs_to_download)} blobs with timestamp "
            f"{target_timestamp}"
        )
        return blobs_to_download

    def find_latest_country_examples(
        self, countries: List[str] = None, limit: int = 3
    ) -> List[str]:
        """
        Find latest examples for specified countries

        Args:
            countries: List of country codes (e.g., ['de', 'fr', 'es'])
            limit: Number of latest examples per country

        Returns:
            List of blob names
        """
        if countries is None:
            countries = ["de", "fr", "es"]

        logger.info(
            f"Searching for latest {limit} examples for countries: "
            f"{countries}"
        )
        blobs_to_download = []

        for country in countries:
            # Search in robyn folder (only revisions starting with 'r')
            country_runs = {}
            prefix = "robyn/"
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)

            for blob in blobs:
                parts = blob.name.split("/")
                # Structure: robyn/{revision}/{country}/{timestamp}/
                if (
                    len(parts) >= 4
                    and parts[0] == "robyn"
                    and parts[1].startswith("r")
                    and parts[2] == country
                ):
                    timestamp = parts[3]
                    run_path = "/".join(parts[:4])
                    if run_path not in country_runs:
                        country_runs[run_path] = {
                            "timestamp": timestamp,
                            "blobs": [],
                        }
                    country_runs[run_path]["blobs"].append(blob.name)

            # Sort by timestamp and get latest N
            sorted_runs = sorted(
                country_runs.items(),
                key=lambda x: x[1]["timestamp"],
                reverse=True,
            )
            latest_runs = sorted_runs[:limit]

            for run_path, run_data in latest_runs:
                logger.info(
                    f"Including run: {run_path} "
                    f"({len(run_data['blobs'])} files)"
                )
                blobs_to_download.extend(run_data["blobs"])

        logger.info(
            f"Found {len(blobs_to_download)} blobs for country examples"
        )
        return blobs_to_download

    def find_latest_and_universal_blobs(
        self, countries: List[str] = None
    ) -> List[str]:
        """
        Find blobs in 'latest' and 'universal' folders

        Args:
            countries: List of countries to filter (default: ['de', 'fr', 'es'])

        Returns:
            List of blob names
        """
        if countries is None:
            countries = ["de", "fr", "es"]

        logger.info("Searching for 'latest' and 'universal' blobs")
        logger.info(f"  Filtering for countries: {countries}")
        blobs_to_download = []

        # Search for 'latest' folders in datasets and mapped-datasets
        for prefix in ["datasets/", "mapped-datasets/", "metadata/"]:
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
            for blob in blobs:
                parts = blob.name.split("/")

                # Check if 'latest' or 'universal' is in the path
                if "/latest/" in blob.name or "/universal/" in blob.name:
                    # For datasets and mapped-datasets, check country
                    if parts[0] in ["datasets", "mapped-datasets"]:
                        # Structure: datasets/{country}/latest/
                        if len(parts) >= 2 and (
                            parts[1] in countries or parts[1] == "latest"
                        ):
                            blobs_to_download.append(blob.name)
                    # For metadata, check for universal or country
                    elif parts[0] == "metadata":
                        # Structure: metadata/{country}/latest/
                        # or metadata/universal/...
                        if len(parts) >= 2 and (
                            parts[1] in countries or parts[1] == "universal"
                        ):
                            blobs_to_download.append(blob.name)

        logger.info(
            f"Found {len(blobs_to_download)} blobs in 'latest' "
            f"and 'universal' folders"
        )
        return blobs_to_download

    def download_test_data(self, target_timestamp: str = "20251211_115528"):
        """
        Download all test data based on criteria

        Args:
            target_timestamp: Target timestamp to search for
        """
        logger.info("=" * 60)
        logger.info("Starting test data download")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info(f"Output directory: {self.output_dir}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)

        # Define countries to filter
        countries = ["de", "fr", "es"]

        # Collect all blobs to download
        all_blobs: Set[str] = set()

        # 1. Find blobs with specific timestamp
        timestamp_blobs = self.find_blobs_by_timestamp(
            target_timestamp, countries=countries
        )
        all_blobs.update(timestamp_blobs)

        # 2. Find latest country examples
        country_blobs = self.find_latest_country_examples(
            countries=countries, limit=3
        )
        all_blobs.update(country_blobs)

        # 3. Find latest and universal blobs
        latest_universal_blobs = self.find_latest_and_universal_blobs(
            countries=countries
        )
        all_blobs.update(latest_universal_blobs)

        # Download all collected blobs
        logger.info("=" * 60)
        logger.info(f"Total unique blobs to download: {len(all_blobs)}")
        logger.info("=" * 60)

        success_count = 0
        failure_count = 0

        for blob_name in sorted(all_blobs):
            if self.download_blob(blob_name):
                success_count += 1
            else:
                failure_count += 1

        # Summary
        logger.info("=" * 60)
        logger.info("Download complete!")
        logger.info(f"Successfully downloaded: {success_count}")
        logger.info(f"Failed: {failure_count}")
        logger.info(f"Output directory: {self.output_dir}")
        logger.info("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Download test data from GCS bucket"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--output-dir",
        default="./test_data",
        help="Output directory for downloaded files (default: ./test_data)",
    )
    parser.add_argument(
        "--timestamp",
        default="20251211_115528",
        help="Target timestamp to search for (default: 20251211_115528)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list files without downloading",
    )

    args = parser.parse_args()

    downloader = TestDataDownloader(
        bucket_name=args.bucket,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
    downloader.download_test_data(target_timestamp=args.timestamp)


if __name__ == "__main__":
    main()
