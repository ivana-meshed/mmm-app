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
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage

try:
    import pandas as pd
except ImportError:
    pd = None  # Optional for basic functionality

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
        variant_specs = benchmark_config.variants
        combination_mode = benchmark_config.config_dict.get("combination_mode", "single")
        
        if combination_mode == "cartesian":
            # Generate cartesian product of all dimensions
            return self._generate_cartesian_variants(base_config, benchmark_config)
        else:
            # Generate variants for each dimension separately (default)
            return self._generate_single_variants(base_config, benchmark_config)
    
    def _generate_single_variants(
        self, base_config: Dict[str, Any], benchmark_config: BenchmarkConfig
    ) -> List[Dict[str, Any]]:
        """Generate variants for each dimension separately."""
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
    
    def _generate_cartesian_variants(
        self, base_config: Dict[str, Any], benchmark_config: BenchmarkConfig
    ) -> List[Dict[str, Any]]:
        """Generate cartesian product of all variant dimensions."""
        variant_specs = benchmark_config.variants
        
        # Generate variants for each dimension
        dimension_variants = {}
        
        if "adstock" in variant_specs:
            dimension_variants["adstock"] = self._generate_adstock_variants(
                base_config, variant_specs["adstock"]
            )
        
        if "train_splits" in variant_specs:
            dimension_variants["train_splits"] = self._generate_split_variants(
                base_config, variant_specs["train_splits"]
            )
        
        if "time_aggregation" in variant_specs:
            dimension_variants["time_aggregation"] = self._generate_time_agg_variants(
                base_config, variant_specs["time_aggregation"]
            )
        
        if "spend_var_mapping" in variant_specs:
            dimension_variants["spend_var_mapping"] = self._generate_spend_var_variants(
                base_config, variant_specs["spend_var_mapping"]
            )
        
        if "seasonality_window" in variant_specs:
            dimension_variants["seasonality_window"] = self._generate_seasonality_variants(
                base_config, variant_specs["seasonality_window"]
            )
        
        # Generate cartesian product
        if not dimension_variants:
            return []
        
        # Create combinations
        dimension_names = list(dimension_variants.keys())
        dimension_lists = [dimension_variants[name] for name in dimension_names]
        
        combined_variants = []
        for combo in product(*dimension_lists):
            # Merge all configs in this combination
            merged = base_config.copy()
            variant_name_parts = []
            
            for variant_config in combo:
                merged.update(variant_config)
                variant_name_parts.append(variant_config.get("benchmark_variant", ""))
            
            # Create combined name
            merged["benchmark_variant"] = "_".join(variant_name_parts)
            merged["benchmark_test"] = "combination"
            merged["benchmark_description"] = f"Combination: {', '.join(variant_name_parts)}"
            
            combined_variants.append(merged)
        
        # Limit combinations if needed
        max_combos = benchmark_config.max_combinations
        if len(combined_variants) > max_combos:
            logger.warning(
                f"Generated {len(combined_variants)} cartesian combinations, "
                f"limiting to {max_combos}"
            )
            combined_variants = combined_variants[:max_combos]
        
        return combined_variants

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
            "created_at": datetime.now(timezone.utc).isoformat(),
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

    def submit_variants_to_queue(
        self,
        benchmark_id: str,
        variants: List[Dict[str, Any]],
        queue_name: str = "default",
    ) -> int:
        """
        Submit benchmark variants to the training job queue.

        Args:
            benchmark_id: Unique benchmark identifier
            variants: List of configuration variants to queue
            queue_name: Queue name (default: "default")

        Returns:
            Number of jobs submitted
        """
        # Load current queue
        queue_doc = self._load_queue(queue_name)
        entries = queue_doc.get("entries", [])

        # Find next ID
        next_id = max([e.get("id", 0) for e in entries], default=0) + 1

        # Create queue entries for each variant
        new_entries = []
        for i, variant in enumerate(variants):
            # Build params dict compatible with existing queue format
            params = self._variant_to_queue_params(variant, benchmark_id)

            entry = {
                "id": next_id + i,
                "params": params,
                "status": "PENDING",
                "timestamp": None,
                "execution_name": None,
                "gcs_prefix": None,
                "message": "",
            }
            new_entries.append(entry)

        # Add to queue
        entries.extend(new_entries)
        queue_doc["entries"] = entries

        # Save queue back to GCS
        self._save_queue(queue_name, queue_doc)

        logger.info(
            f"Submitted {len(new_entries)} benchmark jobs to queue "
            f"'{queue_name}'"
        )

        return len(new_entries)

    def _variant_to_queue_params(
        self, variant: Dict[str, Any], benchmark_id: str
    ) -> Dict[str, Any]:
        """Convert benchmark variant to queue params format."""
        # Extract required fields
        country = variant.get("country", "")
        revision = variant.get("revision", "default")
        
        # CRITICAL: Construct data_gcs_path from data_version
        # This is required for queue processing to work
        data_version = variant.get("data_version", "")
        if data_version:
            # Path format: gs://{bucket}/mapped-datasets/{country}/{version}/raw.parquet
            data_gcs_path = (
                f"gs://{self.bucket_name}/mapped-datasets/"
                f"{country.lower()}/{data_version}/raw.parquet"
            )
        else:
            # Fallback: try to infer from meta_version or fail
            logger.warning(
                f"No data_version in variant {variant.get('benchmark_variant')}, "
                f"job may fail"
            )
            data_gcs_path = None

        # Get dep_var from either dep_var or selected_goal
        dep_var = variant.get("dep_var") or variant.get("selected_goal", "UPLOAD_VALUE")

        # Build params compatible with existing training format
        params = {
            "country": country,
            "revision": revision,
            "date_input": variant.get("date_input", ""),
            "iterations": variant.get("iterations", 2000),
            "trials": variant.get("trials", 5),
            "train_size": variant.get("train_size", [0.7, 0.9]),
            "start_date": variant.get("start_date", ""),
            "end_date": variant.get("end_date", ""),
            "paid_media_spends": variant.get("paid_media_spends", []),
            "paid_media_vars": variant.get("paid_media_vars", []),
            "context_vars": variant.get("context_vars", []),
            "factor_vars": variant.get("factor_vars", []),
            "organic_vars": variant.get("organic_vars", []),
            "dep_var": dep_var,
            "dep_var_type": variant.get("dep_var_type", "revenue"),
            "date_var": variant.get("date_var", "date"),
            "adstock": variant.get("adstock", "geometric"),
            "hyperparameter_preset": variant.get(
                "hyperparameter_preset", "Meshed recommend"
            ),
            "resample_freq": variant.get("resample_freq", "none"),
            "gcs_bucket": self.bucket_name,
            # CRITICAL: Add data_gcs_path for GCS-based workflow
            "data_gcs_path": data_gcs_path,
            # Add benchmark metadata
            "benchmark_id": benchmark_id,
            "benchmark_test": variant.get("benchmark_test", ""),
            "benchmark_variant": variant.get("benchmark_variant", ""),
        }

        # Add optional fields if present
        if "custom_hyperparameters" in variant:
            params["custom_hyperparameters"] = variant[
                "custom_hyperparameters"
            ]
        if "column_agg_strategies" in variant:
            params["column_agg_strategies"] = variant[
                "column_agg_strategies"
            ]

        return params

    def _load_queue(self, queue_name: str) -> Dict[str, Any]:
        """Load queue document from GCS."""
        queue_root = os.getenv("QUEUE_ROOT", "robyn-queues")
        blob_path = f"{queue_root}/{queue_name}/queue.json"
        blob = self.bucket.blob(blob_path)

        if not blob.exists():
            return {
                "version": 1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "entries": [],
                "queue_running": True,
            }

        try:
            doc = json.loads(blob.download_as_text())
            if isinstance(doc, list):
                # Back-compat: wrap list as document
                doc = {
                    "version": 1,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "entries": doc,
                    "queue_running": True,
                }
            return doc
        except Exception as e:
            logger.warning(f"Failed to load queue: {e}")
            return {
                "version": 1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "entries": [],
                "queue_running": True,
            }

    def _save_queue(self, queue_name: str, queue_doc: Dict[str, Any]):
        """Save queue document to GCS."""
        queue_root = os.getenv("QUEUE_ROOT", "robyn-queues")
        blob_path = f"{queue_root}/{queue_name}/queue.json"
        blob = self.bucket.blob(blob_path)

        queue_doc["saved_at"] = datetime.now(timezone.utc).isoformat()

        blob.upload_from_string(
            json.dumps(queue_doc, indent=2),
            content_type="application/json",
        )

        logger.info(f"Saved queue: gs://{self.bucket_name}/{blob_path}")

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

    def _count_config_variants(self, config_data: Dict[str, Any]) -> int:
        """Count actual variants in a config."""
        variants_dict = config_data.get("variants", {})
        if not variants_dict:
            return 0
        
        combination_mode = config_data.get("combination_mode", "single")
        
        if combination_mode == "cartesian":
            # Cartesian product - multiply counts
            total = 1
            for variant_list in variants_dict.values():
                if isinstance(variant_list, list):
                    total *= len(variant_list)
            return total
        else:
            # Single dimension - sum counts
            total = 0
            for variant_list in variants_dict.values():
                if isinstance(variant_list, list):
                    total += len(variant_list)
            return total
    
    def list_config_files(self) -> List[Dict[str, Any]]:
        """List available benchmark configuration files."""
        benchmarks_dir = Path(__file__).parent.parent / "benchmarks"
        
        if not benchmarks_dir.exists():
            return []
        
        configs = []
        for config_file in benchmarks_dir.glob("*.json"):
            try:
                with open(config_file) as f:
                    config_data = json.load(f)
                    configs.append({
                        "file": config_file.name,
                        "path": str(config_file),
                        "name": config_data.get("name", ""),
                        "description": config_data.get("description", ""),
                        "variant_count": self._count_config_variants(config_data),
                        "combination_mode": config_data.get("combination_mode", "single"),
                    })
            except Exception as e:
                logger.warning(f"Failed to load {config_file}: {e}")
        
        return configs


