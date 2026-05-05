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
from datetime import datetime, timedelta, timezone
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


def _resample_freq_to_frequency(resample_freq: Optional[str]) -> str:
    """Map a resample_freq value to a frequency key used in ranges config.

    Args:
        resample_freq: The resample_freq field value from a variant
            (e.g. "none", "W", "MS").

    Returns:
        One of "daily", "weekly", or "monthly".
    """
    freq_upper = str(resample_freq).upper().strip() if resample_freq else ""
    if freq_upper in ("W", "WEEKLY"):
        return "weekly"
    if freq_upper in ("M", "MS", "MONTHLY"):
        return "monthly"
    return "daily"


class HyperparameterRangesConfig:
    """Loads and queries a generic hyperparameter ranges config (v2 format).

    The config JSON contains per-frequency, per-adstock-type,
    per-channel-type, and per-preset ranges for Robyn hyperparameters.
    """

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = json.load(f)

    def get_ranges(
        self,
        frequency: str,
        adstock_type: str,
        channel_type: Optional[str],
        preset: str = "balanced",
    ) -> Optional[Dict[str, List[float]]]:
        """Return hyperparameter ranges for the given combination.

        Falls back to the ``_default`` entry when no channel-specific
        ranges exist for the requested ``adstock_type``.

        Args:
            frequency: One of "daily", "weekly", "monthly".
            adstock_type: One of "geometric", "weibull_cdf", "weibull_pdf".
            channel_type: Channel type key (e.g. "search_brand"), or None
                to use the ``_default`` entry.
            preset: One of "conservative", "balanced", "exploratory", "fb", or "meshed".

        Returns:
            Dict with range lists (e.g. ``{"alpha": [0.5, 3.0], ...}``),
            or None if the combination is not found.
        """
        ranges = self.config.get("ranges", {})
        freq_ranges = ranges.get(frequency, {})
        adstock_ranges = freq_ranges.get(adstock_type, {})

        # Try the requested channel type first, then fall back to _default.
        channel_ranges = None
        if channel_type:
            channel_ranges = adstock_ranges.get(channel_type)
        if channel_ranges is None:
            channel_ranges = adstock_ranges.get("_default")
        if channel_ranges is None:
            return None

        return channel_ranges.get(preset)

    def resolve_custom_hyperparameters(
        self,
        var_names: List[str],
        adstock_type: str,
        frequency: str,
        channel_type_mapping: Dict[str, str],
        preset: str = "balanced",
    ) -> Dict[str, Any]:
        """Build a ``custom_hyperparameters`` dict for all variables.

        Each variable is looked up using the channel type from
        ``channel_type_mapping``.  Variables without a mapping entry
        use the ``_default`` ranges for the adstock type.

        Args:
            var_names: List of paid-media or organic variable names.
            adstock_type: Adstock type ("geometric", "weibull_cdf", …).
            frequency: Frequency key ("daily", "weekly", "monthly").
            channel_type_mapping: Maps variable names to channel types.
            preset: Preset to use ("balanced", "conservative", "exploratory", "fb", or "meshed").

        Returns:
            Dict with keys like ``"{var}_alphas"``, ``"{var}_thetas"``, etc.
        """
        custom_hp: Dict[str, Any] = {}

        for var_name in var_names:
            channel_type = channel_type_mapping.get(var_name)
            ranges = self.get_ranges(
                frequency, adstock_type, channel_type, preset
            )
            if ranges is None:
                logger.info(
                    f"  No ranges found for '{var_name}' "
                    f"(channel_type={channel_type!r}, "
                    f"{frequency}/{adstock_type}/{preset}) — "
                    "using Robyn defaults for this variable"
                )
                continue

            logger.info(
                f"  '{var_name}' → type={channel_type!r} → "
                f"{', '.join(f'{k}={ranges[k]}' for k in ('alpha', 'gamma', 'theta', 'shape', 'scale') if k in ranges)}"  # noqa: E501
            )
            if "alpha" in ranges:
                custom_hp[f"{var_name}_alphas"] = ranges["alpha"]
            if "gamma" in ranges:
                custom_hp[f"{var_name}_gammas"] = ranges["gamma"]
            if "theta" in ranges:
                custom_hp[f"{var_name}_thetas"] = ranges["theta"]
            if "shape" in ranges:
                custom_hp[f"{var_name}_shapes"] = ranges["shape"]
            if "scale" in ranges:
                custom_hp[f"{var_name}_scales"] = ranges["scale"]

        return custom_hp


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

    @property
    def hyperparameter_ranges_config(self) -> Optional[str]:
        """Optional path to a hyperparameter ranges config JSON file.

        When set, per-channel hyperparameter ranges are resolved from
        this file and passed as ``custom_hyperparameters`` to each job.
        The path is relative to the repository root.
        """
        return self.config.get("hyperparameter_ranges_config")

    @property
    def channel_type_assignments_config(self) -> Optional[str]:
        """Path to a JSON file that maps variable names to channel types.

        The file must contain an ``"assignments"`` key whose value is a
        flat dict of ``{"VARIABLE_NAME": "channel_type"}``.  The path is
        relative to the repository root.

        Example file (``benchmarks/channel_type_assignments.json``)::

            {
                "name": "channel_type_assignments",
                "description": "...",
                "assignments": {
                    "GA_BRAND_SESSIONS": "search_brand",
                    "TV_COSTS": "tv_offline"
                }
            }

        Takes precedence over the inline ``channel_type_mapping`` field.
        """
        return self.config.get("channel_type_assignments_config")

    @property
    def channel_type_mapping(self) -> Dict[str, str]:
        """Map of variable name → channel type used for range lookups.

        Prefer ``channel_type_assignments_config`` (external file) over
        this inline dict.  This property is kept for backward
        compatibility when no external file is configured.

        Example::

            {
                "GA_BRAND_SESSIONS": "search_brand",
                "TV_COSTS": "tv_offline"
            }
        """
        return self.config.get("channel_type_mapping", {})

    @property
    def hyperparameter_preset(self) -> str:
        """Preset to use when resolving hyperparameter ranges.

        One of "conservative", "balanced" (default), "exploratory", "fb", or "meshed".
        """
        return self.config.get("hyperparameter_preset", "balanced")


