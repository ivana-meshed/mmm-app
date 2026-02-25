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
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

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
        if re.match(r'\d{8}_\d{6}', part):
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
    
    logger.info(f"✅ Downloaded config for country: {config.get('country')}, goal: {config.get('selected_goal')}")
    
    return config


def generate_benchmark_config(selected_columns: Dict[str, Any], version_from_path: str, is_test_run: bool = True) -> Dict[str, Any]:
    """
    Generate comprehensive benchmark configuration from selected_columns.json.
    
    Creates cartesian product of:
    - 3 adstock types
    - 3 train/test splits
    - 2 time aggregations
    - 3 spend→var mapping strategies
    = 54 total combinations
    """
    country = selected_columns.get("country", "de")
    goal = selected_columns.get("selected_goal", "N_UPLOADS_WEB")
    timestamp = selected_columns.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
    
    # Base configuration - use version from GCS path, not data_version from JSON
    base_config = {
        "country": country,
        "goal": goal,
        "version": version_from_path  # Use the timestamp from GCS path
    }
    
    # Iterations and trials based on test vs full run
    if is_test_run:
        iterations = 10
        trials = 1
        logger.info("🧪 TEST RUN MODE - Using reduced iterations (10) and trials (1)")
    else:
        iterations = 1000
        trials = 3
        logger.info("🚀 FULL RUN MODE - Using full iterations (1000) and trials (3)")
    
    # Build comprehensive benchmark config
    benchmark_config = {
        "name": f"comprehensive_benchmark_{timestamp}",
        "description": "Complete cartesian product benchmark: adstock × train_splits × time_agg × spend_var_mapping",
        "base_config": base_config,
        "iterations": iterations,
        "trials": trials,
        "max_combinations": 60,
        "combination_mode": "cartesian",
        "variants": {
            "adstock": [
                {
                    "name": "geometric",
                    "description": "Geometric adstock",
                    "adstock": "geometric",
                    "hyperparameter_preset": "Meshed recommend"
                },
                {
                    "name": "weibull_cdf",
                    "description": "Weibull CDF adstock",
                    "adstock": "weibull_cdf",
                    "hyperparameter_preset": "Meta default"
                },
                {
                    "name": "weibull_pdf",
                    "description": "Weibull PDF adstock",
                    "adstock": "weibull_pdf",
                    "hyperparameter_preset": "Meshed recommend"
                }
            ],
            "train_splits": [
                {
                    "name": "70_90",
                    "description": "70% train, 20% val, 10% test",
                    "train_size": [0.7, 0.9]
                },
                {
                    "name": "75_90",
                    "description": "75% train, 15% val, 10% test",
                    "train_size": [0.75, 0.9]
                },
                {
                    "name": "65_80",
                    "description": "65% train, 15% val, 20% test",
                    "train_size": [0.65, 0.8]
                }
            ],
            "time_aggregation": [
                {
                    "name": "daily",
                    "description": "Daily time aggregation",
                    "resample_freq": "none"
                },
                {
                    "name": "weekly",
                    "description": "Weekly time aggregation",
                    "resample_freq": "W"
                }
            ],
            "spend_var_mapping": [
                {
                    "name": "spend_to_spend",
                    "description": "All channels: spend → spend",
                    "mapping_strategy": "spend_to_spend"
                },
                {
                    "name": "spend_to_proxy",
                    "description": "All channels: spend → sessions",
                    "mapping_strategy": "spend_to_proxy"
                },
                {
                    "name": "mixed_by_funnel",
                    "description": "Upper funnel → sessions, lower → spend",
                    "mapping_strategy": "mixed"
                }
            ]
        }
    }
    
    logger.info(f"📊 Generated benchmark config:")
    logger.info(f"   Country: {country}")
    logger.info(f"   Goal: {goal}")
    logger.info(f"   Iterations: {iterations}")
    logger.info(f"   Trials: {trials}")
    logger.info(f"   Expected variants: 54 (3 × 3 × 2 × 3)")
    
    return benchmark_config


