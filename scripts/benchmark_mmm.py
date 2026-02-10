#!/usr/bin/env python3
"""
MMM Benchmarking Script

Systematically evaluate different Robyn/MMM configurations to identify
optimal settings for various scenarios (spend→var mapping, adstock,
train/test splits, etc.).

This script:
1. Loads a base selected_columns.json configuration
2. Generates test configuration variants based on benchmark config
3. Submits jobs to the Cloud Run training queue
4. Collects and analyzes results
5. Exports comparison tables for analysis

Usage:
    python scripts/benchmark_mmm.py --config benchmarks/my_test.json
    python scripts/benchmark_mmm.py --list-configs
    python scripts/benchmark_mmm.py --collect-results benchmark_id_123
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Environment constants
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
BENCHMARK_ROOT = "benchmarks"


class BenchmarkConfig:
    """Configuration for a benchmark test run."""

    def __init__(self, config_dict: Dict[str, Any]):
        self.config = config_dict
        self.validate()

    def validate(self):
        """Validate benchmark configuration."""
        required = ["name", "description", "base_config", "variants"]
        for field in required:
            if field not in self.config:
                raise ValueError(f"Missing required field: {field}")

    @property
    def name(self) -> str:
        return self.config["name"]

    @property
    def description(self) -> str:
        return self.config["description"]

    @property
    def base_config(self) -> Dict[str, str]:
        """Base configuration reference."""
        return self.config["base_config"]

    @property
    def variants(self) -> Dict[str, List[Dict]]:
        """Test variants to generate."""
        return self.config.get("variants", {})

    @property
    def max_combinations(self) -> int:
        """Maximum number of config combinations to test."""
        return self.config.get("max_combinations", 50)

    @property
    def iterations(self) -> int:
        """Robyn iterations per config."""
        return self.config.get("iterations", 2000)

    @property
    def trials(self) -> int:
        """Robyn trials per config."""
        return self.config.get("trials", 5)


class BenchmarkRunner:
    """Manages benchmark test execution."""

    def __init__(self, bucket_name: str = GCS_BUCKET):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def load_base_config(
        self, country: str, goal: str, version: str
    ) -> Dict[str, Any]:
        """Load selected_columns.json from GCS."""
        blob_path = (
            f"training_data/{country.lower()}/{goal}/{version}/"
            f"selected_columns.json"
        )
        blob = self.bucket.blob(blob_path)

        if not blob.exists():
            raise FileNotFoundError(
                f"Base config not found: gs://{self.bucket_name}/{blob_path}"
            )

        data = blob.download_as_bytes()
        return json.loads(data)

    def generate_variants(
        self, base_config: Dict[str, Any], benchmark_config: BenchmarkConfig
    ) -> List[Dict[str, Any]]:
        """Generate test configuration variants."""
        variants = []
        variant_specs = benchmark_config.variants

        # Generate spend→var mapping variants
        if "spend_var_mapping" in variant_specs:
            variants.extend(
                self._generate_spend_var_variants(
                    base_config, variant_specs["spend_var_mapping"]
                )
            )

        # Generate adstock variants
        if "adstock" in variant_specs:
            variants.extend(
                self._generate_adstock_variants(
                    base_config, variant_specs["adstock"]
                )
            )

        # Generate train/val/test split variants
        if "train_splits" in variant_specs:
            variants.extend(
                self._generate_split_variants(
                    base_config, variant_specs["train_splits"]
                )
            )

        # Generate time aggregation variants
        if "time_aggregation" in variant_specs:
            variants.extend(
                self._generate_time_agg_variants(
                    base_config, variant_specs["time_aggregation"]
                )
            )

        # Generate seasonality window variants
        if "seasonality_window" in variant_specs:
            variants.extend(
                self._generate_seasonality_variants(
                    base_config, variant_specs["seasonality_window"]
                )
            )

        # Limit combinations if needed
        max_combos = benchmark_config.max_combinations
        if len(variants) > max_combos:
            logger.warning(
                f"Generated {len(variants)} variants, "
                f"limiting to {max_combos}"
            )
            variants = variants[:max_combos]

        return variants

    def _generate_spend_var_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate spend→var mapping test variants."""
        variants = []

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "spend_var_mapping"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")

            mapping_type = spec.get("type")

            if mapping_type == "spend_to_spend":
                # All channels: spend → spend
                variant["paid_media_vars"] = variant["paid_media_spends"]
                variant["var_to_spend_mapping"] = {
                    spend: spend
                    for spend in variant["paid_media_spends"]
                }

            elif mapping_type == "spend_to_proxy":
                # All channels: spend → proxy
                # Use provided proxy mapping or default pattern
                proxy_map = spec.get("proxy_mapping", {})
                variant["var_to_spend_mapping"] = proxy_map

            elif mapping_type == "mixed_by_funnel":
                # Upper funnel → proxy, lower funnel → spend
                upper_channels = spec.get("upper_funnel_channels", [])
                lower_channels = spec.get("lower_funnel_channels", [])
                proxy_map = spec.get("proxy_mapping", {})

                mapping = {}
                for spend in variant["paid_media_spends"]:
                    if spend in upper_channels:
                        mapping[spend] = proxy_map.get(spend, spend)
                    elif spend in lower_channels:
                        mapping[spend] = spend
                    else:
                        # Default to spend
                        mapping[spend] = spend

                variant["var_to_spend_mapping"] = mapping

            variants.append(variant)

        return variants

    def _generate_adstock_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate adstock type test variants."""
        variants = []

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "adstock"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")
            variant["adstock"] = spec.get("type")

            # Optional: specify hyperparameter preset
            if "hyperparameter_preset" in spec:
                variant["hyperparameter_preset"] = spec[
                    "hyperparameter_preset"
                ]

            variants.append(variant)

        return variants

    def _generate_split_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate train/val/test split variants."""
        variants = []

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "train_split"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")
            variant["train_size"] = spec.get("train_size")

            variants.append(variant)

        return variants

    def _generate_time_agg_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate time aggregation variants."""
        variants = []

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "time_aggregation"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")
            variant["resample_freq"] = spec.get("frequency")

            variants.append(variant)

        return variants

    def _generate_seasonality_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate seasonality window variants."""
        variants = []

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "seasonality_window"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")

            # Override start/end dates for seasonality window
            if "start_date" in spec:
                variant["start_date"] = spec["start_date"]
            if "end_date" in spec:
                variant["end_date"] = spec["end_date"]

            variants.append(variant)

        return variants

    def save_benchmark_plan(
        self,
        benchmark_id: str,
        benchmark_config: BenchmarkConfig,
        variants: List[Dict[str, Any]],
    ):
        """Save benchmark execution plan to GCS."""
        plan = {
            "benchmark_id": benchmark_id,
            "name": benchmark_config.name,
            "description": benchmark_config.description,
            "created_at": datetime.utcnow().isoformat(),
            "status": "planned",
            "variant_count": len(variants),
            "variants": variants,
        }

        blob_path = f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        blob = self.bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(plan, indent=2), content_type="application/json"
        )

        logger.info(
            f"Saved benchmark plan: "
            f"gs://{self.bucket_name}/{blob_path}"
        )

    def list_benchmarks(self) -> List[Dict[str, Any]]:
        """List all benchmark runs."""
        blobs = self.client.list_blobs(
            self.bucket_name, prefix=f"{BENCHMARK_ROOT}/", delimiter="/"
        )

        benchmarks = []
        for blob in blobs:
            if blob.name.endswith("plan.json"):
                data = json.loads(blob.download_as_bytes())
                benchmarks.append(
                    {
                        "benchmark_id": data["benchmark_id"],
                        "name": data["name"],
                        "created_at": data["created_at"],
                        "status": data.get("status", "unknown"),
                        "variant_count": data.get("variant_count", 0),
                    }
                )

        return benchmarks