class ResultsCollector:
    """Collects and analyzes benchmark results."""

    def __init__(self, bucket_name: str = GCS_BUCKET):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def _load_benchmark_plan(
        self, benchmark_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Load benchmark plan from GCS.
        
        Args:
            benchmark_id: The benchmark ID
            
        Returns:
            Plan dict if found, None otherwise
        """
        try:
            plan_blob = self.bucket.blob(
                f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
            )
            if not plan_blob.exists():
                return None
            return json.loads(plan_blob.download_as_bytes())
        except Exception as e:
            logger.error(f"Error loading benchmark plan: {e}")
            return None

    def collect_results(self, benchmark_id: str):
        """
        Collect results from all benchmark variants.

        Returns DataFrame (if pandas available) or dict of results.
        """
        # Load benchmark plan
        plan = self._load_benchmark_plan(benchmark_id)
        if not plan:
            raise FileNotFoundError(
                f"Benchmark plan not found: {benchmark_id}"
            )

        variants = plan.get("variants", [])
        
        if not variants:
            logger.warning(f"No variants found in benchmark plan")
            if pd is not None:
                return pd.DataFrame()
            return []

        logger.info(
            f"Collecting results for {len(variants)} variants..."
        )

        results = []
        for i, variant in enumerate(variants, 1):
            logger.info(
                f"  Processing variant {i}/{len(variants)}: "
                f"{variant.get('benchmark_variant', 'unknown')}"
            )
            try:
                result = self._collect_variant_result(variant, benchmark_id)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(
                    f"Error collecting variant {i}: {e}"
                )

        logger.info(f"Collected {len(results)} results")

        if not results:
            logger.warning(f"No results found for benchmark {benchmark_id}")
            if pd is not None:
                return pd.DataFrame()
            return []

        if pd is not None:
            return pd.DataFrame(results)
        return results

    def _collect_variant_result(
        self, variant: Dict[str, Any], benchmark_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Collect results for a single variant.

        Searches for model_summary.json in GCS based on benchmark metadata.
        """
        # Build search pattern for this variant's results
        # Results are stored at: robyn/<revision>/<country>/<timestamp>/
        country = variant.get("country", "")
        revision = variant.get("revision", "default")

        # Search for results matching this variant
        # We need to find the GCS path by matching benchmark metadata
        prefix = f"robyn/{revision}/{country}/"

        try:
            blobs = self.client.list_blobs(
                self.bucket_name, prefix=prefix
            )

            # Look for model_summary.json files and check metadata
            for blob in blobs:
                if "model_summary.json" in blob.name:
                    # Check if this summary matches our variant
                    summary = self._load_summary(blob.name)
                    if summary and self._matches_variant(
                        summary, variant, benchmark_id
                    ):
                        return self._extract_metrics(summary, variant)

        except Exception as e:
            logger.warning(
                f"Error collecting results for variant "
                f"{variant.get('benchmark_variant')}: {e}"
            )

        return None

    def _load_summary(self, blob_path: str) -> Optional[Dict[str, Any]]:
        """Load model_summary.json from GCS."""
        try:
            blob = self.bucket.blob(blob_path)
            if blob.exists():
                return json.loads(blob.download_as_bytes())
        except Exception as e:
            logger.debug(f"Failed to load summary {blob_path}: {e}")
        return None

    def _matches_variant(
        self,
        summary: Dict[str, Any],
        variant: Dict[str, Any],
        benchmark_id: str,
    ) -> bool:
        """
        Check if a model summary matches a benchmark variant.

        Matches based on benchmark metadata or other identifying fields.
        """
        # Check if summary has benchmark metadata
        # (Added to job params when submitting)
        # This would need to be passed through to the summary

        # For now, match on key parameters
        summary_meta = summary.get("input_metadata", {})

        # Match on country
        if (
            summary.get("country", "").lower()
            != variant.get("country", "").lower()
        ):
            return False

        # Match on adstock if specified
        if "adstock" in variant:
            if summary_meta.get("adstock") != variant.get("adstock"):
                return False

        # Match on other key fields as needed
        # This is a simplified matching - in production you'd want
        # more robust matching or include benchmark_id in the job config

        return True

    def _extract_metrics(
        self, summary: Dict[str, Any], variant: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract benchmark metrics from model summary."""
        best_model = summary.get("best_model", {})

        result = {
            # Benchmark metadata
            "benchmark_test": variant.get("benchmark_test", ""),
            "benchmark_variant": variant.get("benchmark_variant", ""),
            "country": variant.get("country", ""),
            "revision": variant.get("revision", ""),
            # Configuration
            "adstock": variant.get("adstock", ""),
            "train_size": str(variant.get("train_size", "")),
            "iterations": variant.get("iterations", ""),
            "trials": variant.get("trials", ""),
            "resample_freq": variant.get("resample_freq", "none"),
            # Model fit metrics
            "rsq_train": best_model.get("rsq_train"),
            "rsq_val": best_model.get("rsq_val"),
            "rsq_test": best_model.get("rsq_test"),
            "nrmse_train": best_model.get("nrmse_train"),
            "nrmse_val": best_model.get("nrmse_val"),
            "nrmse_test": best_model.get("nrmse_test"),
            "decomp_rssd": best_model.get("decomp_rssd"),
            "mape": best_model.get("mape"),
            # Model metadata
            "model_id": best_model.get("model_id"),
            "pareto_model_count": summary.get("pareto_model_count", 0),
            "candidate_model_count": summary.get(
                "candidate_model_count", 0
            ),
            # Execution metadata
            "training_time_mins": summary.get("training_time_mins"),
            "timestamp": summary.get("timestamp", ""),
            "created_at": summary.get("created_at", ""),
        }

        return result

    def export_results(
        self, benchmark_id: str, results, format: str = "csv"
    ):
        """Export results to GCS."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if format == "csv":
            output_path = (
                f"{BENCHMARK_ROOT}/{benchmark_id}/"
                f"results_{timestamp}.csv"
            )

            if pd is not None and isinstance(results, pd.DataFrame):
                csv_data = results.to_csv(index=False)
            else:
                # Manual CSV generation
                if not results:
                    logger.warning("No results to export")
                    return

                # Get all keys from first result
                keys = list(results[0].keys())
                lines = [",".join(keys)]

                for result in results:
                    values = [str(result.get(k, "")) for k in keys]
                    lines.append(",".join(values))

                csv_data = "\n".join(lines)

            blob = self.bucket.blob(output_path)
            blob.upload_from_string(csv_data, content_type="text/csv")

            logger.info(
                f"Exported results: gs://{self.bucket_name}/{output_path}"
            )

        elif format == "parquet":
            if pd is None:
                logger.error("pandas required for parquet export")
                return

            output_path = (
                f"{BENCHMARK_ROOT}/{benchmark_id}/"
                f"results_{timestamp}.parquet"
            )
            # Would need pyarrow for this
            logger.warning("Parquet export requires pyarrow")
            return

    def list_results(self, benchmark_id: str):
        """
        List all available results that might match a benchmark.
        
        Shows model results with metadata to help user identify their benchmark results.
        """
        print(f"\nSearching for results matching benchmark: {benchmark_id}")
        print("=" * 80)
        
        # Load benchmark plan to get variants
        plan = self._load_benchmark_plan(benchmark_id)
        if not plan:
            print(f"⚠️  Could not load benchmark plan for {benchmark_id}")
            print("Searching for all recent results instead...\n")
            variants = []
        else:
            variants = plan.get("variants", [])
            print(f"Benchmark has {len(variants)} variants")
            print(f"Created: {plan.get('created_at', 'unknown')}\n")
        
        # Search for results
        results_found = 0
        
        for variant in variants:
            country = variant.get("country", "")
            revision = variant.get("revision", "default")
            adstock = variant.get("adstock", "")
            variant_name = variant.get("benchmark_variant", "")
            
            print(f"Variant: {variant_name} (adstock: {adstock})")
            print(f"Looking in: robyn/{revision}/{country}/")
            
            # List recent results
            prefix = f"robyn/{revision}/{country}/"
            try:
                blobs = list(self.bucket.list_blobs(prefix=prefix))
                summaries = [b for b in blobs if "model_summary.json" in b.name]
                
                if summaries:
                    print(f"  Found {len(summaries)} model result(s)")
                    for blob in summaries[:5]:  # Show first 5
                        print(f"    - {blob.name}")
                        print(f"      Created: {blob.time_created}")
                    results_found += len(summaries)
                else:
                    print(f"  ⚠️  No results found")
            except Exception as e:
                print(f"  Error searching: {e}")
            
            print()
        
        if results_found == 0:
            print("❌ No results found for any variants")
            print("\nPossible reasons:")
            print("  1. Jobs haven't completed yet")
            print("  2. Jobs failed during execution")
            print("  3. Results saved to different location")
            print(f"\n💡 Use --show-results-location {benchmark_id} to see expected paths")
        else:
            print(f"✅ Found {results_found} result file(s)")
            print("\n💡 To access results manually:")
            print(f"  gsutil ls gs://{self.bucket_name}/robyn/")
    
    def show_results_location(self, benchmark_id: str):
        """
        Show where results should be located for a benchmark.
        
        Provides GCS paths and manual access instructions.
        """
        print(f"\nResults Location Information")
        print("=" * 80)
        
        # Load benchmark plan
        plan = self._load_benchmark_plan(benchmark_id)
        if not plan:
            print(f"⚠️  Could not load benchmark plan for {benchmark_id}")
            print(f"Expected location: gs://{self.bucket_name}/{BENCHMARK_ROOT}/{benchmark_id}/plan.json")
            return
        
        print(f"Benchmark: {plan.get('name', 'unknown')}")
        print(f"Description: {plan.get('description', '')}")
        print(f"Created: {plan.get('created_at', 'unknown')}")
        print(f"Variants: {plan.get('variant_count', 0)}")
        print()
        
        variants = plan.get("variants", [])
        
        print("Expected Results Locations:")
        print("-" * 80)
        
        for i, variant in enumerate(variants, 1):
            country = variant.get("country", "")
            revision = variant.get("revision", "default")
            variant_name = variant.get("benchmark_variant", "")
            
            print(f"\n{i}. Variant: {variant_name}")
            print(f"   Country: {country}")
            print(f"   Revision: {revision}")
            print(f"   Path: gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_HHMMSS/")
            print(f"   Contains:")
            print(f"     - model_summary.json  (metrics and metadata)")
            print(f"     - best_model_plots.png (visualizations)")
            print(f"     - model_params.json   (configuration)")
        
        print("\n" + "=" * 80)
        print("Manual Access Commands:")
        print("-" * 80)
        
        # Provide gsutil commands
        for variant in variants[:1]:  # Show example for first variant
            country = variant.get("country", "")
            revision = variant.get("revision", "default")
            
            print(f"\n# List all results for {country}:")
            print(f"gsutil ls gs://{self.bucket_name}/robyn/{revision}/{country}/")
            
            print(f"\n# View a specific model summary:")
            print(f"gsutil cat gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_HHMMSS/model_summary.json | jq .")
            
            print(f"\n# Download all results:")
            print(f"gsutil -m cp -r gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_*/ ./results/")
        
        print("\n" + "=" * 80)
        print(f"\n💡 To list available results:")
        print(f"  python scripts/benchmark_mmm.py --list-results {benchmark_id}")


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
    parser.add_argument(
        "--queue-name",
        type=str,
        default=os.getenv("DEFAULT_QUEUE_NAME", "default"),
        help="Queue name for job submission (default: from DEFAULT_QUEUE_NAME env var or 'default')",
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Generate and save plan but don't submit to queue",
    )
    parser.add_argument(
        "--trigger-queue",
        action="store_true",
        help="Trigger queue processing after submitting (useful when scheduler is disabled)",
    )
    parser.add_argument(
        "--trigger-count",
        type=int,
        default=None,
        help="Number of queue ticks to trigger (default: number of variants submitted)",
    )
    parser.add_argument(
        "--list-results",
        type=str,
        help="List all available results for a benchmark ID",
    )
    parser.add_argument(
        "--show-results-location",
        type=str,
        help="Show where results are located for a benchmark ID",
    )
    parser.add_argument(
        "--test-run",
        action="store_true",
        help="Run quick test with minimal iterations (10) and trials (1), first variant only",
    )

    args = parser.parse_args()

    runner = BenchmarkRunner()

    if args.list_configs:
        configs = runner.list_config_files()
        if not configs:
            print("No benchmark configuration files found in benchmarks/ directory")
            return

        print("\nAvailable Benchmark Configurations:")
        print("=" * 80)
        for cfg in configs:
            print(f"\nFile: {cfg['file']}")
            print(f"Name: {cfg['name']}")
            print(f"Description: {cfg['description']}")
            print(f"Estimated variants: {cfg['variant_count']}")
            print(f"Path: {cfg['path']}")
            print("-" * 80)
        
        print(f"\nTotal: {len(configs)} configuration(s)")
        print("\nTo run a benchmark:")
        print(f"  python scripts/benchmark_mmm.py --config benchmarks/<filename>")
        return

    if args.list_results:
        try:
            collector = ResultsCollector()
            collector.list_results(args.list_results)
        except Exception as e:
            print(f"\n❌ Error: Could not access Google Cloud Storage")
            print(f"   {str(e)}")
            print("\nThis command requires Google Cloud credentials.")
            print("\nPlease set up credentials using ONE of these methods:")
            print("\n1. Set environment variable:")
            print("   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json")
            print("\n2. Use gcloud auth:")
            print("   gcloud auth application-default login")
            print("\nThen retry the command.")
            sys.exit(1)
        return

    if args.show_results_location:
        try:
            collector = ResultsCollector()
            collector.show_results_location(args.show_results_location)
        except Exception as e:
            print(f"\n❌ Error: Could not access Google Cloud Storage")
            print(f"   {str(e)}")
            print("\nThis command requires Google Cloud credentials.")
            print("\nPlease set up credentials using ONE of these methods:")
            print("\n1. Set environment variable:")
            print("   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json")
            print("\n2. Use gcloud auth:")
            print("   gcloud auth application-default login")
            print("\nThen retry the command.")
            sys.exit(1)
        return

    if args.collect_results:
        if pd is None:
            logger.error("pandas is required for results collection")
            sys.exit(1)

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

    # Override iterations/trials in base config
    base_config["iterations"] = benchmark_config.iterations
    base_config["trials"] = benchmark_config.trials

    # Generate variants with error handling
    try:
        variants = runner.generate_variants(base_config, benchmark_config)
        logger.info(f"Generated {len(variants)} test variants")
    except Exception as e:
        logger.error(f"Error generating variants: {e}", exc_info=True)
        print(f"\n❌ Error generating variants: {e}")
        print(f"\nConfig file: {args.config}")
        print(f"Base config: {benchmark_config.base_config}")
        print("\nPlease check:")
        print("  - Benchmark configuration syntax")
        print("  - Variant specifications are valid")
        print("  - Base config exists and is accessible")
        sys.exit(1)
    
    # Validate variants were generated
    if not variants:
        logger.error("No variants generated! Check your configuration.")
        print("\n❌ Error: No variants were generated")
        print("\nPossible issues:")
        print("  - Check that your config has valid variant specifications")
        print("  - Verify the base config exists")
        print(f"  - Config file: {args.config}")
        sys.exit(1)

    # Generate benchmark ID
    benchmark_id = (
        f"{benchmark_config.name}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )

    # Save benchmark plan
    runner.save_benchmark_plan(benchmark_id, benchmark_config, variants)

    if args.dry_run:
        logger.info("Dry run - not submitting jobs")
        print(f"\nGenerated {len(variants)} variants:")
        for i, variant in enumerate(variants, 1):
            test = variant.get("benchmark_test", "unknown")
            name = variant.get("benchmark_variant", "unnamed")
            print(f"{i}. {test}: {name}")
        print(f"\nBenchmark ID: {benchmark_id}")
        print(
            f"Plan saved: gs://{runner.bucket_name}/"
            f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        )
        return
    
    if args.test_run:
        if not variants:
            logger.error("Cannot run test - no variants generated")
            print("\n❌ Error: Cannot run test with empty variants list")
            sys.exit(1)
            
        logger.info("🧪 TEST RUN MODE - Running first variant with minimal settings")
        print("\n🧪 TEST RUN MODE")
        print(f"Iterations: 10 (reduced from {benchmark_config.iterations})")
        print(f"Trials: 1 (reduced from {benchmark_config.trials})")
        print(f"Testing variant: {variants[0].get('benchmark_variant', 'first')}")
        
        # Modify first variant for test
        test_variants = [variants[0].copy()]
        test_variants[0]["iterations"] = 10
        test_variants[0]["trials"] = 1
        variants = test_variants
        
        # Update benchmark_id to indicate test
        benchmark_id = f"{benchmark_id}_test"

    if args.no_submit:
        logger.info("--no-submit flag set - variants saved but not queued")
        print(f"\nBenchmark ID: {benchmark_id}")
        print(f"Variants saved: {len(variants)}")
        print(
            f"Plan: gs://{runner.bucket_name}/"
            f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        )
        return

    # Submit variants to queue
    try:
        submitted_count = runner.submit_variants_to_queue(
            benchmark_id, variants, queue_name=args.queue_name
        )

        print(f"\n✅ Benchmark submitted successfully!")
        print(f"Benchmark ID: {benchmark_id}")
        print(f"Variants queued: {submitted_count}")
        print(f"Queue: {args.queue_name}")
        print(
            f"Plan: gs://{runner.bucket_name}/"
            f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        )

        # Trigger queue processing if requested
        if args.trigger_queue:
            print(f"\n🔄 Triggering queue processing...")
            trigger_count = args.trigger_count or submitted_count

            try:
                # Call the trigger_queue script
                import subprocess

                trigger_script = (
                    Path(__file__).parent / "trigger_queue.py"
                )
                cmd = [
                    sys.executable,
                    str(trigger_script),
                    "--queue-name",
                    args.queue_name,
                    "--count",
                    str(trigger_count),
                    "--delay",
                    "10",  # 10 second delay between ticks
                    "--resume-queue",  # Auto-resume if paused
                ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600
                )

                if result.returncode == 0:
                    print(result.stdout)
                    print(
                        f"\n✅ Queue processing triggered for {trigger_count} job(s)"
                    )
                else:
                    # Show both stdout and stderr for better debugging
                    if result.stdout:
                        print(f"\n⚠️  Queue trigger output:")
                        print(result.stdout)
                    if result.stderr:
                        print(f"\n⚠️  Queue trigger failed:")
                        print(result.stderr)
                    print(
                        "\nYou can manually trigger queue processing with:"
                    )
                    print(
                        f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue"
                    )

            except Exception as e:
                logger.error(f"Failed to trigger queue: {e}")
                print(
                    f"\n⚠️  Could not automatically trigger queue: {e}"
                )
                print("You can manually trigger queue processing with:")
                print(
                    f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue"
                )
        else:
            print(
                f"\n💡 Monitor progress in the Streamlit app "
                f"(Run Experiment → Queue Monitor)"
            )
            print(
                f"\nOr manually trigger queue processing with:"
            )
            print(
                f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue --until-empty"
            )

    except Exception as e:
        logger.error(f"Failed to submit jobs: {e}")
        print(f"\n❌ Error submitting jobs: {e}")
        print(
            f"Benchmark plan saved but jobs not queued: "
            f"{benchmark_id}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