def save_temp_benchmark_config(config: Dict[str, Any]) -> str:
    """Save benchmark config to temporary file."""
    import tempfile
    
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(config, temp_file, indent=2)
    temp_file.close()
    
    logger.info(f"💾 Saved temporary benchmark config: {temp_file.name}")
    return temp_file.name


def run_benchmark_submission(config_path: str) -> str:
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
        "--config", config_path
    ]
    
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
    for line in output.split('\n'):
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
        "--queue-name", queue_name
    ]
    
    logger.info(f"⚙️  Running queue processor: {' '.join(cmd)}")
    logger.info(f"   This will process jobs until the queue is empty...")
    logger.info(f"   Press Ctrl+C if you want to stop early")
    
    try:
        result = subprocess.run(cmd)
        
        if result.returncode != 0:
            logger.warning(f"⚠️  Queue processor exited with code {result.returncode}")
        else:
            logger.info(f"✅ Queue processing complete!")
    
    except KeyboardInterrupt:
        logger.info("\n⚠️  Queue processing interrupted by user")
        logger.info("   Jobs will continue running in the background")
        logger.info("   You can check status later with process_queue_simple.py")


def analyze_results(benchmark_id: str):
    """Analyze and visualize benchmark results."""
    logger.info("=" * 80)
    logger.info("STEP 3: ANALYZING RESULTS")
    logger.info("=" * 80)
    
    cmd = [
        "python3",
        "scripts/analyze_benchmark_results.py",
        "--benchmark-id", benchmark_id,
        "--output-dir", "./benchmark_analysis"
    ]
    
    logger.info(f"📊 Running analysis: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"❌ Analysis failed!")
        logger.error(result.stderr)
        logger.warning("   You can run analysis manually later with:")
        logger.warning(f"   python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id}")
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
  # Test run (default - reduced iterations/trials)
  python scripts/run_full_benchmark.py --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json
  
  # Full production run
  python scripts/run_full_benchmark.py --path <path> --full-run
  
  # With custom queue
  python scripts/run_full_benchmark.py --path <path> --queue-name default-dev
        """
    )
    
    parser.add_argument(
        "--path",
        required=True,
        help="Path to selected_columns.json (GCS path like gs://bucket/path/to/selected_columns.json)"
    )
    
    parser.add_argument(
        "--full-run",
        action="store_true",
        help="Run full benchmark (1000 iterations, 3 trials). Default is test run (10 iterations, 1 trial)"
    )
    
    parser.add_argument(
        "--queue-name",
        default=DEFAULT_QUEUE,
        help=f"Queue name (default: {DEFAULT_QUEUE})"
    )
    
    parser.add_argument(
        "--skip-queue",
        action="store_true",
        help="Skip queue processing (only submit benchmark)"
    )
    
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip analysis (only submit and process queue)"
    )
    
    args = parser.parse_args()
    
    # Print header
    logger.info("=" * 80)
    logger.info("COMPLETE BENCHMARKING WORKFLOW")
    logger.info("=" * 80)
    logger.info(f"Mode: {'FULL RUN' if args.full_run else 'TEST RUN'}")
    logger.info(f"Config path: {args.path}")
    logger.info(f"Queue: {args.queue_name}")
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
            is_test_run=not args.full_run
        )
        
        # Save to temporary file
        config_path = save_temp_benchmark_config(benchmark_config)
        logger.info("")
        
        # Step 1: Submit benchmark
        benchmark_id = run_benchmark_submission(config_path)
        logger.info("")
        
        # Clean up temp file
        os.unlink(config_path)
        
        # Step 2: Process queue (optional)
        if not args.skip_queue:
            process_queue(args.queue_name)
            logger.info("")
        else:
            logger.info("⏭️  Skipping queue processing (--skip-queue)")
            logger.info(f"   Run manually: python scripts/process_queue_simple.py --loop --queue-name {args.queue_name}")
            logger.info("")
        
        # Step 3: Analyze results (optional)
        if not args.skip_analysis and not args.skip_queue:
            analyze_results(benchmark_id)
            logger.info("")
        elif args.skip_analysis:
            logger.info("⏭️  Skipping analysis (--skip-analysis)")
            logger.info(f"   Run manually: python scripts/analyze_benchmark_results.py --benchmark-id {benchmark_id}")
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