class ResultsCollector:
    """Collects and analyzes benchmark results."""

    def __init__(self, bucket_name: str = GCS_BUCKET):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def collect_results(self, benchmark_id: str) -> pd.DataFrame:
        """Collect results from all benchmark variants."""
        # Load benchmark plan
        plan_blob = self.bucket.blob(
            f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        )
        if not plan_blob.exists():
            raise FileNotFoundError(
                f"Benchmark plan not found: {benchmark_id}"
            )

        plan = json.loads(plan_blob.download_as_bytes())
        variants = plan["variants"]

        results = []
        for variant in variants:
            result = self._collect_variant_result(variant)
            if result:
                results.append(result)

        if not results:
            logger.warning(f"No results found for benchmark {benchmark_id}")
            return pd.DataFrame()

        df = pd.DataFrame(results)
        return df

    def _collect_variant_result(
        self, variant: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Collect results for a single variant."""
        # Find the GCS path for this variant's results
        # This would be based on the job execution
        # For now, return None as placeholder
        logger.warning("Result collection not yet fully implemented")
        return None

    def export_results(
        self, benchmark_id: str, df: pd.DataFrame, format: str = "csv"
    ):
        """Export results to GCS."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        if format == "csv":
            output_path = (
                f"{BENCHMARK_ROOT}/{benchmark_id}/"
                f"results_{timestamp}.csv"
            )
            csv_data = df.to_csv(index=False)
            blob = self.bucket.blob(output_path)
            blob.upload_from_string(
                csv_data, content_type="text/csv"
            )
        elif format == "parquet":
            output_path = (
                f"{BENCHMARK_ROOT}/{benchmark_id}/"
                f"results_{timestamp}.parquet"
            )
            # Would need pyarrow for this
            logger.warning("Parquet export not yet implemented")
            return

        logger.info(
            f"Exported results: gs://{self.bucket_name}/{output_path}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run MMM benchmarking tests"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to benchmark configuration JSON file",
    )
    parser.add_argument(
        "--list-configs",
        action="store_true",
        help="List available benchmark configurations",
    )
    parser.add_argument(
        "--collect-results",
        type=str,
        help="Collect results for a benchmark ID",
    )
    parser.add_argument(
        "--export-format",
        type=str,
        default="csv",
        choices=["csv", "parquet"],
        help="Export format for results",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate variants but don't submit jobs",
    )

    args = parser.parse_args()

    runner = BenchmarkRunner()

    if args.list_configs:
        benchmarks = runner.list_benchmarks()
        if not benchmarks:
            print("No benchmarks found")
            return

        print("\nAvailable Benchmarks:")
        print("-" * 80)
        for bm in benchmarks:
            print(f"ID: {bm['benchmark_id']}")
            print(f"Name: {bm['name']}")
            print(f"Created: {bm['created_at']}")
            print(f"Status: {bm['status']}")
            print(f"Variants: {bm['variant_count']}")
            print("-" * 80)
        return

    if args.collect_results:
        collector = ResultsCollector()
        logger.info(f"Collecting results for {args.collect_results}")
        df = collector.collect_results(args.collect_results)

        if not df.empty:
            collector.export_results(
                args.collect_results, df, format=args.export_format
            )
            print(f"\nCollected {len(df)} results")
            print(df.describe())
        else:
            print("No results found")
        return

    if not args.config:
        parser.error("--config is required (or use --list-configs)")

    # Load benchmark configuration
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    with open(args.config) as f:
        config_dict = json.load(f)

    benchmark_config = BenchmarkConfig(config_dict)
    logger.info(f"Loaded benchmark: {benchmark_config.name}")
    logger.info(f"Description: {benchmark_config.description}")

    # Load base configuration
    base_cfg = benchmark_config.base_config
    base_config = runner.load_base_config(
        country=base_cfg["country"],
        goal=base_cfg["goal"],
        version=base_cfg["version"],
    )
    logger.info(
        f"Loaded base config: {base_cfg['country']}/{base_cfg['goal']}"
    )

    # Generate variants
    variants = runner.generate_variants(base_config, benchmark_config)
    logger.info(f"Generated {len(variants)} test variants")

    # Generate benchmark ID
    benchmark_id = f"{benchmark_config.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    # Save benchmark plan
    runner.save_benchmark_plan(benchmark_id, benchmark_config, variants)

    if args.dry_run:
        logger.info("Dry run - not submitting jobs")
        print(f"\nGenerated {len(variants)} variants:")
        for i, variant in enumerate(variants, 1):
            test = variant.get("benchmark_test", "unknown")
            name = variant.get("benchmark_variant", "unnamed")
            print(f"{i}. {test}: {name}")
        return

    # TODO: Submit variants to job queue
    logger.warning(
        "Job submission not yet implemented - "
        "variants saved to GCS"
    )
    print(f"\nBenchmark ID: {benchmark_id}")
    print(f"Variants saved: {len(variants)}")
    print(
        f"Plan: gs://{runner.bucket_name}/"
        f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
    )


if __name__ == "__main__":
    main()
