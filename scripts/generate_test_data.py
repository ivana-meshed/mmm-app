#!/usr/bin/env python3
"""
Generate test data based on collected GCS data examples.

This script reads the JSON report from collect_gcs_data_examples.py
and generates synthetic test data that matches the structure.
"""

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_dtype(dtype_str: str) -> type:
    """Parse pandas dtype string to Python type."""
    if "int" in dtype_str:
        return int
    elif "float" in dtype_str:
        return float
    elif "datetime" in dtype_str or "date" in dtype_str:
        return "datetime"
    elif "object" in dtype_str or "string" in dtype_str:
        return str
    else:
        return str


def generate_synthetic_data(
    schema: Dict[str, Any], num_rows: int = 100
) -> pd.DataFrame:
    """Generate synthetic data matching the schema."""
    data = {}

    for col, dtype_str in schema["dtypes"].items():
        dtype_type = parse_dtype(dtype_str)

        # Use sample values if available
        if (
            "sample_values" in schema
            and col in schema["sample_values"]
            and schema["sample_values"][col]
        ):
            sample_values = schema["sample_values"][col]
            # Repeat and extend sample values to fill num_rows
            data[col] = (sample_values * (num_rows // len(sample_values) + 1))[
                :num_rows
            ]
        elif dtype_type == int:
            data[col] = np.random.randint(0, 100, size=num_rows)
        elif dtype_type == float:
            data[col] = np.random.random(size=num_rows) * 100
        elif dtype_type == "datetime":
            start_date = datetime(2023, 1, 1)
            data[col] = [
                start_date + timedelta(days=i) for i in range(num_rows)
            ]
        else:  # str
            data[col] = [f"value_{i}" for i in range(num_rows)]

    return pd.DataFrame(data)


def generate_mapped_dataset(
    example: Dict[str, Any], output_dir: Path, country: str
) -> None:
    """Generate synthetic mapped dataset (raw.parquet)."""
    logger.info(f"Generating mapped dataset for {country}...")

    if not example.get("sample_schemas"):
        logger.warning(f"  No schema available for {country}, skipping")
        return

    # Get first schema
    schema = next(iter(example["sample_schemas"].values()))

    # Generate synthetic data
    df = generate_synthetic_data(schema, num_rows=365)

    # Save to parquet
    output_path = output_dir / "mapped-datasets" / country / "latest"
    output_path.mkdir(parents=True, exist_ok=True)

    parquet_file = output_path / "raw.parquet"
    df.to_parquet(parquet_file, index=False)

    logger.info(f"  Created: {parquet_file}")
    logger.info(f"  Shape: {df.shape}")


def generate_metadata(
    example: Dict[str, Any], output_dir: Path, country: str
) -> None:
    """Generate synthetic metadata (mapping.json)."""
    logger.info(f"Generating metadata for {country}...")

    if not example.get("sample_mappings"):
        logger.warning(f"  No mapping available for {country}, skipping")
        return

    # Get first mapping
    mapping = next(iter(example["sample_mappings"].values()))

    # Save to JSON
    output_path = output_dir / "metadata" / country / "latest"
    output_path.mkdir(parents=True, exist_ok=True)

    json_file = output_path / "mapping.json"
    with open(json_file, "w") as f:
        json.dump(mapping, f, indent=2)

    logger.info(f"  Created: {json_file}")


def generate_training_data(
    example: Dict[str, Any], output_dir: Path, country: str
) -> None:
    """Generate synthetic training data."""
    logger.info(f"Generating training data for {country}...")

    if not example.get("sample_configs"):
        logger.warning(f"  No config available for {country}, skipping")
        return

    # Get first config
    config = next(iter(example["sample_configs"].values()))

    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save config
    output_path = output_dir / "training_data" / country / timestamp
    output_path.mkdir(parents=True, exist_ok=True)

    json_file = output_path / "selected_columns.json"
    with open(json_file, "w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"  Created: {json_file}")

    # Generate sample parquet if schema available
    if "columns" in config:
        # Create minimal dataframe with columns from config
        columns = config.get("columns", [])
        if columns:
            df = pd.DataFrame(
                {col: np.random.random(100) for col in columns[:10]}
            )
            parquet_file = output_path / "training_data.parquet"
            df.to_parquet(parquet_file, index=False)
            logger.info(f"  Created: {parquet_file}")


def generate_training_config(example: Dict[str, Any], output_dir: Path) -> None:
    """Generate synthetic training config."""
    logger.info("Generating training config...")

    if not example.get("sample_data"):
        logger.warning("  No training config available, skipping")
        return

    # Get first sample
    sample = next(iter(example["sample_data"].values()))

    # Save config
    output_path = output_dir / "training_config"
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_file = output_path / f"config_{timestamp}.json"
    with open(json_file, "w") as f:
        json.dump(sample, f, indent=2)

    logger.info(f"  Created: {json_file}")


def generate_robyn_output_structure(output_dir: Path, country: str) -> None:
    """Generate minimal Robyn output directory structure."""
    logger.info(f"Generating Robyn output structure for {country}...")

    # Create a minimal run directory
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_path = output_dir / "robyn" / "v1" / country / timestamp
    output_path.mkdir(parents=True, exist_ok=True)

    # Create placeholder files
    placeholder_files = [
        "model_summary.json",
        "hyperparameters.json",
        "results.csv",
    ]

    for filename in placeholder_files:
        file_path = output_path / filename
        if filename.endswith(".json"):
            with open(file_path, "w") as f:
                json.dump({"placeholder": True, "timestamp": timestamp}, f)
        else:
            pd.DataFrame({"placeholder": [True]}).to_csv(file_path, index=False)

    logger.info(f"  Created: {output_path}")


def generate_queue_structure(output_dir: Path) -> None:
    """Generate minimal queue directory structure."""
    logger.info("Generating queue structure...")

    output_path = output_dir / "robyn-queues" / "default"
    output_path.mkdir(parents=True, exist_ok=True)

    # Create placeholder queue.json
    queue_file = output_path / "queue.json"
    with open(queue_file, "w") as f:
        json.dump(
            {"queue": [], "timestamp": datetime.now(timezone.utc).isoformat()},
            f,
        )

    logger.info(f"  Created: {queue_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate test data from collected examples"
    )
    parser.add_argument(
        "--input",
        default="gcs_data_examples.json",
        help="Input JSON file from collect_gcs_data_examples.py",
    )
    parser.add_argument(
        "--output-dir",
        default="test_data",
        help="Output directory for test data (default: test_data)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        default=["de", "universal"],
        help="Countries to generate data for",
    )

    args = parser.parse_args()

    logger.info(f"Reading examples from: {args.input}")

    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        logger.error("Please run collect_gcs_data_examples.py first")
        return 1

    with open(args.input, "r") as f:
        report = json.load(f)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    logger.info(f"Generating test data in: {output_dir}")

    # Generate data for each section
    for country in args.countries:
        # Mapped datasets
        if country in report.get("mapped_datasets", {}):
            generate_mapped_dataset(
                report["mapped_datasets"][country], output_dir, country
            )

        # Metadata
        if country in report.get("metadata", {}):
            generate_metadata(report["metadata"][country], output_dir, country)

        # Training data
        if country in report.get("training_data", {}):
            generate_training_data(
                report["training_data"][country], output_dir, country
            )

        # Robyn output structure
        generate_robyn_output_structure(output_dir, country)

    # Training configs
    if report.get("training_configs"):
        generate_training_config(report["training_configs"], output_dir)

    # Queue structure
    generate_queue_structure(output_dir)

    logger.info("\n✅ Test data generation complete!")
    logger.info(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
