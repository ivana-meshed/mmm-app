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

    # With per-channel hyperparameter ranges
    python scripts/run_full_benchmark.py --path <path> --full-run \\
        --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
        --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
        --hyperparameter-preset balanced

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


def generate_benchmark_config(
    selected_columns: Dict[str, Any],
    version_from_path: str,
    run_mode: str = "test",
    adstock_types: list = None,
    window_lengths: list = None,
    hyperparameter_ranges_config: Optional[str] = None,
    channel_type_assignments_config: Optional[str] = None,
    hyperparameter_preset: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate comprehensive benchmark configuration from selected_columns.json.

    Creates cartesian product of:
    - N adstock types (default: 1 – geometric only; use --all-adstock for all 3)
    - 3 train/test splits
    - 2 time aggregations
    - 3 spend→var mapping strategies
    - M window lengths (default: none; use --all-windows / --windows to add)
    = 18 combinations by default (geometric, no window sweep),
      54 when all adstock tested,
      54 when geometric + all 3 windows,
      162 when all adstock + all 3 windows

    run_mode options:
    - "test": 10 iterations, 1 trial
    - "standard": 1000 iterations, 3 trials
    - "extended": 2000 iterations, 5 trials
    - "production": 5000 iterations, 5 trials

    adstock_types: list of adstock names to include (None → geometric only)
    window_lengths: list of window names from ALL_WINDOW_VARIANTS to include
                    (None → no window sweep; the base config dates are used)
    hyperparameter_ranges_config: optional path to a hyperparameter ranges JSON
                    file (relative to repo root), e.g.
                    "benchmarks/generic_hyperparameter_ranges_v2.json"
    channel_type_assignments_config: optional path to a channel-type assignments
                    JSON file (relative to repo root), e.g.
                    "benchmarks/channel_type_assignments.json"
    hyperparameter_preset: one of "conservative", "balanced" (default),
                    or "exploratory"
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

    # Resolve window-length variants to include
    window_name_map = {v["name"]: v for v in ALL_WINDOW_VARIANTS}
    selected_windows_raw: Optional[List[Dict]] = None
    if window_lengths:
        selected_windows_raw = [
            window_name_map[name]
            for name in window_lengths
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
            # else: no override → base config dates used
            selected_window_specs.append(spec)

        n_windows = len(selected_window_specs)
        window_label = ", ".join(w["name"] for w in selected_windows_raw)
        logger.info(f"📅 Window lengths: {window_label} ({n_windows} variants)")
    else:
        n_windows = 1  # counts as one implicit "full" window in the product

    n_combos = n_adstock * 3 * 2 * 3 * n_windows  # adstock × splits × time_agg × spend_var × windows
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

    # Build comprehensive benchmark config
    window_dim = f" × {n_windows} windows" if selected_window_specs else ""
    benchmark_config = {
        "name": f"comprehensive_benchmark_{timestamp}",
        "description": (
            "Complete cartesian product benchmark: "
            f"adstock × train_splits × time_agg × spend_var_mapping{window_dim}"
        ),
        "base_config": base_config,
        "iterations": iterations,
        "trials": trials,
        "max_combinations": max_combinations,
        "combination_mode": "cartesian",
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
    if hyperparameter_preset:
        benchmark_config["hyperparameter_preset"] = hyperparameter_preset
        logger.info(f"🔧 Hyperparameter preset: {hyperparameter_preset}")

    window_factor = f" × {n_windows}" if selected_window_specs else ""
    logger.info(f"📊 Generated benchmark config:")
    logger.info(f"   Country: {country}")
    logger.info(f"   Goal: {goal}")
    logger.info(f"   Iterations: {iterations}")
    logger.info(f"   Trials: {trials}")
    logger.info(
        f"   Expected variants: {n_combos} ({n_adstock} × 3 × 2 × 3{window_factor})"
    )

    return benchmark_config


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
    config_path: str, queue_name: str = DEFAULT_QUEUE, top_n: int = 54
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

    if top_n < 54:
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


def analyze_results(benchmark_id: str):
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
    ]

    logger.info(f"📊 Running analysis: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"❌ Analysis failed!")
        logger.error(result.stderr)
        logger.warning("   You can run analysis manually later with:")
        logger.warning(
            f"   python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id}"
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
  # Test run (default - geometric only, 18 combos, reduced iterations/trials)
  python scripts/run_full_benchmark.py --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

  # Standard run, geometric only (18 combos, 1000 iterations, 3 trials)
  python scripts/run_full_benchmark.py --path <path> --full-run

  # Standard run, all adstock types (54 combos)
  python scripts/run_full_benchmark.py --path <path> --full-run --all-adstock --top-n 54

  # Standard run, window-length sweep only (54 combos: 1 adstock × 3 × 2 × 3 × 3 windows)
  python scripts/run_full_benchmark.py --path <path> --full-run --all-windows --top-n 54

  # Extended run, specific adstock types
  python scripts/run_full_benchmark.py --path <path> --extended-run --adstock geometric weibull_cdf

  # Extended run, window-length sweep with specific windows
  python scripts/run_full_benchmark.py --path <path> --extended-run --windows 2y 3y

  # Production run, all adstock + all windows (162 combos)
  python scripts/run_full_benchmark.py --path <path> --production-run --all-adstock --all-windows --top-n 162

  # Top-N combinations with extended run
  python scripts/run_full_benchmark.py --path <path> --top-n 10 --extended-run

  # With per-channel hyperparameter ranges (balanced preset is the default)
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json

  # With hyperparameter ranges and an explicit preset
  python scripts/run_full_benchmark.py --path <path> --full-run \\
      --hyperparameter-ranges-config benchmarks/generic_hyperparameter_ranges_v2.json \\
      --channel-type-assignments-config benchmarks/channel_type_assignments.json \\
      --hyperparameter-preset exploratory

  # With custom queue
  python scripts/run_full_benchmark.py --path <path> --queue-name default-dev
        """,
    )

    parser.add_argument(
        "--path",
        required=True,
        help="Path to selected_columns.json (GCS path like gs://bucket/path/to/selected_columns.json)",
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
        default=18,
        help=(
            "Number of combinations to submit (default: 18 = geometric only). "
            "Set higher when using --all-adstock / --all-windows."
        ),
    )

    adstock_group = parser.add_mutually_exclusive_group()
    adstock_group.add_argument(
        "--all-adstock",
        action="store_true",
        help="Test all 3 adstock types: geometric, weibull_cdf, weibull_pdf (54 combos)",
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
        help="Test all 3 window lengths: full, 2y, 3y (multiplies combos by 3)",
    )
    window_group.add_argument(
        "--windows",
        nargs="+",
        choices=["full", "2y", "3y"],
        metavar="WINDOW",
        help=(
            "Window length(s) to test: full (all history), 2y (last 2 years), "
            "3y (last 3 years). Requires end_date in selected_columns.json."
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
        choices=["conservative", "balanced", "exploratory"],
        default=None,
        help=(
            "Preset to use when resolving hyperparameter ranges "
            "(conservative / balanced / exploratory). "
            "Defaults to 'balanced' when --hyperparameter-ranges-config is set."
        ),
    )

    args = parser.parse_args()

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
    if args.all_windows:
        window_lengths = ["full", "2y", "3y"]
    elif args.windows:
        window_lengths = args.windows
    else:
        window_lengths = None  # no window sweep

    # Print header
    logger.info("=" * 80)
    logger.info("COMPLETE BENCHMARKING WORKFLOW")
    logger.info("=" * 80)
    logger.info(f"Mode: {run_mode.upper()}")
    logger.info(f"Adstock: {', '.join(adstock_types) if adstock_types else 'geometric (default)'}")
    logger.info(f"Windows: {', '.join(window_lengths) if window_lengths else 'none (base config dates)'}")
    logger.info(f"Top-N combinations: {args.top_n}")
    logger.info(f"Config path: {args.path}")
    logger.info(f"Queue: {args.queue_name}")
    if args.hyperparameter_ranges_config:
        logger.info(
            f"Hyperparameter ranges: {args.hyperparameter_ranges_config}"
        )
        logger.info(
            f"Channel type assignments: {args.channel_type_assignments_config or '(none)'}"
        )
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

        # Generate benchmark configuration
        benchmark_config = generate_benchmark_config(
            selected_columns,
            version_from_path=version_from_path,
            run_mode=run_mode,
            adstock_types=adstock_types,
            window_lengths=window_lengths,
            hyperparameter_ranges_config=args.hyperparameter_ranges_config,
            channel_type_assignments_config=args.channel_type_assignments_config,
            hyperparameter_preset=args.hyperparameter_preset,
        )

        # Save to temporary file
        config_path = save_temp_benchmark_config(benchmark_config)
        logger.info("")

        # Step 1: Submit benchmark
        benchmark_id = run_benchmark_submission(
            config_path, args.queue_name, top_n=args.top_n
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
        if not args.skip_analysis and not args.skip_queue:
            analyze_results(benchmark_id)
            logger.info("")
        elif args.skip_analysis:
            logger.info("⏭️  Skipping analysis (--skip-analysis)")
            logger.info(
                f"   Run manually: python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id}"
            )
            logger.info("")

        # Final summary
        logger.info("=" * 80)
        logger.info("✅ WORKFLOW COMPLETE!")
        logger.info("=" * 80)
        logger.info(f"Benchmark ID: {benchmark_id}")

        if not args.skip_analysis and not args.skip_queue:
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
