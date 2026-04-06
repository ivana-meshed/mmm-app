#!/usr/bin/env python3
"""
Complete End-to-End Benchmarking Workflow

Single command to:
1. Parse selected_columns.json from GCS path
2. Generate comprehensive benchmark configuration
3. Submit all test combinations
4. Process queue until complete
5. Analyze and visualize results

Usage:
    # Test run (default - reduced iterations/trials)
    python scripts/run_full_benchmark.py --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

    # Full production run
    python scripts/run_full_benchmark.py --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json --full-run

    # With per-channel hyperparameter ranges (balanced preset is the default)
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json

    # With Meshed recommended preset (shorthand --meshed flag)
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
        --meshed

    # With Facebook/Robyn official preset (shorthand --fb flag)
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
        --fb

    # Compare balanced, fb, and meshed presets in one run (3× variants)
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
        --compare-presets

    # Compare all five presets in one run (5× variants)
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
        --compare-all-presets

    # With custom queue name
    python scripts/run_full_benchmark.py --path <path> --queue-name default-dev
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Constants
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_QUEUE = os.getenv("QUEUE_NAME", "default-dev")

# Preset comparison groups
# --compare-presets: compare the three most common presets
PRESETS_COMPARE = [
    {"name": "balanced", "description": "General-purpose default preset"},
    {
        "name": "fb",
        "description": "Robyn/Facebook official documentation defaults",
    },
    {"name": "meshed", "description": "Meshed recommended ranges"},
]
# --compare-all-presets: compare all five built-in presets
PRESETS_ALL = [
    {
        "name": "conservative",
        "description": "Narrow search space for fast screening",
    },
    {"name": "balanced", "description": "General-purpose default preset"},
    {
        "name": "exploratory",
        "description": "Wide search space for uncertain channels",
    },
    {
        "name": "fb",
        "description": "Robyn/Facebook official documentation defaults",
    },
    {"name": "meshed", "description": "Meshed recommended ranges"},
]


def parse_gcs_path(path: str) -> tuple:
    """
    Parse GCS path to extract bucket and object path.

    Example: gs://bucket/path/to/file.json -> ('bucket', 'path/to/file.json')
    """
    if path.startswith("gs://"):
        path = path[5:]  # Remove 'gs://'

    parts = path.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    else:
        return parts[0], ""


def extract_version_from_path(gcs_path: str) -> str:
    """
    Extract version (timestamp) from GCS path.

    Example: gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json
    Returns: 20260122_113141
    """
    # Remove gs:// prefix if present
    if gcs_path.startswith("gs://"):
        gcs_path = gcs_path[5:]

    # Split path and get the timestamp part (before selected_columns.json)
    # Format: bucket/training_data/country/goal/timestamp/selected_columns.json
    parts = gcs_path.split("/")

    # Find selected_columns.json and get the part before it
    for i, part in enumerate(parts):
        if part == "selected_columns.json" and i > 0:
            return parts[i - 1]

    # Fallback: try to find a timestamp-like pattern (YYYYMMDD_HHMMSS)
    import re

    for part in reversed(parts):
        if re.match(r"\d{8}_\d{6}", part):
            return part

    return "Latest"


def download_selected_columns(gcs_path: str) -> Dict[str, Any]:
    """Download and parse selected_columns.json from GCS."""
    logger.info(f"📥 Downloading config from: {gcs_path}")

    bucket_name, object_path = parse_gcs_path(gcs_path)

    # Download from GCS
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_path)

    content = blob.download_as_text()
    config = json.loads(content)

    logger.info(
        f"✅ Downloaded config for country: {config.get('country')}, goal: {config.get('selected_goal')}"
    )

    return config


ALL_ADSTOCK_VARIANTS = [
    {
        "name": "geometric",
        "description": "Geometric adstock",
        "adstock": "geometric",
        "hyperparameter_preset": "Meshed recommend",
    },
    {
        "name": "weibull_cdf",
        "description": "Weibull CDF adstock",
        "adstock": "weibull_cdf",
        "hyperparameter_preset": "Meta default",
    },
    {
        "name": "weibull_pdf",
        "description": "Weibull PDF adstock",
        "adstock": "weibull_pdf",
        "hyperparameter_preset": "Meshed recommend",
    },
]

# Window-length variants expressed as weeks_back from end_date.
# None means "use all available history" (no start_date override).
ALL_WINDOW_VARIANTS: List[Dict[str, Any]] = [
    {
        "name": "full",
        "description": "Full data window (all available history)",
        "weeks_back": None,
    },
    {
        "name": "2y",
        "description": "Last 2 years (~104 weeks)",
        "weeks_back": 104,
    },
    {
        "name": "3y",
        "description": "Last 3 years (~156 weeks)",
        "weeks_back": 156,
    },
]

# Fixed dimension sizes for the four benchmark dimensions.
# These must stay in sync with the variants_dict built in
# generate_benchmark_config() (the train_splits / time_aggregation /
# spend_var_mapping lists defined inside that function).
_NUM_TRAIN_SPLITS = 3  # 70_90, 75_90, 65_80
_NUM_TIME_AGGREGATIONS = 2  # daily, weekly
_NUM_SPEND_VAR_MAPPINGS = 3  # spend_to_spend, spend_to_proxy, mixed_by_funnel

# Maximum possible cartesian combinations across all adstock types and windows:
# 3 adstock × 3 splits × 2 time_agg × 3 spend_var × 3 windows = 162
_MAX_CARTESIAN_COMBINATIONS = (
    len(ALL_ADSTOCK_VARIANTS)
    * _NUM_TRAIN_SPLITS
    * _NUM_TIME_AGGREGATIONS
    * _NUM_SPEND_VAR_MAPPINGS
    * len(ALL_WINDOW_VARIANTS)
)


def generate_benchmark_config(
    selected_columns: Dict[str, Any],
    version_from_path: str,
    run_mode: str = "test",
    adstock_types: list = None,
    window_lengths: list = None,
    sequential: bool = False,
    hyperparameter_ranges_config: Optional[str] = None,
    channel_type_assignments_config: Optional[str] = None,
    hyperparameter_preset: Optional[str] = None,
    compare_presets: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Generate comprehensive benchmark configuration from selected_columns.json.

    By default (cartesian mode) creates a cartesian product of:
    - N adstock types (default: 1 – geometric only; use --all-adstock for all 3)
    - 3 train/test splits
    - 2 time aggregations
    - 3 spend→var mapping strategies
    - M window lengths (default: 1 – "full"; use --all-windows / --windows to add more)
    = 18 combinations by default (geometric, full window),
      54 when all adstock tested,
      54 when geometric + all 3 windows,
      162 when all adstock + all 3 windows

    In sequential mode (--sequential) each dimension is varied independently:
    - Dimensions are tested one at a time using the base config for other dimensions
    - Order: adstock → train_splits → time_aggregation → spend_var_mapping → window_lengths
    - Total = sum of dimension sizes (e.g. 1+3+2+3 = 9 for geometric-only default)
    - Recommended for initial exploration before committing to a full cartesian sweep

    run_mode options:
    - "test": 10 iterations, 1 trial (no window sweep by default)
    - "standard": 1000 iterations, 3 trials (full window by default)
    - "extended": 2000 iterations, 5 trials (full window by default)
    - "production": 5000 iterations, 5 trials (full window by default)

    adstock_types: list of adstock names to include (None → geometric only)
    window_lengths: list of window names from ALL_WINDOW_VARIANTS to include
                    (None → "full" for non-test modes; no window dim for test mode)
    sequential: if True, use sequential (single-dimension) mode instead of
                    cartesian product; reduces combinations from product to sum
    hyperparameter_ranges_config: optional path to a hyperparameter ranges JSON
                    file (relative to repo root), e.g.
                    "benchmarks/generic_hyperparameter_ranges_v2.json"
    channel_type_assignments_config: optional path to a channel-type assignments
                    JSON file (relative to repo root), e.g.
                    "benchmarks/channel_type_assignments.json"
    hyperparameter_preset: one of "conservative", "balanced" (default),
                    "exploratory", "fb" (Facebook/Robyn official), or "meshed"
                    (Meshed recommended). Ignored when compare_presets is set.
    compare_presets: list of preset spec dicts to compare as a dimension,
                    e.g. ``PRESETS_COMPARE`` (balanced/fb/meshed) or
                    ``PRESETS_ALL`` (all five). When set, the preset becomes
                    a full variant dimension (multiplied into cartesian product
                    or added as a sequential sweep). Requires
                    hyperparameter_ranges_config to be set for per-channel
                    range resolution. Mutually exclusive with hyperparameter_preset.
    """
    country = selected_columns.get("country", "de")
    goal = selected_columns.get("selected_goal", "N_UPLOADS_WEB")
    timestamp = selected_columns.get(
        "timestamp", datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    # Base configuration - use version from GCS path, not data_version from JSON
    base_config = {
        "country": country,
        "goal": goal,
        "version": version_from_path,  # Use the timestamp from GCS path
    }

    # Iterations and trials based on run mode
    mode_config = {
        "test": {"iterations": 10, "trials": 1},
        "standard": {"iterations": 1000, "trials": 3},
        "extended": {"iterations": 2000, "trials": 5},
        "production": {"iterations": 5000, "trials": 5},
    }
    config = mode_config[run_mode]
    iterations = config["iterations"]
    trials = config["trials"]

    mode_labels = {
        "test": "🧪 TEST RUN MODE - Using reduced iterations (10) and trials (1)",
        "standard": "🚀 STANDARD RUN MODE - Using full iterations (1000) and trials (3)",
        "extended": "🚀 EXTENDED RUN MODE - Using extended iterations (2000) and trials (5)",
        "production": "🚀 PRODUCTION RUN MODE - Using production iterations (5000) and trials (5)",
    }
    logger.info(mode_labels[run_mode])

    # Resolve adstock variants to include
    adstock_name_map = {v["name"]: v for v in ALL_ADSTOCK_VARIANTS}
    if adstock_types:
        selected_adstock = [
            adstock_name_map[name]
            for name in adstock_types
            if name in adstock_name_map
        ]
    else:
        selected_adstock = [adstock_name_map["geometric"]]

    n_adstock = len(selected_adstock)

    adstock_label = (
        ", ".join(v["name"] for v in selected_adstock)
        if n_adstock > 1
        else selected_adstock[0]["name"]
    )
    logger.info(
        f"🎯 Adstock: {adstock_label} "
        f"({'all types' if n_adstock == 3 else 'geometric only' if n_adstock == 1 else f'{n_adstock} types'})"
    )

    # For non-test runs, default to the "full" window so results always carry
    # an explicit seasonality_window label (no data-window change; weeks_back=None
    # leaves base-config dates unchanged).
    effective_window_lengths = window_lengths
    if effective_window_lengths is None and run_mode != "test":
        effective_window_lengths = ["full"]

    # Resolve window-length variants to include
    window_name_map = {v["name"]: v for v in ALL_WINDOW_VARIANTS}
    selected_windows_raw: Optional[List[Dict]] = None
    if effective_window_lengths:
        selected_windows_raw = [
            window_name_map[name]
            for name in effective_window_lengths
            if name in window_name_map
        ]

    # Convert relative offsets to absolute start_date strings using end_date
    selected_window_specs: Optional[List[Dict]] = None
    if selected_windows_raw:
        end_date_str = selected_columns.get("end_date", "")
        if end_date_str:
            try:
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
            except ValueError:
                end_dt = datetime.now()
                logger.warning(
                    f"Could not parse end_date '{end_date_str}', using today"
                )
        else:
            end_dt = datetime.now()
            logger.warning(
                "No end_date in selected_columns; using today for window offsets"
            )

        selected_window_specs = []
        for w in selected_windows_raw:
            spec: Dict[str, Any] = {
                "name": w["name"],
                "description": w["description"],
            }
            weeks = w["weeks_back"]
            if weeks is not None:
                start_dt = end_dt - timedelta(weeks=weeks)
                spec["start_date"] = start_dt.strftime("%Y-%m-%d")
                spec["end_date"] = end_dt.strftime("%Y-%m-%d")
            # else: no override → base config dates used (full history)
            selected_window_specs.append(spec)

        n_windows = len(selected_window_specs)
        window_label = ", ".join(w["name"] for w in selected_windows_raw)
        logger.info(f"📅 Window lengths: {window_label} ({n_windows} variants)")
    else:
        n_windows = 1  # counts as one implicit "full" window in the product

    # Determine combination mode
    combination_mode = "single" if sequential else "cartesian"
    mode_label = "sequential" if sequential else "cartesian"

    # Calculate expected number of variants
    n_presets = len(compare_presets) if compare_presets else 1
    dim_sizes = [
        n_adstock,
        _NUM_TRAIN_SPLITS,
        _NUM_TIME_AGGREGATIONS,
        _NUM_SPEND_VAR_MAPPINGS,
    ]  # adstock, splits, time_agg, spend_var
    if selected_window_specs and n_windows > 1:
        dim_sizes.append(n_windows)
    if compare_presets and n_presets > 1:
        dim_sizes.append(n_presets)
    if sequential:
        # Sequential: each dimension varied independently (sum)
        n_combos = sum(dim_sizes)
    else:
        # Cartesian: product of all dimensions
        n_combos = 1
        for s in dim_sizes:
            n_combos *= s
    max_combinations = max(60, n_combos + 10)

    # Build variants dict
    variants_dict: Dict[str, Any] = {
        "adstock": selected_adstock,
        "train_splits": [
            {
                "name": "70_90",
                "description": "70% train, 20% val, 10% test",
                "train_size": [0.7, 0.9],
            },
            {
                "name": "75_90",
                "description": "75% train, 15% val, 10% test",
                "train_size": [0.75, 0.9],
            },
            {
                "name": "65_80",
                "description": "65% train, 15% val, 20% test",
                "train_size": [0.65, 0.8],
            },
        ],
        "time_aggregation": [
            {
                "name": "daily",
                "description": "Daily time aggregation",
                "resample_freq": "none",
            },
            {
                "name": "weekly",
                "description": "Weekly time aggregation",
                "resample_freq": "W",
            },
        ],
        "spend_var_mapping": [
            {
                "name": "spend_to_spend",
                "description": "All channels: spend → spend",
                "mapping_strategy": "spend_to_spend",
            },
            {
                "name": "spend_to_proxy",
                "description": "All channels: spend → sessions",
                "mapping_strategy": "spend_to_proxy",
            },
            {
                "name": "mixed_by_funnel",
                "description": "Upper funnel → sessions, lower → spend",
                "mapping_strategy": "mixed",
            },
        ],
    }

    if selected_window_specs:
        variants_dict["seasonality_window"] = selected_window_specs

    if compare_presets:
        variants_dict["hyperparameter_preset"] = compare_presets

    # Build comprehensive benchmark config
    preset_dim = (
        f" × {n_presets} presets" if compare_presets and n_presets > 1 else ""
    )
    window_dim = f" × {n_windows} windows" if selected_window_specs else ""
    benchmark_config = {
        "name": f"comprehensive_benchmark_{timestamp}",
        "description": (
            f"Complete {mode_label} benchmark: "
            f"adstock × train_splits × time_agg × spend_var_mapping"
            f"{window_dim}{preset_dim}"
        ),
        "base_config": base_config,
        "iterations": iterations,
        "trials": trials,
        "max_combinations": max_combinations,
        "combination_mode": combination_mode,
        "variants": variants_dict,
    }

    if hyperparameter_ranges_config:
        benchmark_config["hyperparameter_ranges_config"] = (
            hyperparameter_ranges_config
        )
        logger.info(
            f"🎛️  Hyperparameter ranges config: {hyperparameter_ranges_config}"
        )
    if channel_type_assignments_config:
        benchmark_config["channel_type_assignments_config"] = (
            channel_type_assignments_config
        )
        logger.info(
            f"📋 Channel type assignments config: {channel_type_assignments_config}"
        )
    if compare_presets:
        preset_names = [p["name"] for p in compare_presets]
        logger.info(
            f"🔀 Preset comparison: {', '.join(preset_names)} "
            f"({n_presets} presets × {n_combos // n_presets} base variants "
            f"= {n_combos} total)"
        )
    elif hyperparameter_preset:
        benchmark_config["hyperparameter_preset"] = hyperparameter_preset
        logger.info(f"🔧 Hyperparameter preset: {hyperparameter_preset}")

    window_factor = f" × {n_windows}" if selected_window_specs else ""
    preset_factor = (
        f" × {n_presets}" if compare_presets and n_presets > 1 else ""
    )
    _ns = _NUM_TRAIN_SPLITS
    _nt = _NUM_TIME_AGGREGATIONS
    _nv = _NUM_SPEND_VAR_MAPPINGS
    combo_formula = (
        f"{n_adstock} + {_ns} + {_nt} + {_nv}"
        f"{(' + ' + str(n_windows)) if selected_window_specs and n_windows > 1 else ''}"
        f"{(' + ' + str(n_presets)) if compare_presets and n_presets > 1 else ''}"
        if sequential
        else f"{n_adstock} × {_ns} × {_nt} × {_nv}{window_factor}{preset_factor}"
    )
    logger.info(f"📊 Generated benchmark config:")
    logger.info(f"   Mode: {mode_label}")
    logger.info(f"   Country: {country}")
    logger.info(f"   Goal: {goal}")
    logger.info(f"   Iterations: {iterations}")
    logger.info(f"   Trials: {trials}")
    logger.info(f"   Expected variants: {n_combos} ({combo_formula})")

    return benchmark_config


def load_external_benchmark_config(
    config_path: str,
    run_mode: str = "test",
    adstock_types: Optional[List[str]] = None,
    window_lengths: Optional[List[str]] = None,
    sequential: bool = False,
    hyperparameter_ranges_config: Optional[str] = None,
    channel_type_assignments_config: Optional[str] = None,
    hyperparameter_preset: Optional[str] = None,
    compare_presets: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Load an external benchmark config JSON and apply CLI run-mode overrides.

    Iteration and trial counts are always overridden by the ``run_mode`` flag
    so the same config file can be used for test → production sweeps without
    manual edits.  All variant dimensions (adstock, train_splits,
    time_aggregation, spend_var_mapping, seasonality_window) are taken from the
    config file as-is, with the following exceptions:

    * ``--all-adstock`` / ``--adstock`` replaces the config's adstock list.
    * ``--all-windows`` / ``--windows`` filters the config's
      ``seasonality_window`` list.  By default (no flag) only the ``"full"``
      window variant is kept; ``--all-windows`` restores all variants defined
      in the file.
    * ``--sequential`` sets ``combination_mode`` to ``"single"``.
    * ``--hyperparameter-ranges-config``, ``--channel-type-assignments-config``,
      and ``--hyperparameter-preset`` are merged into the config when supplied.

    Args:
        config_path: Path to the benchmark config JSON (relative to repo root
            or absolute).
        run_mode: ``"test"``, ``"standard"``, ``"extended"``, or
            ``"production"``.  Determines iterations and trials.
        adstock_types: If provided, replaces the config's adstock variant list
            with the named types from ``ALL_ADSTOCK_VARIANTS``.
        window_lengths: Window variant names to keep from the config's
            ``seasonality_window`` list (e.g. ``["full", "2y", "3y"]``).
            When ``None`` (default) only the ``"full"`` variant is kept.
        sequential: If ``True``, sets ``combination_mode`` to ``"single"``
            (vary one dimension at a time instead of cartesian product).
        hyperparameter_ranges_config: Optional path to a hyperparameter ranges
            JSON.  Added to the config when supplied.
        channel_type_assignments_config: Optional path to a channel-type
            assignments JSON.  Added to the config when supplied.
        hyperparameter_preset: Optional preset override
            (``conservative`` / ``balanced`` / ``exploratory`` / ``fb`` / ``meshed``).

    Returns:
        Modified benchmark config dict ready to be saved and passed to
        ``benchmark_mmm.py``.
    """
    resolved = Path(config_path)
    if not resolved.is_absolute():
        resolved = Path(__file__).parent.parent / config_path
    if not resolved.exists():
        raise FileNotFoundError(f"Benchmark config file not found: {resolved}")

    with open(resolved) as f:
        config = json.load(f)

    # Always override iterations/trials from the run mode flag so the same
    # config file can be used across test → production without manual edits.
    iter_map: Dict[str, tuple] = {
        "test": (10, 1),
        "standard": (1000, 3),
        "extended": (2000, 5),
        "production": (5000, 5),
    }
    iterations, trials = iter_map[run_mode]
    config["iterations"] = iterations
    config["trials"] = trials
    logger.info(
        f"📋 Loaded external config: {resolved.name} "
        f"(iterations={iterations}, trials={trials} from {run_mode} mode)"
    )

    # Override adstock variants when --all-adstock or --adstock is given.
    if adstock_types is not None:
        adstock_name_map = {v["name"]: v for v in ALL_ADSTOCK_VARIANTS}
        replaced = [
            adstock_name_map[t] for t in adstock_types if t in adstock_name_map
        ]
        if replaced:
            config.setdefault("variants", {})["adstock"] = replaced
            logger.info(
                f"🎯 Adstock overridden to: " f"{[v['name'] for v in replaced]}"
            )

    # Filter seasonality_window: default = full only; --all-windows keeps all.
    effective_windows = window_lengths if window_lengths else ["full"]
    variants_section = config.setdefault("variants", {})
    if "seasonality_window" in variants_section:
        filtered = [
            w
            for w in variants_section["seasonality_window"]
            if w.get("name") in effective_windows
        ]
        if filtered:
            variants_section["seasonality_window"] = filtered
            logger.info(
                f"📅 Seasonality window(s): "
                f"{[w['name'] for w in filtered]}"
                + (
                    " (use --all-windows to add 2y / 3y)"
                    if len(filtered) == 1
                    else ""
                )
            )

    # Sequential mode: vary one dimension at a time.
    if sequential:
        config["combination_mode"] = "single"
        logger.info("🔀 Combination mode set to sequential (single dimension)")

    # Merge hyperparameter config fields when provided.
    if hyperparameter_ranges_config:
        config["hyperparameter_ranges_config"] = hyperparameter_ranges_config
        logger.info(
            f"🎛️  Hyperparameter ranges config: {hyperparameter_ranges_config}"
        )
    if channel_type_assignments_config:
        config["channel_type_assignments_config"] = (
            channel_type_assignments_config
        )
        logger.info(
            f"📋 Channel type assignments config: "
            f"{channel_type_assignments_config}"
        )
    if hyperparameter_preset:
        config["hyperparameter_preset"] = hyperparameter_preset
        logger.info(f"🔧 Hyperparameter preset: {hyperparameter_preset}")

    if compare_presets:
        config.setdefault("variants", {})[
            "hyperparameter_preset"
        ] = compare_presets
        preset_names = [p["name"] for p in compare_presets]
        logger.info(
            f"🔀 Preset comparison dimension added: {', '.join(preset_names)}"
        )

    return config


def save_temp_benchmark_config(config: Dict[str, Any]) -> str:
    """Save benchmark config to temporary file."""
    import tempfile

    temp_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    json.dump(config, temp_file, indent=2)
    temp_file.close()

    logger.info(f"💾 Saved temporary benchmark config: {temp_file.name}")
    return temp_file.name


def run_benchmark_submission(
    config_path: str,
    queue_name: str = DEFAULT_QUEUE,
    top_n: Optional[int] = 54,
) -> str:
    """
    Submit benchmark to queue.
    Returns benchmark_id for tracking.
    """
    logger.info("=" * 80)
    logger.info("STEP 1: SUBMITTING BENCHMARK TO QUEUE")
    logger.info("=" * 80)

    cmd = [
        "python3",
        "scripts/benchmark_mmm.py",
        "--config",
        config_path,
        "--queue-name",
        queue_name,
    ]

    if top_n is not None and top_n < _MAX_CARTESIAN_COMBINATIONS:
        cmd.extend(["--top-n", str(top_n)])

    logger.info(f"🚀 Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"❌ Benchmark submission failed!")
        logger.error(result.stderr)
        sys.exit(1)

    # Parse output to get benchmark_id
    output = result.stdout
    logger.info(output)

    # Extract benchmark_id from output
    benchmark_id = None
    for line in output.split("\n"):
        if "Benchmark ID:" in line:
            benchmark_id = line.split("Benchmark ID:")[1].strip()
            break

    if not benchmark_id:
        logger.error("❌ Could not extract benchmark_id from output")
        sys.exit(1)

    logger.info(f"✅ Benchmark submitted successfully!")
    logger.info(f"   Benchmark ID: {benchmark_id}")

    return benchmark_id


def process_queue(queue_name: str):
    """Process the queue until all jobs complete."""
    logger.info("=" * 80)
    logger.info("STEP 2: PROCESSING QUEUE")
    logger.info("=" * 80)

    cmd = [
        "python3",
        "scripts/process_queue_simple.py",
        "--loop",
        "--cleanup",
        "--queue-name",
        queue_name,
    ]

    logger.info(f"⚙️  Running queue processor: {' '.join(cmd)}")
    logger.info(f"   This will process jobs until the queue is empty...")
    logger.info(f"   Press Ctrl+C if you want to stop early")

    try:
        result = subprocess.run(cmd)

        if result.returncode != 0:
            logger.warning(
                f"⚠️  Queue processor exited with code {result.returncode}"
            )
        else:
            logger.info(f"✅ Queue processing complete!")

    except KeyboardInterrupt:
        logger.info("\n⚠️  Queue processing interrupted by user")
        logger.info("   Jobs will continue running in the background")
        logger.info(
            "   You can check status later with process_queue_simple.py"
        )


def analyze_results(benchmark_id: str, queue_name: str = DEFAULT_QUEUE):
    """Analyze and visualize benchmark results."""
    logger.info("=" * 80)
    logger.info("STEP 3: ANALYZING RESULTS")
    logger.info("=" * 80)

    cmd = [
        "python3",
        "scripts/analyze_benchmark_results.py",
        "--benchmark-id",
        benchmark_id,
        "--output-dir",
        "./benchmark_analysis",
        "--queue-name",
        queue_name,
    ]

    logger.info(f"📊 Running analysis: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"❌ Analysis failed!")
        logger.error(result.stderr)
        logger.warning("   You can run analysis manually later with:")
        logger.warning(
            f"   python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id} --queue-name {queue_name}"
        )
    else:
        logger.info(result.stdout)
        logger.info(f"✅ Analysis complete!")
        logger.info(f"   Results saved to: ./benchmark_analysis/")


def main():
    parser = argparse.ArgumentParser(
        description="Complete end-to-end benchmarking workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test run (default - geometric only, 10 combos, reduced iterations/trials)
  python scripts/run_full_benchmark.py --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

  # Standard run, geometric only (18 combos cartesian, full window default, 1000 iterations, 3 trials)
  python scripts/run_full_benchmark.py --path <path> --full-run

  # Sequential run — each dimension tested independently (9 combos, faster exploration)
  python scripts/run_full_benchmark.py --path <path> --full-run --sequential

  # Standard run, all adstock types (54 combos cartesian)
  python scripts/run_full_benchmark.py --path <path> --full-run --all-adstock --top-n 54

  # Standard run, window-length sweep (54 combos: 1 adstock × 3 × 2 × 3 × 3 windows)
  python scripts/run_full_benchmark.py --path <path> --full-run --all-windows --top-n 54

  # Extended run, specific adstock types
  python scripts/run_full_benchmark.py --path <path> --extended-run --adstock geometric weibull_cdf

  # Extended run, window-length sweep with specific windows
  python scripts/run_full_benchmark.py --path <path> --extended-run --windows 2y 3y

  # Production run, all adstock + all windows (162 combos)
  python scripts/run_full_benchmark.py --path <path> --production-run --all-adstock --all-windows --top-n 162

  # Top-N combinations with extended run
  python scripts/run_full_benchmark.py --path <path> --top-n 10 --extended-run

  # Fleet marketplace config — geometric, test mode (~90 combos, 1.5 h, ~$15)
  python scripts/run_full_benchmark.py --path <path> \\
      --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json

  # Fleet marketplace config — standard run, geometric only (~90 combos, ~6 h, ~$75)
  python scripts/run_full_benchmark.py --path <path> \\
      --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \\
      --full-run

  # Fleet marketplace config — extended run, all adstock (~270 combos, ~40 h, ~$630)
  python scripts/run_full_benchmark.py --path <path> \\
      --config benchmarks/comprehensive_benchmark_fleet_marketplace.json \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments_fleet_marketplace.json \\
      --extended-run --all-adstock

  # With per-channel hyperparameter ranges (balanced preset is the default)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json

  # With an explicit preset (conservative / balanced / exploratory / fb / meshed)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --hyperparameter-preset exploratory

  # Shorthand preset flags: --fb (Facebook/Robyn defaults) or --meshed (Meshed recommended)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --fb

  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --meshed

  # Preset comparison: compare balanced, fb, and meshed in one run (3× variants)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --compare-presets

  # Compare all five presets in one run (5× variants)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --compare-all-presets

  # With custom queue
  python scripts/run_full_benchmark.py --path <path> --queue-name default-dev
        """,
    )

    parser.add_argument(
        "--path",
        required=True,
        help="Path to selected_columns.json (GCS path like gs://bucket/path/to/selected_columns.json)",
    )

    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to an existing benchmark config JSON "
            "(e.g. benchmarks/comprehensive_benchmark_fleet_marketplace.json). "
            "When provided, variant dimensions (adstock, splits, time_agg, "
            "spend_var, windows) are taken directly from that file instead of "
            "being generated dynamically from selected_columns.json. "
            "Run-mode flags (--full-run etc.) still override iterations and "
            "trials, and --all-adstock / --adstock can replace the adstock "
            "list. --all-windows and --windows are ignored (windows are "
            "defined in the config file)."
        ),
    )

    run_mode_group = parser.add_mutually_exclusive_group()
    run_mode_group.add_argument(
        "--full-run",
        action="store_true",
        help="Standard run (1000 iterations, 3 trials)",
    )
    run_mode_group.add_argument(
        "--extended-run",
        action="store_true",
        help="Extended run (2000 iterations, 5 trials, ~10-15 hours)",
    )
    run_mode_group.add_argument(
        "--production-run",
        action="store_true",
        help="Production run (5000 iterations, 5 trials, ~25-35 hours)",
    )

    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help=(
            "Number of combinations to submit (default: all generated). "
            "Set to limit e.g. --top-n 10 for a quick sweep."
        ),
    )

    adstock_group = parser.add_mutually_exclusive_group()
    adstock_group.add_argument(
        "--all-adstock",
        action="store_true",
        help="Test all 3 adstock types: geometric, weibull_cdf, weibull_pdf (triples variant count)",
    )
    adstock_group.add_argument(
        "--adstock",
        nargs="+",
        choices=["geometric", "weibull_cdf", "weibull_pdf"],
        metavar="TYPE",
        help="Adstock type(s) to test. Default: geometric only",
    )

    window_group = parser.add_mutually_exclusive_group()
    window_group.add_argument(
        "--all-windows",
        action="store_true",
        help="Test all 3 window lengths: full, 2y, 3y (triples variant count). Default: full only.",
    )
    window_group.add_argument(
        "--windows",
        nargs="+",
        choices=["full", "2y", "3y"],
        metavar="WINDOW",
        help=(
            "Window length(s) to test: full (all history), 2y (last 2 years), "
            "3y (last 3 years). Requires end_date in selected_columns.json. "
            "Non-test runs default to 'full' when no window flag is given."
        ),
    )

    parser.add_argument(
        "--sequential",
        action="store_true",
        help=(
            "Run tests sequentially per dimension instead of the cartesian product. "
            "Each dimension (adstock, splits, time_agg, spend_var) is varied "
            "independently, using base-config defaults for the other dimensions. "
            "A single 'full' window that carries no date override is skipped as a "
            "sequential dimension (it is the base default). "
            "With --config benchmarks/comprehensive_benchmark_fleet_marketplace.json "
            "and --compare-presets: 1+3+2+5+3 = 14 variants. "
            "Recommended for initial exploration before a full cartesian sweep."
        ),
    )

    parser.add_argument(
        "--queue-name",
        default=DEFAULT_QUEUE,
        help=f"Queue name (default: {DEFAULT_QUEUE})",
    )

    parser.add_argument(
        "--skip-queue",
        action="store_true",
        help="Skip queue processing (only submit benchmark)",
    )

    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip analysis (only submit and process queue)",
    )

    parser.add_argument(
        "--hyperparameter-ranges-config",
        metavar="PATH",
        default=None,
        help=(
            "Path to a hyperparameter ranges JSON file (relative to repo root). "
            "E.g. benchmarks/generic_hyperparameter_ranges_v2.json. "
            "When set, per-channel hyperparameter ranges are resolved from "
            "this file and embedded in the generated benchmark config."
        ),
    )
    parser.add_argument(
        "--channel-type-assignments-config",
        metavar="PATH",
        default=None,
        help=(
            "Path to a channel-type assignments JSON file (relative to repo root). "
            "E.g. benchmarks/channel_type_assignments.json. "
            "Maps variable names to channel types for hyperparameter range lookups. "
            "Required when --hyperparameter-ranges-config is set."
        ),
    )
    parser.add_argument(
        "--hyperparameter-preset",
        choices=["conservative", "balanced", "exploratory", "fb", "meshed"],
        default=None,
        help=(
            "Preset to use when resolving hyperparameter ranges "
            "(conservative / balanced / exploratory / fb / meshed). "
            "Defaults to 'balanced' when --hyperparameter-ranges-config is set. "
            "'fb' uses Robyn/Facebook official documentation defaults, channel-type-differentiated "
            "(Digital 0.0–0.3, OOH/Print/Radio 0.1–0.4, TV 0.3–0.8 at weekly frequency). "
            "'meshed' uses Meshed recommended ranges (channel-type-differentiated). "
            "Shorthand: use --fb or --meshed instead of --hyperparameter-preset fb/meshed."
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
    preset_shorthand_group.add_argument(
        "--compare-presets",
        dest="compare_presets",
        action="store_true",
        default=False,
        help=(
            "Compare balanced, fb, and meshed presets within a single benchmark run. "
            "Adds the hyperparameter preset as a variant dimension: each base combination "
            "is run 3× (once per preset). "
            "Requires --hyperparameter-ranges-config and --channel-type-assignments-config. "
            "Variant count is multiplied by 3 vs. the single-preset default."
        ),
    )
    preset_shorthand_group.add_argument(
        "--compare-all-presets",
        dest="compare_all_presets",
        action="store_true",
        default=False,
        help=(
            "Compare all five presets (conservative, balanced, exploratory, fb, meshed) "
            "within a single benchmark run. "
            "Each base combination is run 5×. "
            "Requires --hyperparameter-ranges-config and --channel-type-assignments-config. "
            "Variant count is multiplied by 5 vs. the single-preset default."
        ),
    )

    args = parser.parse_args()

    # Resolve shorthand preset flags and preset comparison modes
    compare_presets: Optional[List[Dict]] = None
    if args.fb:
        args.hyperparameter_preset = "fb"
    elif args.meshed:
        args.hyperparameter_preset = "meshed"
    elif args.compare_presets:
        compare_presets = PRESETS_COMPARE
        args.hyperparameter_preset = None
    elif args.compare_all_presets:
        compare_presets = PRESETS_ALL
        args.hyperparameter_preset = None

    # Determine run mode
    if args.production_run:
        run_mode = "production"
    elif args.extended_run:
        run_mode = "extended"
    elif args.full_run:
        run_mode = "standard"
    else:
        run_mode = "test"

    # Determine adstock types to test
    if args.all_adstock:
        adstock_types = ["geometric", "weibull_cdf", "weibull_pdf"]
    elif args.adstock:
        adstock_types = args.adstock
    else:
        adstock_types = None  # defaults to geometric only

    # Determine window lengths to test
    # Non-test runs default to ["full"] (applied inside generate_benchmark_config).
    if args.all_windows:
        window_lengths = ["full", "2y", "3y"]
    elif args.windows:
        window_lengths = args.windows
    else:
        window_lengths = (
            None  # generate_benchmark_config applies "full" for non-test
        )

    sequential = args.sequential

    # Print header
    logger.info("=" * 80)
    logger.info("COMPLETE BENCHMARKING WORKFLOW")
    logger.info("=" * 80)
    logger.info(f"Mode: {run_mode.upper()}")
    logger.info(
        f"Combination strategy: {'sequential (one dimension at a time)' if sequential else 'cartesian (product of all dimensions)'}"
    )
    logger.info(
        f"Adstock: {', '.join(adstock_types) if adstock_types else 'geometric (default)'}"
    )
    if window_lengths:
        logger.info(f"Windows: {', '.join(window_lengths)}")
    elif run_mode != "test":
        logger.info(
            "Windows: full (default — use --all-windows to add 2y / 3y)"
        )
    else:
        logger.info(
            "Windows: full (default — use --all-windows to add 2y / 3y)"
        )
    if args.top_n is not None:
        logger.info(f"Top-N combinations: {args.top_n}")
    logger.info(f"Data path: {args.path}")
    if args.config:
        logger.info(f"Benchmark config: {args.config}")
    logger.info(f"Queue: {args.queue_name}")
    if args.hyperparameter_ranges_config:
        logger.info(
            f"Hyperparameter ranges: {args.hyperparameter_ranges_config}"
        )
        logger.info(
            f"Channel type assignments: {args.channel_type_assignments_config or '(none)'}"
        )
        if compare_presets:
            preset_names = [p["name"] for p in compare_presets]
            logger.info(
                f"Preset comparison: {', '.join(preset_names)} ({len(compare_presets)} presets)"
            )
        else:
            preset_label = args.hyperparameter_preset or "balanced (default)"
            logger.info(f"Hyperparameter preset: {preset_label}")
        logger.info(
            "  Per-variable range resolution will be logged at the "
            "benchmark submission step (one line per variable per variant)."
        )
    logger.info("=" * 80)
    logger.info("")

    try:
        # Step 0: Download and parse selected_columns.json
        logger.info("STEP 0: LOADING CONFIGURATION")
        logger.info("=" * 80)
        selected_columns = download_selected_columns(args.path)

        # Extract version (timestamp) from GCS path
        version_from_path = extract_version_from_path(args.path)
        logger.info(f"📍 Extracted version from path: {version_from_path}")
        logger.info("")

        # Generate (or load) benchmark configuration
        if args.config:
            # Use an external config file; run mode overrides iterations/trials.
            benchmark_config = load_external_benchmark_config(
                args.config,
                run_mode=run_mode,
                adstock_types=adstock_types,
                window_lengths=window_lengths,
                sequential=sequential,
                hyperparameter_ranges_config=args.hyperparameter_ranges_config,
                channel_type_assignments_config=args.channel_type_assignments_config,
                hyperparameter_preset=args.hyperparameter_preset,
                compare_presets=compare_presets,
            )
            # Always overwrite base_config with the actual country/goal/version
            # derived from --path.  External configs ship with a placeholder
            # base_config (e.g. "country": "FILL_IN_COUNTRY_CODE") that must
            # be replaced before benchmark_mmm.py uses it to fetch the real
            # selected_columns.json from GCS.
            country = selected_columns.get("country", "")
            goal = selected_columns.get("selected_goal", "")
            benchmark_config["base_config"] = {
                "country": country,
                "goal": goal,
                "version": version_from_path,
            }
            logger.info(
                f"📌 Base config injected from --path: "
                f"{country}/{goal}/{version_from_path}"
            )
        else:
            # Build config dynamically from selected_columns.json.
            benchmark_config = generate_benchmark_config(
                selected_columns,
                version_from_path=version_from_path,
                run_mode=run_mode,
                adstock_types=adstock_types,
                window_lengths=window_lengths,
                sequential=sequential,
                hyperparameter_ranges_config=args.hyperparameter_ranges_config,
                channel_type_assignments_config=args.channel_type_assignments_config,
                hyperparameter_preset=args.hyperparameter_preset,
                compare_presets=compare_presets,
            )

        # Derive top_n: if not specified, submit all generated variants.
        # max_combinations is always set to n_combos + 10 when generated
        # dynamically; external configs may omit it, in which case we fall
        # back to None (submit all variants).
        top_n = (
            args.top_n
            if args.top_n is not None
            else benchmark_config.get("max_combinations")
        )

        # ── Pre-submission variant summary ────────────────────────────────
        # Log the key Robyn parameters for every variant so the operator
        # can verify what will be sent before jobs hit the queue.
        variants_summary = benchmark_config.get("variants", {})
        combination_mode = benchmark_config.get("combination_mode", "single")
        logger.info("")
        logger.info("=" * 72)
        logger.info("BENCHMARK VARIANT PLAN (what will be sent to Robyn)")
        logger.info("=" * 72)
        logger.info(f"  Combination mode : {combination_mode}")
        logger.info(
            f"  Iterations       : {benchmark_config.get('iterations')}"
        )
        logger.info(f"  Trials           : {benchmark_config.get('trials')}")
        logger.info(
            f"  Hyperparameter ranges config : "
            f"{benchmark_config.get('hyperparameter_ranges_config', '(none)')}"
        )
        logger.info(
            f"  Channel type assignments     : "
            f"{benchmark_config.get('channel_type_assignments_config', '(none)')}"
        )
        logger.info("")
        for dim_name, dim_variants in variants_summary.items():
            if not isinstance(dim_variants, list):
                continue
            logger.info(
                f"  Dimension '{dim_name}' — {len(dim_variants)} variant(s):"
            )
            for v in dim_variants:
                name = v.get("name", "?")
                desc = v.get("description", "")
                preset = v.get("hyperparameter_preset", "")
                extra = f"  preset={preset}" if preset else ""
                logger.info(f"    • {name:<28} {desc[:50]}{extra}")
        logger.info("=" * 72)
        logger.info("")
        # ─────────────────────────────────────────────────────────────────

        # Save to temporary file
        config_path = save_temp_benchmark_config(benchmark_config)
        logger.info("")

        # Step 1: Submit benchmark
        benchmark_id = run_benchmark_submission(
            config_path, args.queue_name, top_n=top_n
        )
        logger.info("")

        # Clean up temp file
        os.unlink(config_path)

        # Step 2: Process queue (optional)
        if not args.skip_queue:
            process_queue(args.queue_name)
            logger.info("")
        else:
            logger.info("⏭️  Skipping queue processing (--skip-queue)")
            logger.info(
                f"   Run manually: python scripts/process_queue_simple.py --loop --queue-name {args.queue_name}"
            )
            logger.info("")

        # Step 3: Analyze results (optional)
        if not args.skip_analysis:
            if args.skip_queue:
                logger.info(
                    "ℹ️  Queue processing was skipped — analyzing any "
                    "results already available in GCS. "
                    "Re-run analysis after all jobs complete for full results:"
                )
                logger.info(
                    f"   python scripts/analyze_benchmark_results.py "
                    f"--benchmark-id {benchmark_id} "
                    f"--queue-name {args.queue_name}"
                )
            analyze_results(benchmark_id, args.queue_name)
            logger.info("")
        else:
            logger.info("⏭️  Skipping analysis (--skip-analysis)")
            logger.info(
                f"   Run manually: python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id} --queue-name {args.queue_name}"
            )
            logger.info("")

        # Final summary
        logger.info("=" * 80)
        logger.info("✅ WORKFLOW COMPLETE!")
        logger.info("=" * 80)
        logger.info(f"Benchmark ID: {benchmark_id}")

        if not args.skip_analysis:
            logger.info(f"Results: ./benchmark_analysis/")
            logger.info(f"CSV: ./benchmark_analysis/results_*.csv")
            logger.info(f"Plots: ./benchmark_analysis/*.png")

        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Review results in ./benchmark_analysis/")
        logger.info("  2. Identify best-performing configurations")
        logger.info("  3. Apply learnings to production models")
        logger.info("=" * 80)

    except KeyboardInterrupt:
        logger.info("\n⚠️  Workflow interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
