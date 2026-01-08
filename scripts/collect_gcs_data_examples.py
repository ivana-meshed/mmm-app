#!/usr/bin/env python3
"""
Collect data examples from GCS bucket for test data generation.

This script scans the mmm-app-output GCS bucket to collect examples of:
- Raw data structures and field names
- mapped-datasets (raw.parquet files)
- metadata (mapping.json files)
- training_data (selected_columns.json and parquet files)
- training_config files
- training-data folders
- robyn output structures

The output is a JSON report that can be used to generate test data.
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from google.cloud import storage

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def safe_json_serialize(obj: Any) -> Any:
    """Convert objects to JSON-serializable format."""
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    elif isinstance(obj, pd.Series):
        return obj.to_dict()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    elif isinstance(obj, (set, frozenset)):
        return list(obj)
    elif hasattr(obj, "__dict__"):
        return str(obj)
    return obj


def list_all_blobs(bucket_name: str, prefix: str = "") -> List[Dict]:
    """List all blobs in bucket with metadata."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=prefix)

    blob_list = []
    for blob in blobs:
        blob_list.append(
            {
                "name": blob.name,
                "size": blob.size,
                "content_type": blob.content_type,
                "updated": blob.updated.isoformat() if blob.updated else None,
            }
        )
    return blob_list


