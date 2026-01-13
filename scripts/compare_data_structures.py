#!/usr/bin/env python3
"""
Compare generated test data structure with actual GCS data.

This script verifies that generated test data matches the structure
of data in the GCS bucket to ensure compatibility.
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pandas as pd
from google.cloud import storage

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class StructureComparison:
    """Compare local and GCS data structures."""

    def __init__(self, bucket_name: str, local_dir: Path):
        self.bucket_name = bucket_name
        self.local_dir = local_dir
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.issues = []
        self.warnings = []
        self.matches = []

    def get_local_structure(self) -> Dict[str, List[str]]:
        """Get structure of local test data."""
        structure = {}
        for path in self.local_dir.rglob("*"):
            if path.is_file():
                rel_path = path.relative_to(self.local_dir)
                folder = str(rel_path.parent)
                if folder not in structure:
                    structure[folder] = []
                structure[folder].append(path.name)
        return structure

    def get_gcs_structure(
        self, prefix: str = "", max_samples: int = 10
    ) -> Dict[str, List[str]]:
        """Get structure of GCS bucket data."""
        structure = {}
        blobs = self.bucket.list_blobs(prefix=prefix)

        seen_folders = set()
        for blob in blobs:
            if blob.name.endswith("/"):
                continue

            parts = blob.name.split("/")
            if len(parts) >= 2:
                # Group by folder pattern
                folder = "/".join(parts[:-1])
                filename = parts[-1]

                if folder not in seen_folders:
                    seen_folders.add(folder)
                    if len(seen_folders) > max_samples:
                        break

                if folder not in structure:
                    structure[folder] = []
                if filename not in structure[folder]:
                    structure[folder].append(filename)

        return structure

    def compare_parquet_schemas(
        self, local_path: Path, gcs_path: str
    ) -> Tuple[bool, str]:
        """Compare schemas of local and GCS parquet files."""
        try:
            # Read local parquet
            local_df = pd.read_parquet(local_path)
            local_schema = {
                col: str(dtype) for col, dtype in local_df.dtypes.items()
            }

            # Download and read GCS parquet
            blob = self.bucket.blob(gcs_path)
            temp_path = f"/tmp/{Path(gcs_path).name}"
            blob.download_to_filename(temp_path)
            gcs_df = pd.read_parquet(temp_path)
            gcs_schema = {
                col: str(dtype) for col, dtype in gcs_df.dtypes.items()
            }
            os.remove(temp_path)

            # Compare schemas
            local_cols = set(local_schema.keys())
            gcs_cols = set(gcs_schema.keys())

            if local_cols == gcs_cols:
                # Check data types
                dtype_mismatches = []
                for col in local_cols:
                    if local_schema[col] != gcs_schema[col]:
                        dtype_mismatches.append(
                            f"{col}: {local_schema[col]} vs {gcs_schema[col]}"
                        )

                if dtype_mismatches:
                    return (
                        False,
                        f"Data type mismatches: {', '.join(dtype_mismatches)}",
                    )
                return True, "Schemas match"
            else:
                missing_in_local = gcs_cols - local_cols
                missing_in_gcs = local_cols - gcs_cols
                msg = []
                if missing_in_local:
                    msg.append(
                        f"Missing in local: {', '.join(missing_in_local)}"
                    )
                if missing_in_gcs:
                    msg.append(
                        f"Missing in GCS: {', '.join(missing_in_gcs)}"
                    )
                return False, "; ".join(msg)

        except Exception as e:
            return False, f"Error comparing schemas: {e}"

    def compare_json_structure(
        self, local_path: Path, gcs_path: str
    ) -> Tuple[bool, str]:
        """Compare structure of local and GCS JSON files."""
        try:
            # Read local JSON
            with open(local_path, "r") as f:
                local_data = json.load(f)

            # Download and read GCS JSON
            blob = self.bucket.blob(gcs_path)
            gcs_data = json.loads(blob.download_as_text())

            # Compare keys at top level
            local_keys = set(
                local_data.keys() if isinstance(local_data, dict) else []
            )
            gcs_keys = set(
                gcs_data.keys() if isinstance(gcs_data, dict) else []
            )

            if local_keys == gcs_keys:
                return True, "JSON structures match"
            else:
                missing_in_local = gcs_keys - local_keys
                missing_in_gcs = local_keys - gcs_keys
                msg = []
                if missing_in_local:
                    msg.append(
                        f"Missing keys in local: {', '.join(missing_in_local)}"
                    )
                if missing_in_gcs:
                    msg.append(
                        f"Extra keys in local: {', '.join(missing_in_gcs)}"
                    )
                return False, "; ".join(msg)

        except Exception as e:
            return False, f"Error comparing JSON: {e}"

    def find_matching_gcs_file(
        self, local_rel_path: Path, gcs_structure: Dict[str, List[str]]
    ) -> str:
        """Find matching GCS file for local file."""
        # Try exact match first
        gcs_path = str(local_rel_path).replace("\\", "/")
        if any(gcs_path in folder for folder in gcs_structure):
            return gcs_path

        # Try matching by filename in similar folder structure
        filename = local_rel_path.name
        local_parts = local_rel_path.parts

        for folder, files in gcs_structure.items():
            if filename in files:
                folder_parts = folder.split("/")
                # Check if folder structures are similar
                if any(part in folder_parts for part in local_parts[:-1]):
                    return f"{folder}/{filename}"

        return None

    def compare_structures(
        self, countries: List[str] = None
    ) -> Dict[str, Any]:
        """Compare local and GCS structures."""
        logger.info("Comparing local and GCS data structures...")
        logger.info("")
        logger.info("NOTE: This compares generated test data against CURRENT GCS data.")
        logger.info("If schemas mismatch, regenerate test data by running:")
        logger.info("  1. python scripts/collect_gcs_data_examples.py --countries <your-countries>")
        logger.info("  2. python scripts/generate_test_data.py")
        logger.info("")

        # Get structures
        logger.info("Scanning local directory...")
        local_structure = self.get_local_structure()
        logger.info(f"  Found {len(local_structure)} local folders")

        logger.info("Scanning GCS bucket...")
        gcs_structure = self.get_gcs_structure()
        logger.info(f"  Found {len(gcs_structure)} GCS folder patterns")

        # Compare folder structure
        local_folders = set(local_structure.keys())
        gcs_folder_patterns = set(
            "/".join(folder.split("/")[:2]) for folder in gcs_structure.keys()
        )

        logger.info("\nComparing folder structures...")

        # Check if key folders exist
        key_folders = [
            "mapped-datasets",
            "metadata",
            "training_data",
            "robyn",
            "robyn-queues",
        ]

        for folder in key_folders:
            has_local = any(
                folder in str(lf) for lf in local_structure.keys()
            )
            has_gcs = any(folder in gf for gf in gcs_structure.keys())

            if has_local and has_gcs:
                self.matches.append(f"✓ Folder '{folder}' exists in both")
                logger.info(f"  ✓ {folder}: Found in both")
            elif has_local and not has_gcs:
                self.warnings.append(
                    f"Folder '{folder}' in local but not in GCS"
                )
                logger.warning(f"  ⚠ {folder}: Only in local")
            elif not has_local and has_gcs:
                self.issues.append(f"Folder '{folder}' missing in local")
                logger.error(f"  ✗ {folder}: Missing in local")

        # Compare file structures for parquet and JSON files
        logger.info("\nComparing file structures...")
        files_compared = 0
        files_matched = 0

        for local_rel_path in self.local_dir.rglob("*"):
            if not local_rel_path.is_file():
                continue

            rel_path = local_rel_path.relative_to(self.local_dir)

            # Find matching GCS file
            gcs_path = self.find_matching_gcs_file(rel_path, gcs_structure)

            if not gcs_path:
                self.warnings.append(
                    f"No matching GCS file found for {rel_path}"
                )
                continue

            files_compared += 1

            # Compare based on file type
            if local_rel_path.suffix == ".parquet":
                success, message = self.compare_parquet_schemas(
                    local_rel_path, gcs_path
                )
                if success:
                    self.matches.append(f"✓ Parquet schema matches: {rel_path}")
                    files_matched += 1
                    logger.info(f"  ✓ {rel_path}: {message}")
                else:
                    self.issues.append(
                        f"Parquet schema mismatch in {rel_path}: {message}"
                    )
                    logger.error(f"  ✗ {rel_path}: {message}")

            elif local_rel_path.suffix == ".json":
                success, message = self.compare_json_structure(
                    local_rel_path, gcs_path
                )
                if success:
                    self.matches.append(f"✓ JSON structure matches: {rel_path}")
                    files_matched += 1
                    logger.info(f"  ✓ {rel_path}: {message}")
                else:
                    self.warnings.append(
                        f"JSON structure difference in {rel_path}: {message}"
                    )
                    logger.warning(f"  ⚠ {rel_path}: {message}")

        return {
            "files_compared": files_compared,
            "files_matched": files_matched,
            "matches": len(self.matches),
            "warnings": len(self.warnings),
            "issues": len(self.issues),
        }

    def print_summary(self, stats: Dict[str, Any]) -> None:
        """Print comparison summary."""
        logger.info("\n" + "=" * 60)
        logger.info("COMPARISON SUMMARY")
        logger.info("=" * 60)

        logger.info(f"\nFiles compared: {stats['files_compared']}")
        logger.info(f"Files matched: {stats['files_matched']}")
        logger.info(f"Total matches: {stats['matches']}")
        logger.info(f"Warnings: {stats['warnings']}")
        logger.info(f"Issues: {stats['issues']}")

        if self.issues:
            logger.info("\n" + "=" * 60)
            logger.info("ISSUES (need fixing):")
            logger.info("=" * 60)
            for issue in self.issues:
                logger.error(f"  ✗ {issue}")

        if self.warnings:
            logger.info("\n" + "=" * 60)
            logger.info("WARNINGS (review recommended):")
            logger.info("=" * 60)
            for warning in self.warnings:
                logger.warning(f"  ⚠ {warning}")

        if stats["issues"] == 0 and stats["warnings"] == 0:
            logger.info("\n✅ All structures match! Test data is compatible.")
        elif stats["issues"] == 0:
            logger.info(
                "\n⚠️  Test data is compatible but has some differences."
            )
        else:
            logger.info(
                "\n❌ Test data has structural issues that need fixing."
            )


def main():
    parser = argparse.ArgumentParser(
        description="Compare test data structure with GCS data"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--local-dir",
        default="test_data",
        help="Local test data directory (default: test_data)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="Countries to compare (default: all found)",
    )
    parser.add_argument(
        "--output",
        help="Output file for detailed comparison report (JSON)",
    )

    args = parser.parse_args()

    local_dir = Path(args.local_dir)

    if not local_dir.exists():
        logger.error(f"Local directory not found: {local_dir}")
        logger.error("Please run generate_test_data.py first")
        return 1

    logger.info(f"Local directory: {local_dir}")
    logger.info(f"GCS bucket: {args.bucket}")

    # Create comparison object
    comparator = StructureComparison(args.bucket, local_dir)

    # Run comparison
    stats = comparator.compare_structures(args.countries)

    # Print summary
    comparator.print_summary(stats)

    # Save detailed report if requested
    if args.output:
        report = {
            "statistics": stats,
            "matches": comparator.matches,
            "warnings": comparator.warnings,
            "issues": comparator.issues,
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"\nDetailed report saved to: {args.output}")

    # Return appropriate exit code
    if stats["issues"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