class BenchmarkRunner:
    """Manages benchmark test execution."""

    def __init__(self, bucket_name: str = GCS_BUCKET):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def _find_latest_mapped_version(self, country: str) -> Optional[str]:
        """Find the most recent mapped-dataset version for a country.

        Scans ``mapped-datasets/{country}/`` and returns the newest timestamp
        folder that contains a ``raw.parquet`` blob, or *None* if none exist.
        """
        prefix = f"mapped-datasets/{country.lower()}/"
        blobs = list(self.bucket.list_blobs(prefix=prefix))
        versions = set()
        for blob in blobs:
            parts = blob.name.split("/")
            # mapped-datasets/<country>/<ts>/raw.parquet  → 4 parts
            if len(parts) == 4 and parts[-1] == "raw.parquet":
                ts = parts[2]
                if ts != "latest" and len(ts) == 15 and "_" in ts:
                    try:
                        datetime.strptime(ts, "%Y%m%d_%H%M%S")
                        versions.add(ts)
                    except ValueError:
                        continue
        if not versions:
            return None
        return sorted(versions, reverse=True)[0]

    def _find_latest_version(self, country: str, goal: str) -> str:
        """Find the most recent version (timestamp) for a country/goal combination."""
        prefix = f"training_data/{country.lower()}/{goal}/"

        # List all "folders" (prefixes) under this path
        blobs = self.bucket.list_blobs(prefix=prefix, delimiter="/")

        # Consume the iterator to populate prefixes
        _ = list(blobs)

        # Get all version folders
        versions = []
        for prefix_path in blobs.prefixes:
            # Extract version from path like "training_data/de/N_UPLOADS_WEB/20260122_113141/"
            version = prefix_path.rstrip("/").split("/")[-1]
            # Filter for timestamp pattern (YYYYMMDD_HHMMSS)
            if len(version) == 15 and "_" in version:
                try:
                    # Validate it's a timestamp
                    datetime.strptime(version, "%Y%m%d_%H%M%S")
                    versions.append(version)
                except ValueError:
                    continue

        if not versions:
            raise FileNotFoundError(
                f"No versions found at gs://{self.bucket_name}/{prefix}"
            )

        # Sort and return most recent
        versions.sort(reverse=True)
        latest = versions[0]
        logger.info(f"ℹ️  Resolved 'Latest' to most recent version: {latest}")
        return latest

    def load_base_config(
        self, country: str, goal: str, version: str
    ) -> Dict[str, Any]:
        """Load selected_columns.json from GCS."""
        # Handle special "Latest" version
        if version.lower() == "latest":
            version = self._find_latest_version(country, goal)

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
        """Generate test configuration variants.

        After building variants from dimension specs, applies per-channel
        hyperparameter ranges if ``hyperparameter_ranges_config`` is set
        in the benchmark config.
        """
        combination_mode = benchmark_config.config.get(
            "combination_mode", "single"
        )

        if combination_mode == "cartesian":
            # Generate cartesian product of all dimensions
            variants = self._generate_cartesian_variants(
                base_config, benchmark_config
            )
        else:
            # Generate variants for each dimension separately (default)
            variants = self._generate_single_variants(
                base_config, benchmark_config
            )

        # Apply per-channel hyperparameter ranges when configured.
        variants = self._apply_hyperparameter_ranges(variants, benchmark_config)

        return variants

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

        # Generate seasonality window variants.
        # In sequential mode a single "full" window spec that carries no
        # date override is equivalent to the base-config default and does
        # NOT add a meaningful test dimension — skip it so the variant
        # count stays at 1+3+2+5+3 = 14 for the standard DK benchmark.
        if "seasonality_window" in variant_specs:
            window_specs = variant_specs["seasonality_window"]
            non_trivial_windows = [
                w
                for w in window_specs
                if "weeks_back" in w or "start_date" in w or "end_date" in w
            ]
            if non_trivial_windows:
                variants.extend(
                    self._generate_seasonality_variants(
                        base_config, non_trivial_windows
                    )
                )
            elif len(window_specs) > 1:
                # All windows are "full" (no override) — still worth sweeping
                variants.extend(
                    self._generate_seasonality_variants(
                        base_config, window_specs
                    )
                )

        # Generate hyperparameter preset comparison variants
        if "hyperparameter_preset" in variant_specs:
            variants.extend(
                self._generate_preset_variants(
                    base_config, variant_specs["hyperparameter_preset"]
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
            dimension_variants["time_aggregation"] = (
                self._generate_time_agg_variants(
                    base_config, variant_specs["time_aggregation"]
                )
            )

        if "spend_var_mapping" in variant_specs:
            dimension_variants["spend_var_mapping"] = (
                self._generate_spend_var_variants(
                    base_config, variant_specs["spend_var_mapping"]
                )
            )

        if "seasonality_window" in variant_specs:
            dimension_variants["seasonality_window"] = (
                self._generate_seasonality_variants(
                    base_config, variant_specs["seasonality_window"]
                )
            )

        if "hyperparameter_preset" in variant_specs:
            dimension_variants["hyperparameter_preset"] = (
                self._generate_preset_variants(
                    base_config, variant_specs["hyperparameter_preset"]
                )
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
                variant_name_parts.append(
                    variant_config.get("benchmark_variant", "")
                )

            # Create combined name
            merged["benchmark_variant"] = "_".join(variant_name_parts)
            merged["benchmark_test"] = "combination"
            merged["benchmark_description"] = (
                f"Combination: {', '.join(variant_name_parts)}"
            )

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

            mapping_type = spec.get("mapping_strategy")

            if mapping_type == "spend_to_spend":
                # All channels: spend → spend
                variant["paid_media_vars"] = variant["paid_media_spends"]
                variant["var_to_spend_mapping"] = {
                    spend: spend for spend in variant["paid_media_spends"]
                }

            elif mapping_type == "spend_to_proxy":
                # All channels: spend → proxy
                # Use provided proxy mapping or default pattern
                proxy_map = spec.get("proxy_mapping", {})
                variant["var_to_spend_mapping"] = proxy_map
                if proxy_map:
                    # Derive paid_media_vars from proxy map values, ordered by
                    # paid_media_spends so Robyn receives different inputs
                    variant["paid_media_vars"] = [
                        proxy_map.get(s, s)
                        for s in variant.get("paid_media_spends", [])
                    ]

            elif mapping_type in ("mixed_by_funnel", "mixed"):
                # Both "mixed" (used in JSON configs) and "mixed_by_funnel"
                # (legacy alias) are accepted here for forward compatibility.
                # Upper funnel → proxy, lower funnel → spend
                upper_channels = spec.get("upper_funnel_channels", [])
                lower_channels = spec.get("lower_funnel_channels", [])
                proxy_map = spec.get("proxy_mapping", {})

                mapping = {}
                for spend in variant.get("paid_media_spends", []):
                    if spend in upper_channels:
                        mapping[spend] = proxy_map.get(spend, spend)
                    elif spend in lower_channels:
                        mapping[spend] = spend
                    else:
                        # Default to spend
                        mapping[spend] = spend

                variant["var_to_spend_mapping"] = mapping
                if upper_channels or lower_channels:
                    # Derive paid_media_vars from the computed channel mapping
                    variant["paid_media_vars"] = [
                        mapping.get(s, s)
                        for s in variant.get("paid_media_spends", [])
                    ]

            # Apply explicit override last — always wins over derived values.
            # Also rebuild var_to_spend_mapping so Robyn can trace each proxy
            # variable back to its spend column.
            if "paid_media_vars_override" in spec:
                override = spec["paid_media_vars_override"]
                variant["paid_media_vars"] = override
                spends = variant.get("paid_media_spends", [])
                if len(spends) == len(override):
                    variant["var_to_spend_mapping"] = {
                        var: spend for var, spend in zip(override, spends)
                    }
                elif spends and override:
                    # Lengths differ — create best-effort mapping using the
                    # shorter of the two lists (zip stops at the shorter one).
                    min_len = min(len(spends), len(override))
                    logger.warning(
                        f"paid_media_vars_override length ({len(override)}) "
                        f"!= paid_media_spends length ({len(spends)}) for "
                        f"variant '{spec.get('name', '?')}'. "
                        f"var_to_spend_mapping will only cover the first "
                        f"{min_len} pair(s)."
                    )
                    variant["var_to_spend_mapping"] = {
                        var: spend for var, spend in zip(override, spends)
                    }

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
            variant["adstock"] = spec.get("adstock")

            # Optional: specify hyperparameter preset
            if "hyperparameter_preset" in spec:
                variant["hyperparameter_preset"] = spec["hyperparameter_preset"]

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
            variant["resample_freq"] = spec.get("resample_freq")

            variants.append(variant)

        return variants

    def _generate_seasonality_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate seasonality window variants.

        Each spec may provide either explicit ``start_date``/``end_date``
        strings or a ``weeks_back`` integer.  When ``weeks_back`` is given
        the start date is computed relative to the ``end_date`` found in
        ``base_config`` (falling back to today when absent).  Specs with
        neither field leave the base-config training window unchanged
        (i.e. the "full history" variant).
        """
        variants = []

        # Resolve reference end date once for all weeks_back specs
        ref_end_date_str = base_config.get("end_date", "")
        if ref_end_date_str:
            try:
                ref_end_dt = datetime.strptime(ref_end_date_str, "%Y-%m-%d")
            except ValueError:
                ref_end_dt = datetime.now()
                logger.warning(
                    f"Could not parse end_date '{ref_end_date_str}' "
                    "(expected YYYY-MM-DD format); using today for "
                    "weeks_back resolution"
                )
        else:
            ref_end_dt = datetime.now()

        for spec in specs:
            variant = base_config.copy()
            variant["benchmark_test"] = "seasonality_window"
            variant["benchmark_variant"] = spec.get("name", "unnamed")
            variant["benchmark_description"] = spec.get("description", "")
            # Preserve the human-readable window name so it survives
            # cartesian merging and can be tracked in results (mirrors
            # preset_label used by _generate_preset_variants).
            variant["window_label"] = spec.get("name", "")

            # Resolve weeks_back to absolute start_date
            if "weeks_back" in spec:
                weeks = spec["weeks_back"]
                start_dt = ref_end_dt - timedelta(weeks=weeks)
                variant["start_date"] = start_dt.strftime("%Y-%m-%d")
                variant["end_date"] = ref_end_dt.strftime("%Y-%m-%d")
            else:
                # Override start/end dates when provided explicitly
                if "start_date" in spec:
                    variant["start_date"] = spec["start_date"]
                if "end_date" in spec:
                    variant["end_date"] = spec["end_date"]

            variants.append(variant)

        return variants

    def _generate_preset_variants(
        self, base_config: Dict[str, Any], specs: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate hyperparameter preset comparison variants.

        Each spec is a dict with at minimum a ``"name"`` key that matches
        a valid preset name (``"conservative"``, ``"balanced"``,
        ``"exploratory"``, ``"fb"``, or ``"meshed"``).  The ``"name"``
        value is used as both the ``benchmark_variant`` label and the
        ``hyperparameter_preset`` value forwarded to the training job.

        Args:
            base_config: Base configuration dict.
            specs: List of preset spec dicts, e.g.
                ``[{"name": "balanced"}, {"name": "fb"}, {"name": "meshed"}]``.

        Returns:
            List of variant dicts, one per preset.
        """
        variants = []

        for spec in specs:
            preset_name = spec.get("name", "balanced")
            variant = base_config.copy()
            variant["benchmark_test"] = "hyperparameter_preset"
            variant["benchmark_variant"] = preset_name
            variant["benchmark_description"] = spec.get(
                "description",
                f"Hyperparameter preset: {preset_name}",
            )
            variant["hyperparameter_preset"] = preset_name
            # Preserve original preset name so it survives the
            # "Custom" overwrite performed by _apply_hyperparameter_ranges.
            variant["preset_label"] = preset_name
            variants.append(variant)

        return variants

    def _apply_hyperparameter_ranges(
        self,
        variants: List[Dict[str, Any]],
        benchmark_config: BenchmarkConfig,
    ) -> List[Dict[str, Any]]:
        """Apply per-channel hyperparameter ranges to variants.

        When ``benchmark_config.hyperparameter_ranges_config`` is set,
        this method loads the ranges config file, resolves per-channel
        hyperparameter ranges for every variant (based on its adstock
        type and resample frequency), and stores the result as
        ``custom_hyperparameters`` on the variant.  The
        ``hyperparameter_preset`` field is also set to ``"Custom"`` so
        the R training script uses the resolved ranges directly.

        **Preset precedence**: each variant may carry its own
        ``hyperparameter_preset`` value (e.g. set by an adstock spec such
        as ``"Meta default"`` for weibull_cdf).  That variant-level preset
        takes precedence over the benchmark-level
        ``benchmark_config.hyperparameter_preset``.  If neither is set,
        the default is ``"balanced"``.

        Variants that already carry ``custom_hyperparameters`` are left
        unchanged (their existing custom ranges take precedence).

        Args:
            variants: List of generated variant dicts.
            benchmark_config: The active benchmark configuration.

        Returns:
            The same list of variants, with ``custom_hyperparameters``
            added where applicable.
        """
        ranges_config_path = benchmark_config.hyperparameter_ranges_config
        if not ranges_config_path:
            return variants

        # Resolve path relative to the repository root.
        resolved_path = Path(ranges_config_path)
        if not resolved_path.is_absolute():
            resolved_path = Path(__file__).parent.parent / ranges_config_path

        if not resolved_path.exists():
            logger.warning(
                f"Hyperparameter ranges config not found: "
                f"{resolved_path}. Skipping per-channel ranges."
            )
            return variants

        try:
            hp_config = HyperparameterRangesConfig(str(resolved_path))
        except Exception as e:
            logger.warning(
                f"Failed to load hyperparameter ranges config "
                f"'{resolved_path}': {e}. Skipping per-channel ranges."
            )
            return variants

        # Load channel type assignments from an external file when configured,
        # otherwise fall back to the inline channel_type_mapping dict.
        assignments_config_path = (
            benchmark_config.channel_type_assignments_config
        )
        if assignments_config_path:
            assignments_path = Path(assignments_config_path)
            if not assignments_path.is_absolute():
                assignments_path = (
                    Path(__file__).parent.parent / assignments_config_path
                )
            try:
                with open(assignments_path) as f:
                    assignments_doc = json.load(f)
                channel_type_mapping = assignments_doc.get("assignments", {})
                logger.info(
                    f"Loaded {len(channel_type_mapping)} channel type "
                    f"assignment(s) from '{assignments_path.name}'"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to load channel type assignments from "
                    f"'{assignments_path}': {e}. Falling back to inline "
                    f"channel_type_mapping."
                )
                channel_type_mapping = benchmark_config.channel_type_mapping
        else:
            channel_type_mapping = benchmark_config.channel_type_mapping

        # benchmark-level preset acts as the default; individual variants
        # may override it (e.g. an adstock spec that sets "Meta default"
        # for weibull_cdf).  See preset-precedence note in the docstring.
        benchmark_preset = benchmark_config.hyperparameter_preset

        logger.info(
            f"Applying hyperparameter ranges from '{resolved_path.name}' "
            f"(benchmark preset: {benchmark_preset}, "
            f"per-variant overrides respected) "
            f"to {len(variants)} variant(s)"
        )

        updated_variants = []
        for variant in variants:
            # Don't overwrite explicit custom_hyperparameters.
            if "custom_hyperparameters" in variant:
                logger.debug(
                    f"Variant '{variant.get('benchmark_variant')}': "
                    f"skipping range resolution — custom_hyperparameters "
                    f"already set"
                )
                updated_variants.append(variant)
                continue

            # Respect the variant-level preset (e.g. set by an adstock spec)
            # and fall back to the benchmark-level preset when absent.
            preset = variant.get("hyperparameter_preset") or benchmark_preset

            adstock_type = variant.get("adstock", "geometric")
            resample_freq = variant.get("resample_freq", "none")
            frequency = _resample_freq_to_frequency(resample_freq)

            paid_media_vars = variant.get("paid_media_vars", [])
            organic_vars = variant.get("organic_vars", [])
            all_vars = list(paid_media_vars) + list(organic_vars)

            custom_hp = hp_config.resolve_custom_hyperparameters(
                all_vars,
                adstock_type,
                frequency,
                channel_type_mapping,
                preset,
            )

            updated = variant.copy()
            if custom_hp:
                updated["custom_hyperparameters"] = custom_hp
                # Preserve the human-readable preset name so it survives
                # the "Custom" overwrite and can be tracked in results.
                if "preset_label" not in updated:
                    updated["preset_label"] = preset
                updated["hyperparameter_preset"] = "Custom"
                # Summarise per-variable resolution so the operator can
                # verify the preset is being applied.
                resolved_channels = sorted(
                    {
                        k.rsplit("_", 1)[0]
                        for k in custom_hp
                        if not k.endswith("_min") and not k.endswith("_max")
                    }
                )
                logger.info(
                    f"Variant '{variant.get('benchmark_variant')}': "
                    f"resolved {len(custom_hp)} hyperparameter keys for "
                    f"{len(resolved_channels)} variable(s) "
                    f"[{frequency}/{adstock_type}/{preset}]: "
                    + ", ".join(resolved_channels)
                )

            updated_variants.append(updated)

        return updated_variants

    def save_benchmark_plan(
        self,
        benchmark_id: str,
        benchmark_config: BenchmarkConfig,
        variants: List[Dict[str, Any]],
    ):
        """Save benchmark execution plan and combinations log to GCS."""
        # Infer run_mode from iteration count so the UI can display a badge
        _iters = benchmark_config.iterations
        if _iters < 100:
            _run_mode = "test"
        elif _iters < 1500:
            _run_mode = "standard"
        elif _iters < 3500:
            _run_mode = "extended"
        else:
            _run_mode = "production"

        plan = {
            "benchmark_id": benchmark_id,
            "name": benchmark_config.name,
            "description": benchmark_config.description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "planned",
            "run_mode": _run_mode,
            "iterations": benchmark_config.iterations,
            "trials": benchmark_config.trials,
            "variant_count": len(variants),
            "variants": variants,
        }

        blob_path = f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        blob = self.bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(plan, indent=2), content_type="application/json"
        )

        logger.info(
            f"Saved benchmark plan: " f"gs://{self.bucket_name}/{blob_path}"
        )

        # Write a human-readable combinations log alongside plan.json so
        # operators can quickly verify which configs will be trained.
        lines = [
            f"Benchmark: {benchmark_id}",
            f"Name     : {benchmark_config.name}",
            f"Variants : {len(variants)}",
            f"Created  : {plan['created_at']}",
            "",
            f"{'#':<4}  {'variant':<52}  {'adstock':<14}  "
            f"{'train_size':<14}  {'resample_freq':<10}  "
            f"{'paid_media_vars':<36}  {'organic_vars':<28}  "
            f"{'context_vars':<36}  factor_vars",
            "-" * 200,
        ]

        def _preview(lst: list, n: int = 3) -> str:
            """Comma-joined first *n* items with overflow indicator."""
            if not lst:
                return "(none)"
            preview = ", ".join(lst[:n])
            if len(lst) > n:
                preview += f" ... (+{len(lst) - n})"
            return preview

        for idx, v in enumerate(variants, 1):
            pmv = v.get("paid_media_vars") or []
            ctx = v.get("context_vars") or []
            factor = v.get("factor_vars") or []
            organic = v.get("organic_vars") or []
            lines.append(
                f"{idx:<4}  {v.get('benchmark_variant', ''):<52}  "
                f"{str(v.get('adstock', '')):<14}  "
                f"{str(v.get('train_size', '')):<14}  "
                f"{str(v.get('resample_freq', 'none')):<10}  "
                f"{_preview(pmv):<36}  "
                f"{_preview(organic):<28}  "
                f"{_preview(ctx):<36}  "
                f"{_preview(factor)}"
            )

        log_text = "\n".join(lines) + "\n"
        log_path = f"{BENCHMARK_ROOT}/{benchmark_id}/combinations.log"
        log_blob = self.bucket.blob(log_path)
        log_blob.upload_from_string(log_text, content_type="text/plain")

        logger.info(
            f"Saved combinations log: gs://{self.bucket_name}/{log_path}"
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

        # Persist the queue_name into plan.json so the UI can identify
        # which queue to trigger when showing the "PENDING" diagnostic panel.
        plan_blob_path = f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        plan_blob = self.bucket.blob(plan_blob_path)
        try:
            plan_doc = json.loads(plan_blob.download_as_text())
            plan_doc["queue_name"] = queue_name
            plan_doc["status"] = "queued"
            plan_blob.upload_from_string(
                json.dumps(plan_doc, indent=2),
                content_type="application/json",
            )
        except Exception as e:
            logger.warning("Could not update plan.json with queue_name: %s", e)

        # Kick the web app so it starts processing immediately (Cloud Tasks).
        # Falls back gracefully when env vars are not set (e.g. local runs).
        self._trigger_queue_tick(queue_name)

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
            # Resolve the special "Latest" sentinel to the actual most-recent
            # version timestamp so the container downloads a real GCS path.
            # Without this, the training container gets a literal path like
            # "…/mapped-datasets/dk/Latest/raw.parquet" which doesn't exist
            # and causes an immediate container exit.
            if data_version.lower() == "latest":
                goal = variant.get("selected_goal", "")
                if goal:
                    try:
                        data_version = self._find_latest_version(country, goal)
                        logger.info(
                            f"Resolved 'Latest' data_version for "
                            f"{country}/{goal} → {data_version}"
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Could not resolve 'Latest' for "
                            f"{country}/{goal}: {exc}; job may fail"
                        )
                        data_gcs_path = None
                else:
                    logger.warning(
                        f"data_version is 'Latest' but selected_goal is "
                        f"missing for variant "
                        f"'{variant.get('benchmark_variant')}'; job may fail"
                    )
                    data_gcs_path = None

            if data_version and data_version.lower() != "latest":
                # Path format: gs://{bucket}/mapped-datasets/{country}/{version}/raw.parquet
                candidate = (
                    f"gs://{self.bucket_name}/mapped-datasets/"
                    f"{country.lower()}/{data_version}/raw.parquet"
                )
                blob_path = f"mapped-datasets/{country.lower()}/{data_version}/raw.parquet"
                if self.bucket.blob(blob_path).exists():
                    data_gcs_path = candidate
                else:
                    logger.warning(
                        f"Mapped dataset not found at {candidate}; "
                        f"attempting to fall back to the latest available version."
                    )
                    latest_ts = self._find_latest_mapped_version(country)
                    if latest_ts:
                        data_gcs_path = (
                            f"gs://{self.bucket_name}/mapped-datasets/"
                            f"{country.lower()}/{latest_ts}/raw.parquet"
                        )
                        logger.warning(
                            f"Falling back to latest mapped dataset: "
                            f"{data_gcs_path} "
                            f"(original data_version={data_version!r} was missing)"
                        )
                    else:
                        logger.error(
                            f"No mapped dataset found for country '{country}'. "
                            f"Please re-map and save your data, then resubmit."
                        )
                        data_gcs_path = None
        else:
            # Fallback: try to infer from meta_version or fail
            logger.warning(
                f"No data_version in variant {variant.get('benchmark_variant')}, "
                f"job may fail"
            )
            data_gcs_path = None

        # Get dep_var from either dep_var or selected_goal
        dep_var = variant.get("dep_var") or variant.get(
            "selected_goal", "UPLOAD_VALUE"
        )

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
            # Preserve original preset label (survives "Custom" overwrite)
            "preset_label": variant.get("preset_label", ""),
        }

        # Add optional fields if present
        if "custom_hyperparameters" in variant:
            params["custom_hyperparameters"] = variant["custom_hyperparameters"]
        if "column_agg_strategies" in variant:
            params["column_agg_strategies"] = variant["column_agg_strategies"]

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

    def _trigger_queue_tick(self, queue_name: str) -> bool:
        """
        Schedule an immediate queue tick via Cloud Tasks so the web app
        picks up and runs newly-submitted jobs without requiring a periodic
        Cloud Scheduler.

        The logic mirrors app_shared.schedule_queue_tick_via_cloud_tasks.
        It is intentionally duplicated here so that this CLI script has no
        dependency on app_shared (which imports ``streamlit`` and cannot be
        imported outside a Streamlit runtime).

        Uses the same env vars:
          CLOUD_TASKS_QUEUE   – full Cloud Tasks queue resource path
          WEB_SERVICE_URL     – base URL of the web service
          CLOUD_TASKS_SA_EMAIL – optional OIDC service-account email

        Returns True if a task was scheduled, False otherwise (e.g. when
        running locally without these env vars set).
        """
        cloud_tasks_queue = os.getenv("CLOUD_TASKS_QUEUE")
        web_service_url = os.getenv("WEB_SERVICE_URL")
        cloud_tasks_sa = os.getenv("CLOUD_TASKS_SA_EMAIL")

        if not cloud_tasks_queue or not web_service_url:
            logger.info(
                "CLOUD_TASKS_QUEUE / WEB_SERVICE_URL not set — "
                "queue tick not auto-triggered. "
                "Process the queue manually: "
                "python scripts/process_queue_simple.py "
                "--loop --queue-name %s",
                queue_name,
            )
            return False

        try:
            from google.cloud import tasks_v2  # type: ignore[import-untyped]

            client = tasks_v2.CloudTasksClient()
            target_url = f"{web_service_url}?queue_tick=1&name={queue_name}"
            http_request: dict = {
                "http_method": tasks_v2.HttpMethod.GET,
                "url": target_url,
            }
            if cloud_tasks_sa:
                http_request["oidc_token"] = {
                    "service_account_email": cloud_tasks_sa,
                    "audience": web_service_url,
                }
            task: dict = {"http_request": http_request}
            client.create_task(parent=cloud_tasks_queue, task=task)
            logger.info(
                "Scheduled immediate queue tick for '%s' via Cloud Tasks",
                queue_name,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to schedule Cloud Tasks queue tick: %s", exc)
            return False

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
                    configs.append(
                        {
                            "file": config_file.name,
                            "path": str(config_file),
                            "name": config_data.get("name", ""),
                            "description": config_data.get("description", ""),
                            "variant_count": self._count_config_variants(
                                config_data
                            ),
                            "combination_mode": config_data.get(
                                "combination_mode", "single"
                            ),
                        }
                    )
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
            raise FileNotFoundError(f"Benchmark plan not found: {benchmark_id}")

        variants = plan.get("variants", [])

        if not variants:
            logger.warning(f"No variants found in benchmark plan")
            if pd is not None:
                return pd.DataFrame()
            return []

        logger.info(f"Collecting results for {len(variants)} variants...")

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
                logger.error(f"Error collecting variant {i}: {e}")

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
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)

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
        decomp = summary.get("decomp_contribution", {}) or {}
        channel_roas = decomp.get("channel_roas") or {}
        channel_cpa = decomp.get("channel_cpa") or {}

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
            # Decomposition contribution shares
            "paid_media_share": decomp.get("paid_media_share"),
            "baseline_share": decomp.get("baseline_share"),
            "organic_share": decomp.get("organic_share"),
            "context_share": decomp.get("context_share"),
            # Allocator stability (ROAS CV across Pareto front – lower = more stable)
            "allocator_stability_roas_cv": decomp.get(
                "allocator_stability_roas_cv"
            ),
            # Per-channel ROAS serialised as JSON string for CSV portability
            "channel_roas_json": (
                json.dumps(channel_roas) if channel_roas else ""
            ),
            # Per-channel CPA serialised as JSON string for CSV portability
            "channel_cpa_json": (
                json.dumps(channel_cpa) if channel_cpa else ""
            ),
            # Model metadata — unique per variant × run: use benchmark_variant
            # + timestamp so the ID is human-readable and never clashes across
            # variants (Robyn's own IDs like 1_2_5 repeat between runs).
            "model_id": (
                f"{variant.get('benchmark_variant', '')}_{summary.get('timestamp', '')}"
                if summary.get("timestamp")
                else variant.get("benchmark_variant", "")
            ),
            "pareto_model_count": summary.get("pareto_model_count", 0),
            "candidate_model_count": summary.get("candidate_model_count", 0),
            # Execution metadata
            "training_time_mins": summary.get("training_time_mins"),
            "timestamp": summary.get("timestamp", ""),
            "created_at": summary.get("created_at", ""),
        }

        return result

    def export_results(self, benchmark_id: str, results, format: str = "csv"):
        """Export results to GCS."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if format == "csv":
            output_path = (
                f"{BENCHMARK_ROOT}/{benchmark_id}/" f"results_{timestamp}.csv"
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
            print(
                f"\n💡 Use --show-results-location {benchmark_id} to see expected paths"
            )
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
            print(
                f"Expected location: gs://{self.bucket_name}/{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
            )
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
            print(
                f"   Path: gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_HHMMSS/"
            )
            print(f"   Contains:")
            print(f"     - model_summary.json  (metrics and metadata)")
            print(
                f"     - {{model_id}}.png     (visualization plot, e.g., 1_112_3.png)"
            )
            print(f"     - console.log         (execution logs)")

        print("\n" + "=" * 80)
        print("Manual Access Commands:")
        print("-" * 80)

        # Provide gsutil commands
        for variant in variants[:1]:  # Show example for first variant
            country = variant.get("country", "")
            revision = variant.get("revision", "default")

            print(f"\n# List all results for {country}:")
            print(
                f"gsutil ls gs://{self.bucket_name}/robyn/{revision}/{country}/"
            )

            print(f"\n# View a specific model summary:")
            print(
                f"gsutil cat gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_HHMMSS/model_summary.json | jq ."
            )

            print(f"\n# Download all results:")
            print(
                f"gsutil -m cp -r gs://{self.bucket_name}/robyn/{revision}/{country}/YYYYMMDD_*/ ./results/"
            )

        print("\n" + "=" * 80)
        print(f"\n💡 To list available results:")
        print(
            f"  python scripts/benchmark_mmm.py --list-results {benchmark_id}"
        )


def select_top_combinations(
    all_variants: List[Dict[str, Any]], n: int = 10
) -> List[Dict[str, Any]]:
    """
    Select top N combinations based on Robyn best practices.

    Priority:
    1. Adstock diversity (geometric, weibull_cdf, weibull_pdf)
    2. Train split variety (70/90, 75/90, 65/80)
    3. Both time aggregations (daily, weekly)
    4. All spend mapping strategies

    Distributes slots evenly across adstock types, then fills remaining
    slots with the next available variants (in order) to always reach n.
    """
    if n >= len(all_variants):
        return all_variants

    adstock_types = ["geometric", "weibull_cdf", "weibull_pdf"]
    selected = []

    # Distribute slots evenly across adstock types (round-robin to n)
    per_adstock = n // len(adstock_types)
    for adstock in adstock_types:
        adstock_variants = [
            v for v in all_variants if v.get("adstock") == adstock
        ]
        selected.extend(adstock_variants[:per_adstock])

    # Fill remaining slots with diverse configurations not yet selected
    remaining = [v for v in all_variants if v not in selected]
    selected.extend(remaining[: n - len(selected)])

    return selected[:n]


def main():
    parser = argparse.ArgumentParser(description="Run MMM benchmarking tests")
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
    parser.add_argument(
        "--test-run-all",
        action="store_true",
        help="Run quick test with minimal iterations (10) and trials (1) for ALL variants (validates queue processing)",
    )
    parser.add_argument(
        "--all-benchmarks",
        action="store_true",
        help="Run ALL benchmark configurations in one command (discovers all .json files in benchmarks/)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=54,
        help="Number of combinations to submit (default: 54). "
        "Selects best combinations based on Robyn best practices. "
        "Use 18 for geometric-only, 54 for all adstock types, "
        "or higher values when combining adstock × window sweeps.",
    )
    parser.add_argument(
        "--hyperparameter-preset",
        dest="hyperparameter_preset",
        choices=["conservative", "balanced", "exploratory", "fb", "meshed"],
        default=None,
        help=(
            "Override the hyperparameter preset defined in the benchmark config JSON "
            "(conservative / balanced / exploratory / fb / meshed). "
            "'fb' uses Robyn/Facebook official documentation defaults, channel-type-differentiated "
            "(Digital 0.0–0.3, OOH/Print/Radio 0.1–0.4, TV 0.3–0.8 at weekly frequency). "
            "'meshed' uses Meshed recommended ranges (channel-type-differentiated). "
            "Shorthand: use --fb or --meshed instead."
        ),
    )
    preset_shorthand_group = parser.add_mutually_exclusive_group()
    preset_shorthand_group.add_argument(
        "--fb",
        action="store_true",
        default=False,
        help=(
            "Shorthand for --hyperparameter-preset fb. "
            "Uses Robyn/Facebook official documentation defaults, channel-type-differentiated: "
            "Digital theta 0.0–0.3, OOH/Print/Radio 0.1–0.4, TV 0.3–0.8 (weekly; scaled for other frequencies)."
        ),
    )
    preset_shorthand_group.add_argument(
        "--meshed",
        action="store_true",
        default=False,
        help=(
            "Shorthand for --hyperparameter-preset meshed. "
            "Uses Meshed recommended ranges (channel-type-differentiated, tighter saturation)."
        ),
    )

    parser.add_argument(
        "--benchmark-id",
        dest="benchmark_id",
        default=None,
        help=(
            "Use this value as the benchmark ID instead of generating one. "
            "Pass the same ID for all configs in a multi-config run so that "
            "results land under a single benchmarks/{id}/ folder and appear "
            "as one entry on the Benchmark Results page."
        ),
    )
    parser.add_argument(
        "--variant-prefix",
        dest="variant_prefix",
        default=None,
        help=(
            "Prefix to prepend to every benchmark_variant name "
            "(e.g. 'dk_context_minimal'). "
            "Required when multiple configs share the same benchmark ID to "
            "avoid variant-name collisions in GCS."
        ),
    )

    args = parser.parse_args()

    # Resolve shorthand preset flags
    if args.fb:
        args.hyperparameter_preset = "fb"
    elif args.meshed:
        args.hyperparameter_preset = "meshed"

    runner = BenchmarkRunner()

    if args.list_configs:
        configs = runner.list_config_files()
        if not configs:
            print(
                "No benchmark configuration files found in benchmarks/ directory"
            )
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
        print(
            f"  python scripts/benchmark_mmm.py --config benchmarks/<filename>"
        )
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
            print(
                "   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json"
            )
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
            print(
                "   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json"
            )
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

    # Handle --all-benchmarks mode
    if args.all_benchmarks:
        if args.config:
            logger.error("Cannot use both --all-benchmarks and --config")
            print(
                "\n❌ Error: Use either --all-benchmarks OR --config, not both"
            )
            print(
                "  --all-benchmarks: Runs all benchmark configs automatically"
            )
            print("  --config: Runs a specific benchmark config")
            sys.exit(1)

        logger.info("🚀 ALL BENCHMARKS MODE - Running all test configurations")
        print("\n🚀 ALL BENCHMARKS MODE")
        print("=" * 80)

        # Discover all benchmark configs
        configs = runner.list_config_files()
        if not configs:
            print(
                "\n❌ No benchmark configuration files found in benchmarks/ directory"
            )
            sys.exit(1)

        # Filter out comprehensive_benchmark as it's typically for cartesian testing
        configs = [
            c for c in configs if "comprehensive" not in c["file"].lower()
        ]

        print(f"\nDiscovered {len(configs)} benchmark configuration(s):")
        print("-" * 80)

        total_variants = 0
        for cfg in configs:
            print(f"  ✓ {cfg['name']}: {cfg['variant_count']} variants")
            print(
                f"    ({cfg['description'][:70]}...)"
                if len(cfg["description"]) > 70
                else f"    ({cfg['description']})"
            )
            total_variants += cfg["variant_count"]

        print("-" * 80)
        print(f"Total estimated variants: {total_variants}")

        if args.test_run:
            print(
                "\n⚠️  Note: --test-run with --all-benchmarks will run first variant of each benchmark"
            )
            print(
                "    (Not recommended - use --test-run-all instead for better testing)"
            )
        elif args.test_run_all:
            print("\n🧪 TEST RUN ALL MODE")
            print(f"  Iterations: 10 (reduced from default)")
            print(f"  Trials: 1 (reduced from default)")
            print(
                f"  Expected time: ~{total_variants * 5}-{total_variants * 10} minutes"
            )
        else:
            print("\n⏱️  Full benchmark execution")
            print(
                f"  Expected time: ~{total_variants * 20}-{total_variants * 30} minutes"
            )

        if args.dry_run:
            print("\n🔍 DRY RUN - No jobs will be submitted")

        print("\n" + "=" * 80)
        print("Processing benchmarks...\n")

        # Process each benchmark config
        benchmark_results = []
        total_submitted = 0

        for cfg in configs:
            try:
                print(f"\n📊 Processing: {cfg['name']}")
                print("-" * 60)

                # Load config
                config_path = Path(cfg["path"])
                with open(config_path) as f:
                    config_dict = json.load(f)

                # Apply CLI preset override before constructing BenchmarkConfig
                if args.hyperparameter_preset:
                    config_dict["hyperparameter_preset"] = (
                        args.hyperparameter_preset
                    )

                benchmark_config = BenchmarkConfig(config_dict)

                # Load base configuration
                base_cfg = benchmark_config.base_config
                base_config = runner.load_base_config(
                    country=base_cfg["country"],
                    goal=base_cfg["goal"],
                    version=base_cfg["version"],
                )

                # Override iterations/trials
                base_config["iterations"] = benchmark_config.iterations
                base_config["trials"] = benchmark_config.trials

                # Apply any field overrides embedded in the benchmark config's
                # base_config section.  This allows run_full_benchmark.py to
                # substitute context_vars, factor_vars, organic_vars, and
                # paid_media_vars from a local config file without re-uploading
                # selected_columns.json to GCS.
                overrides = base_cfg.get("overrides", {})
                if overrides:
                    base_config.update(overrides)
                    logger.info(
                        f"Applied {len(overrides)} base_config override(s): "
                        f"{list(overrides.keys())}"
                    )

                # Generate variants
                variants = runner.generate_variants(
                    base_config, benchmark_config
                )

                if not variants:
                    logger.warning(f"No variants generated for {cfg['name']}")
                    print(f"  ⚠️  No variants generated - skipping")
                    continue

                print(f"  Generated {len(variants)} variant(s)")

                # Apply test modes
                if args.test_run:
                    # Only first variant
                    variants = [variants[0].copy()]
                    variants[0]["iterations"] = 10
                    variants[0]["trials"] = 1
                    print(f"  🧪 TEST MODE: Running first variant only")
                elif args.test_run_all:
                    # All variants with reduced resources
                    test_variants = []
                    for variant in variants:
                        test_var = variant.copy()
                        test_var["iterations"] = 10
                        test_var["trials"] = 1
                        test_variants.append(test_var)
                    variants = test_variants
                    print(
                        f"  🧪 TEST MODE: All {len(variants)} variants with reduced resources"
                    )

                # Generate benchmark ID (or reuse supplied one)
                if args.benchmark_id:
                    benchmark_id = args.benchmark_id
                else:
                    benchmark_id = (
                        f"{benchmark_config.name}_"
                        f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                    )

                if args.test_run:
                    benchmark_id = f"{benchmark_id}_test"
                elif args.test_run_all:
                    benchmark_id = f"{benchmark_id}_testall"

                # Prefix variant names when sharing a benchmark_id
                if args.variant_prefix:
                    prefix = args.variant_prefix.rstrip("_")
                    for variant in variants:
                        existing = variant.get("benchmark_variant", "")
                        variant["benchmark_variant"] = (
                            f"{prefix}_{existing}" if existing else prefix
                        )

                # Save plan
                runner.save_benchmark_plan(
                    benchmark_id, benchmark_config, variants
                )

                if not args.dry_run and not args.no_submit:
                    # Submit to queue
                    submitted_count = runner.submit_variants_to_queue(
                        benchmark_id, variants, queue_name=args.queue_name
                    )
                    total_submitted += submitted_count
                    print(
                        f"  ✅ Submitted {submitted_count} job(s) to queue '{args.queue_name}'"
                    )
                else:
                    print(f"  💾 Plan saved (not submitted)")

                benchmark_results.append(
                    {
                        "name": benchmark_config.name,
                        "benchmark_id": benchmark_id,
                        "variants": len(variants),
                        "submitted": not (args.dry_run or args.no_submit),
                    }
                )

            except Exception as e:
                logger.error(
                    f"Failed to process {cfg['name']}: {e}", exc_info=True
                )
                print(f"  ❌ Error: {e}")
                continue

        # Summary
        print("\n" + "=" * 80)
        print("📋 SUMMARY")
        print("=" * 80)

        if benchmark_results:
            for result in benchmark_results:
                status = "✅ Submitted" if result["submitted"] else "💾 Saved"
                print(f"  {status}: {result['name']}")
                print(f"    Benchmark ID: {result['benchmark_id']}")
                print(f"    Variants: {result['variants']}")

        print("-" * 80)
        if not (args.dry_run or args.no_submit):
            print(f"✅ Total variants queued: {total_submitted}")
            print(f"Queue: {args.queue_name}")
            print(f"\n💡 Process the queue with:")
            print(f"  python scripts/process_queue_simple.py --loop --cleanup")
        else:
            print(f"💾 Plans saved but not submitted")
            print(f"   Remove --dry-run or --no-submit to submit jobs")

        return

    if not args.config:
        parser.error(
            "--config is required (or use --list-configs, --all-benchmarks)"
        )

    # Load benchmark configuration
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    with open(args.config) as f:
        config_dict = json.load(f)

    # Apply CLI preset override before constructing BenchmarkConfig
    if args.hyperparameter_preset:
        config_dict["hyperparameter_preset"] = args.hyperparameter_preset
        logger.info(
            f"�� Hyperparameter preset override: {args.hyperparameter_preset}"
        )

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
    logger.info(f"Loaded base config: {base_cfg['country']}/{base_cfg['goal']}")

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

    # ── Per-variant Robyn parameter summary ──────────────────────────────
    # Printed immediately after generation so the operator can verify
    # exactly what will be sent to Robyn before any jobs hit the queue.
    logger.info("")
    logger.info("=" * 72)
    logger.info(
        f"ROBYN PARAMETER SUMMARY — {len(variants)} variant(s) to submit"
    )
    logger.info("=" * 72)
    logger.info(
        f"{'#':<4} {'variant':<32} {'adstock':<12} {'freq':<8} "
        f"{'preset/label':<22} {'custom_hp':<10} {'splits'}"
    )
    logger.info("-" * 100)
    for idx, v in enumerate(variants, 1):
        variant_name = v.get("benchmark_variant", "—")
        adstock = v.get("adstock", "geometric")
        freq = v.get("resample_freq", "none")
        preset = v.get("hyperparameter_preset", "—")
        label = v.get("preset_label", "")
        preset_display = f"{preset}" + (
            f" ({label})" if label and label != preset else ""
        )
        hp_keys = len(v.get("custom_hyperparameters", {}))
        hp_str = f"{hp_keys} keys" if hp_keys else "none"
        train_size = v.get("train_size", "")
        splits = f"train_size={train_size}" if train_size else "—"
        logger.info(
            f"{idx:<4} {variant_name:<32} {adstock:<12} {freq:<8} "
            f"{preset_display:<22} {hp_str:<10} {splits}"
        )
    logger.info("-" * 100)
    # Detailed view: paid_media_vars + custom_hyperparameters per variant
    for idx, v in enumerate(variants, 1):
        variant_name = v.get("benchmark_variant", f"variant_{idx}")
        paid_vars = v.get("paid_media_vars", [])
        paid_spends = v.get("paid_media_spends", [])
        organic = v.get("organic_vars", [])
        context = v.get("context_vars", [])
        var_to_spend = v.get("var_to_spend_mapping", {})
        custom_hp = v.get("custom_hyperparameters", {})
        logger.info(
            f"  [{idx}] {variant_name}: "
            f"paid_media_vars={paid_vars} | paid_media_spends={paid_spends} | "
            f"organic_vars={organic} | context_vars={context}"
        )
        # Show var→spend mapping so it is easy to verify proxies are correct
        if var_to_spend:
            mapping_lines = []
            for pvar, pspend in var_to_spend.items():
                arrow = "→" if pvar != pspend else "="
                mapping_lines.append(f"{pvar} {arrow} {pspend}")
            logger.info(
                f"       var_to_spend_mapping: " + " | ".join(mapping_lines)
            )
        else:
            logger.info(
                "       var_to_spend_mapping: (none — paid_media_vars "
                "not remapped from spends)"
            )
        if custom_hp:
            # Group by variable (strip _alphas/_gammas/_thetas suffix)
            hp_by_var: dict = {}
            for key, val in custom_hp.items():
                parts = key.rsplit("_", 1)
                if len(parts) == 2:
                    var, hp_type = parts
                else:
                    var, hp_type = key, "?"
                hp_by_var.setdefault(var, {})[hp_type] = val
            for var_name, hp_vals in sorted(hp_by_var.items()):
                hp_str_detail = ", ".join(
                    f"{t}={vv}" for t, vv in sorted(hp_vals.items())
                )
                logger.info(f"       {var_name}: {hp_str_detail}")
        else:
            logger.info(
                "       ⚠️  no custom_hyperparameters — Robyn will use its "
                "built-in defaults for this variant"
            )
    logger.info("=" * 72)
    logger.info("")
    # ─────────────────────────────────────────────────────────────────────

    # Apply top-N selection if requested
    if args.top_n < len(variants):
        logger.info(
            f"🔢 Selecting top {args.top_n} combinations "
            f"from {len(variants)} total"
        )
        variants = select_top_combinations(variants, n=args.top_n)
        logger.info(f"   Selected {len(variants)} combinations")

    # Generate benchmark ID (or use the supplied one for grouped runs)
    if args.benchmark_id:
        benchmark_id = args.benchmark_id
        logger.info(f"Using supplied benchmark ID: {benchmark_id}")
    else:
        benchmark_id = (
            f"{benchmark_config.name}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )

    # Prefix variant names when sharing a benchmark_id across multiple configs
    # so GCS paths remain unique: benchmarks/{id}/{prefix}_{variant}/
    if args.variant_prefix:
        prefix = args.variant_prefix.rstrip("_")
        for variant in variants:
            existing = variant.get("benchmark_variant", "")
            variant["benchmark_variant"] = (
                f"{prefix}_{existing}" if existing else prefix
            )
        logger.info(
            f"Applied variant prefix '{prefix}' to {len(variants)} variant(s)"
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

    if args.test_run and args.test_run_all:
        logger.error("Cannot use both --test-run and --test-run-all")
        print("\n❌ Error: Use either --test-run OR --test-run-all, not both")
        print("  --test-run: Tests first variant only")
        print("  --test-run-all: Tests all variants with reduced resources")
        sys.exit(1)

    if args.test_run:
        if not variants:
            logger.error("Cannot run test - no variants generated")
            print("\n❌ Error: Cannot run test with empty variants list")
            sys.exit(1)

        logger.info(
            "🧪 TEST RUN MODE - Running first variant with minimal settings"
        )
        print("\n🧪 TEST RUN MODE")
        print(
            f"Generated {len(variants)} total variants, but TEST MODE only runs the first one"
        )
        print(f"Iterations: 10 (reduced from {benchmark_config.iterations})")
        print(f"Trials: 1 (reduced from {benchmark_config.trials})")
        print(
            f"Testing variant: {variants[0].get('benchmark_variant', 'first')}"
        )
        print(
            f"\n💡 To run all {len(variants)} variants, use --config without --test-run"
        )
        print(
            f"💡 To test all variants with reduced resources, use --test-run-all"
        )

        # Modify first variant for test
        test_variants = [variants[0].copy()]
        test_variants[0]["iterations"] = 10
        test_variants[0]["trials"] = 1
        variants = test_variants

        # Update benchmark_id to indicate test
        benchmark_id = f"{benchmark_id}_test"

    if args.test_run_all:
        if not variants:
            logger.error("Cannot run test - no variants generated")
            print("\n❌ Error: Cannot run test with empty variants list")
            sys.exit(1)

        logger.info(
            "🧪 TEST RUN ALL MODE - Running ALL variants with minimal settings"
        )
        print("\n🧪 TEST RUN ALL MODE")
        print(
            f"Generated {len(variants)} variants - ALL will run with reduced resources"
        )
        print(f"Iterations: 10 (reduced from {benchmark_config.iterations})")
        print(f"Trials: 1 (reduced from {benchmark_config.trials})")
        print(f"\nVariants to test:")
        for i, var in enumerate(variants, 1):
            print(f"  {i}. {var.get('benchmark_variant', f'variant_{i}')}")
        print(f"\n💡 This tests queue processing with multiple jobs")
        print(
            f"💡 Expected time: ~{len(variants) * 5}-{len(variants) * 10} minutes"
        )
        print(f"💡 To test just one variant, use --test-run instead")

        # Modify ALL variants for test
        test_variants = []
        for variant in variants:
            test_var = variant.copy()
            test_var["iterations"] = 10
            test_var["trials"] = 1
            test_variants.append(test_var)
        variants = test_variants

        # Update benchmark_id to indicate test
        benchmark_id = f"{benchmark_id}_testall"

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

                trigger_script = Path(__file__).parent / "trigger_queue.py"
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
                    print("\nYou can manually trigger queue processing with:")
                    print(
                        f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue"
                    )

            except Exception as e:
                logger.error(f"Failed to trigger queue: {e}")
                print(f"\n⚠️  Could not automatically trigger queue: {e}")
                print("You can manually trigger queue processing with:")
                print(
                    f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue"
                )
        else:
            print(
                f"\n💡 Monitor progress in the Streamlit app "
                f"(Run Experiment → Queue Monitor)"
            )
            print(f"\nOr manually trigger queue processing with:")
            print(
                f"  python scripts/trigger_queue.py --queue-name {args.queue_name} --resume-queue --until-empty"
            )

    except Exception as e:
        logger.error(f"Failed to submit jobs: {e}")
        print(f"\n❌ Error submitting jobs: {e}")
        print(f"Benchmark plan saved but jobs not queued: " f"{benchmark_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()