def collect_mapped_datasets(
    bucket_name: str, countries: List[str]
) -> Dict[str, Any]:
    """Collect examples of mapped-datasets."""
    logger.info("Collecting mapped-datasets examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {}

    for country in countries:
        prefix = f"mapped-datasets/{country}/"
        blobs = bucket.list_blobs(prefix=prefix)

        country_data = {"versions": [], "sample_schemas": {}}

        for blob in blobs:
            if blob.name.endswith("raw.parquet"):
                parts = blob.name.split("/")
                if len(parts) >= 4:
                    version = parts[2]
                    country_data["versions"].append(
                        {
                            "version": version,
                            "path": blob.name,
                            "size": blob.size,
                            "updated": (
                                blob.updated.isoformat()
                                if blob.updated
                                else None
                            ),
                        }
                    )

                    # Try to read schema from first parquet file only
                    if not country_data["sample_schemas"]:
                        try:
                            # Read just first few rows for schema
                            import tempfile

                            with tempfile.NamedTemporaryFile(
                                suffix=".parquet"
                            ) as tmp:
                                blob.download_to_filename(tmp.name)

                                # Try reading with pandas first
                                df = None
                                try:
                                    df = pd.read_parquet(tmp.name)
                                except Exception as pandas_error:
                                    # If pandas fails, try PyArrow directly with lenient settings
                                    logger.debug(
                                        f"  Pandas read failed for {blob.name}, trying PyArrow: {pandas_error}"
                                    )
                                    try:
                                        import pyarrow.parquet as pq

                                        # Read with PyArrow's more lenient parser
                                        table = pq.read_table(tmp.name)
                                        df = table.to_pandas()
                                        logger.debug(
                                            f"  Successfully read {blob.name} with PyArrow"
                                        )
                                    except Exception as pyarrow_error:
                                        logger.warning(
                                            f"  Could not read parquet {blob.name} with PyArrow: {pyarrow_error}"
                                        )
                                        raise  # Re-raise to be caught by outer exception handler

                                if df is not None:
                                    country_data["sample_schemas"][version] = {
                                        "columns": list(df.columns),
                                        "dtypes": {
                                            col: str(dtype)
                                            for col, dtype in df.dtypes.items()
                                        },
                                        "row_count": len(df),
                                        "sample_values": {
                                            col: (
                                                df[col].head(3).tolist()
                                                if not df[col].empty
                                                else []
                                            )
                                            for col in df.columns
                                        },
                                    }
                                    logger.info(
                                        f"  Read schema from {blob.name}: {len(df.columns)} columns"
                                    )
                        except Exception as e:
                            logger.warning(
                                f"  Could not read parquet {blob.name}: {e}"
                            )

        if country_data["versions"]:
            examples[country] = country_data

    return examples


def collect_metadata(bucket_name: str, countries: List[str]) -> Dict[str, Any]:
    """Collect examples of metadata files."""
    logger.info("Collecting metadata examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {}

    for country in countries:
        prefix = f"metadata/{country}/"
        blobs = bucket.list_blobs(prefix=prefix)

        country_data = {"versions": [], "sample_mappings": {}}

        for blob in blobs:
            if blob.name.endswith("mapping.json"):
                parts = blob.name.split("/")
                if len(parts) >= 4:
                    version = parts[2]
                    country_data["versions"].append(
                        {
                            "version": version,
                            "path": blob.name,
                            "size": blob.size,
                            "updated": (
                                blob.updated.isoformat()
                                if blob.updated
                                else None
                            ),
                        }
                    )

                    # Read first mapping.json found
                    if not country_data["sample_mappings"]:
                        try:
                            content = json.loads(blob.download_as_bytes())
                            country_data["sample_mappings"][version] = content
                            logger.info(
                                f"  Read mapping from {blob.name}: {len(content)} keys"
                            )
                        except Exception as e:
                            logger.warning(
                                f"  Could not read JSON {blob.name}: {e}"
                            )

        if country_data["versions"]:
            examples[country] = country_data

    return examples


def collect_training_data(
    bucket_name: str, countries: List[str]
) -> Dict[str, Any]:
    """Collect examples of training_data files."""
    logger.info("Collecting training_data examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {}

    for country in countries:
        prefix = f"training_data/{country}/"
        blobs = bucket.list_blobs(prefix=prefix)

        country_data = {
            "versions": [],
            "sample_configs": {},
            "parquet_files": [],
        }

        for blob in blobs:
            parts = blob.name.split("/")

            if blob.name.endswith("selected_columns.json"):
                if len(parts) >= 4:
                    version = parts[2]
                    country_data["versions"].append(
                        {
                            "version": version,
                            "path": blob.name,
                            "size": blob.size,
                            "updated": (
                                blob.updated.isoformat()
                                if blob.updated
                                else None
                            ),
                        }
                    )

                    # Read first config found
                    if not country_data["sample_configs"]:
                        try:
                            content = json.loads(blob.download_as_bytes())
                            country_data["sample_configs"][version] = content
                            logger.info(
                                f"  Read training config from {blob.name}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"  Could not read JSON {blob.name}: {e}"
                            )

            elif blob.name.endswith(".parquet"):
                country_data["parquet_files"].append(
                    {
                        "path": blob.name,
                        "size": blob.size,
                        "updated": (
                            blob.updated.isoformat() if blob.updated else None
                        ),
                    }
                )

        if country_data["versions"] or country_data["parquet_files"]:
            examples[country] = country_data

    return examples


def collect_training_configs(bucket_name: str) -> Dict[str, Any]:
    """Collect examples of training_config files."""
    logger.info("Collecting training_config examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {"configs": [], "sample_data": {}}

    prefix = "training_config/"
    blobs = bucket.list_blobs(prefix=prefix)

    for blob in blobs:
        if blob.name.endswith(".json"):
            examples["configs"].append(
                {
                    "path": blob.name,
                    "size": blob.size,
                    "updated": (
                        blob.updated.isoformat() if blob.updated else None
                    ),
                }
            )

            # Read first config found
            if not examples["sample_data"]:
                try:
                    content = json.loads(blob.download_as_bytes())
                    examples["sample_data"][blob.name] = content
                    logger.info(f"  Read config from {blob.name}")
                except Exception as e:
                    logger.warning(f"  Could not read JSON {blob.name}: {e}")

    return examples


def collect_training_data_alt(bucket_name: str) -> Dict[str, Any]:
    """Collect examples from training-data folder (alternative structure)."""
    logger.info("Collecting training-data examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {"files": [], "sample_schemas": {}}

    prefix = "training-data/"
    blobs = bucket.list_blobs(prefix=prefix)

    for blob in blobs:
        examples["files"].append(
            {
                "path": blob.name,
                "size": blob.size,
                "content_type": blob.content_type,
                "updated": blob.updated.isoformat() if blob.updated else None,
            }
        )

        # Try to read first parquet found
        if blob.name.endswith(".parquet") and not examples["sample_schemas"]:
            try:
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                    blob.download_to_filename(tmp.name)

                    # Try reading with pandas first
                    df = None
                    try:
                        df = pd.read_parquet(tmp.name)
                    except Exception as pandas_error:
                        # If pandas fails, try PyArrow directly
                        logger.debug(
                            f"  Pandas read failed for {blob.name}, trying PyArrow: {pandas_error}"
                        )
                        try:
                            import pyarrow.parquet as pq

                            table = pq.read_table(tmp.name)
                            df = table.to_pandas()
                            logger.debug(
                                f"  Successfully read {blob.name} with PyArrow"
                            )
                        except Exception as pyarrow_error:
                            logger.warning(
                                f"  Could not read parquet {blob.name} with PyArrow: {pyarrow_error}"
                            )
                            raise  # Re-raise to be caught by outer exception handler

                    if df is not None:
                        examples["sample_schemas"][blob.name] = {
                            "columns": list(df.columns),
                            "dtypes": {
                                col: str(dtype)
                                for col, dtype in df.dtypes.items()
                            },
                            "row_count": len(df),
                        }
                        logger.info(
                            f"  Read schema from {blob.name}: {len(df.columns)} columns"
                        )
            except Exception as e:
                logger.warning(f"  Could not read parquet {blob.name}: {e}")

    return examples


def collect_robyn_outputs(
    bucket_name: str, countries: List[str]
) -> Dict[str, Any]:
    """Collect examples of Robyn output structures."""
    logger.info("Collecting robyn output examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {}

    for country in countries:
        prefix = f"robyn/v1/{country}/"
        blobs = list(bucket.list_blobs(prefix=prefix))

        # Limit to first 20 blobs to avoid too much data
        blobs = blobs[:20]

        country_data = {"runs": [], "file_types": defaultdict(int)}

        run_ids = set()
        for blob in blobs:
            parts = blob.name.split("/")
            # robyn/v1/country/run_id/...
            if len(parts) >= 4:
                run_id = parts[3]
                run_ids.add(run_id)

            # Count file types
            if "." in blob.name:
                ext = blob.name.split(".")[-1]
                country_data["file_types"][ext] += 1

        country_data["runs"] = sorted(list(run_ids))
        country_data["file_types"] = dict(country_data["file_types"])

        if country_data["runs"]:
            examples[country] = country_data
            logger.info(
                f"  Found {len(country_data['runs'])} runs for {country}"
            )

    return examples


def collect_queue_data(bucket_name: str) -> Dict[str, Any]:
    """Collect examples of queue structures."""
    logger.info("Collecting queue data examples...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    examples = {"queues": [], "files": []}

    prefix = "robyn-queues/"
    blobs = bucket.list_blobs(prefix=prefix)

    for blob in blobs:
        examples["files"].append(
            {
                "path": blob.name,
                "size": blob.size,
                "updated": blob.updated.isoformat() if blob.updated else None,
            }
        )

    return examples


def main():
    parser = argparse.ArgumentParser(
        description="Collect GCS data examples for test generation"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name (default: mmm-app-output)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        default=["de", "universal"],
        help="Countries to scan (default: de universal)",
    )
    parser.add_argument(
        "--output",
        default="gcs_data_examples.json",
        help="Output JSON file (default: gcs_data_examples.json)",
    )
    parser.add_argument(
        "--all-countries",
        action="store_true",
        help="Scan all available countries",
    )

    args = parser.parse_args()

    logger.info(f"Scanning GCS bucket: {args.bucket}")
    logger.info(f"Target countries: {args.countries}")

    # Collect all data examples
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "bucket": args.bucket,
        "countries_scanned": args.countries,
        "mapped_datasets": collect_mapped_datasets(args.bucket, args.countries),
        "metadata": collect_metadata(args.bucket, args.countries),
        "training_data": collect_training_data(args.bucket, args.countries),
        "training_configs": collect_training_configs(args.bucket),
        "training_data_alt": collect_training_data_alt(args.bucket),
        "robyn_outputs": collect_robyn_outputs(args.bucket, args.countries),
        "queue_data": collect_queue_data(args.bucket),
    }

    # Write report
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=safe_json_serialize)

    logger.info(f"\n✅ Report written to: {args.output}")
    logger.info("\nSummary:")
    logger.info(
        f"  - Mapped datasets: {len(report['mapped_datasets'])} countries"
    )
    logger.info(f"  - Metadata: {len(report['metadata'])} countries")
    logger.info(f"  - Training data: {len(report['training_data'])} countries")
    logger.info(
        f"  - Training configs: {len(report['training_configs']['configs'])} files"
    )
    logger.info(
        f"  - Training-data alt: {len(report['training_data_alt']['files'])} files"
    )
    logger.info(f"  - Robyn outputs: {len(report['robyn_outputs'])} countries")
    logger.info(f"  - Queue files: {len(report['queue_data']['files'])} files")


if __name__ == "__main__":
    main()
