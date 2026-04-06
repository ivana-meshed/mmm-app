#!/usr/bin/env python3
"""
Comprehensive DK Benchmark Test

Runs run_full_benchmark.py for every DK context configuration defined in
benchmark_analysis/dk_json_configs_clean/, using the production benchmark
settings.

For each configuration the script:
  1. Uploads the local config JSON to GCS as ``selected_columns.json`` at
     the path derived from the config's own ``country``, ``selected_goal``,
     and ``timestamp`` fields.
  2. Invokes ``run_full_benchmark.py`` with ``--skip-queue`` so that all
     variants are submitted to the queue in one sweep without waiting for
     each config to finish before the next starts.
  3. After all submissions, optionally processes the queue once so that
     results from every config are produced together.

Results land in GCS under ``benchmarks/<benchmark_id>/`` and are
immediately visible on the **Benchmark Results** page of the Streamlit app.

Usage (final command):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev

Dry-run (prints commands without executing them):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --dry-run

Skip uploading configs to GCS (use when they are already there):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --skip-upload

Run a single named config:

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --only dk_context_minimal

Process the queue after all submissions (blocking):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --process-queue
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

DK_CONFIGS_DIR = (
    REPO_ROOT / "benchmark_analysis" / "dk_json_configs_clean"
)
MANIFEST_FILE = DK_CONFIGS_DIR / "dk_context_testing_manifest_clean.json"

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
GCS_BASE = f"gs://{GCS_BUCKET}"
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")

# Fixed benchmark / hyperparameter config paths (relative to repo root)
BENCHMARK_CONFIG = (
    "benchmarks/comprehensive_benchmark_fleet_marketplace_prod.json"
)
HYPERPARAMETER_RANGES_CONFIG = (
    "benchmarks/generic_hyperparameter_ranges_v2_prod.json"
)
CHANNEL_TYPE_ASSIGNMENTS_CONFIG = (
    "benchmarks/channel_type_assignments_prod.json"
)

# Configs to run in recommended order (names without the _clean.json suffix).
# Derived from the manifest's recommended_order; the manifest uses the
# original filenames (without "_clean"), so we map them here.
_MANIFEST_TO_CLEAN = {
    "dk_context_minimal.json": "dk_context_minimal_clean.json",
    "dk_context_supply.json": "dk_context_supply_clean.json",
    "dk_context_supply_plus_occ7d.json": "dk_context_supply_plus_occ7d_clean.json",
    "dk_context_occ_current_test.json": "dk_context_occ_current_test_clean.json",
    "dk_context_expanded_test.json": "dk_context_expanded_test_clean.json",
    "dk_context_occ30d_test.json": "dk_context_occ30d_test_clean.json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_manifest() -> Dict:
    """Load the DK testing manifest."""
    with open(MANIFEST_FILE) as f:
        return json.load(f)


def ordered_config_files() -> List[str]:
    """Return clean config filenames in the manifest's recommended order."""
    manifest = load_manifest()
    ordered = []
    for name in manifest.get("recommended_order", []):
        clean = _MANIFEST_TO_CLEAN.get(name)
        if clean:
            ordered.append(clean)
    return ordered


def load_config(filename: str) -> Dict:
    """Load a JSON config from the DK configs directory."""
    with open(DK_CONFIGS_DIR / filename) as f:
        return json.load(f)


def gcs_path_for_config(config: Dict) -> str:
    """
    Build the GCS path where this config should live as selected_columns.json.

    Format: gs://<bucket>/training_data/<country>/<goal>/<timestamp>/selected_columns.json
    """
    country = config["country"].lower()
    goal = config["selected_goal"]
    timestamp = config["timestamp"]
    return (
        f"{GCS_BASE}/training_data/{country}/{goal}"
        f"/{timestamp}/selected_columns.json"
    )


def upload_config_to_gcs(config: Dict, gcs_path: str, dry_run: bool) -> bool:
    """
    Upload the config dict to GCS as selected_columns.json.

    Returns True on success (or in dry-run mode), False on error.
    """
    if dry_run:
        logger.info(f"  [dry-run] Would upload to: {gcs_path}")
        return True

    try:
        from google.cloud import storage

        bucket_name = gcs_path.replace("gs://", "").split("/")[0]
        blob_path = "/".join(
            gcs_path.replace("gs://", "").split("/")[1:]
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(config, tmp, indent=2)
            tmp_path = tmp.name

        try:
            client = storage.Client(project=PROJECT_ID)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.upload_from_filename(tmp_path)
            logger.info(f"  ✅ Uploaded to {gcs_path}")
            return True
        finally:
            os.unlink(tmp_path)

    except Exception as exc:
        logger.error(f"  ❌ Upload failed: {exc}")
        return False


def run_benchmark(
    gcs_path: str,
    queue_name: str,
    skip_queue: bool,
    dry_run: bool,
) -> bool:
    """
    Invoke run_full_benchmark.py for a single config GCS path.

    Returns True on success (returncode == 0), False otherwise.
    """
    cmd = [
        sys.executable,
        "scripts/run_full_benchmark.py",
        "--path",
        gcs_path,
        "--config",
        BENCHMARK_CONFIG,
        "--full-run",
        "--hyperparameter-ranges-config",
        HYPERPARAMETER_RANGES_CONFIG,
        "--channel-type-assignments-config",
        CHANNEL_TYPE_ASSIGNMENTS_CONFIG,
        "--queue-name",
        queue_name,
    ]
    if skip_queue:
        cmd += ["--skip-queue", "--skip-analysis"]

    logger.info(f"  Running: {' '.join(cmd)}")

    if dry_run:
        logger.info("  [dry-run] Command not executed.")
        return True

    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        logger.error(
            f"  ❌ run_full_benchmark.py exited with code {result.returncode}"
        )
        return False
    return True


def process_queue(queue_name: str, dry_run: bool) -> None:
    """Run process_queue_simple.py --loop to drain the queue."""
    cmd = [
        sys.executable,
        "scripts/process_queue_simple.py",
        "--loop",
        "--cleanup",
        "--queue-name",
        queue_name,
    ]
    logger.info(f"\n{'='*80}")
    logger.info("PROCESSING QUEUE (waiting for all jobs to complete) …")
    logger.info(f"{'='*80}")
    logger.info(f"Running: {' '.join(cmd)}")
    if dry_run:
        logger.info("[dry-run] Command not executed.")
        return
    subprocess.run(cmd, cwd=str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run run_full_benchmark.py for every DK context configuration "
            "in benchmark_analysis/dk_json_configs_clean/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full run — upload configs, submit all to queue, then process:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default-dev --process-queue

  # Dry-run — print commands without executing:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default-dev --dry-run

  # Skip GCS upload (configs already uploaded):
  python scripts/run_dk_benchmark_all_configs.py --queue-name default-dev --skip-upload

  # Run a single config:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default-dev --only dk_context_minimal
        """,
    )

    parser.add_argument(
        "--queue-name",
        default="default-dev",
        help="Cloud Tasks queue name (default: default-dev)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print every GCS upload and benchmark command without executing "
            "them. Useful for verifying paths before a live run."
        ),
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help=(
            "Skip uploading configs to GCS. Use when the selected_columns.json "
            "files already exist at the expected GCS paths."
        ),
    )
    parser.add_argument(
        "--process-queue",
        action="store_true",
        help=(
            "After submitting all benchmarks, run process_queue_simple.py "
            "--loop to drain the queue. Without this flag only submissions "
            "are made (--skip-queue is passed to each run_full_benchmark.py "
            "call) and you must process the queue separately."
        ),
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        default=None,
        help=(
            "Run a single config by its short name, e.g. 'dk_context_minimal' "
            "or 'dk_context_supply_plus_occ7d'. When omitted all configs in "
            "the manifest's recommended_order are processed."
        ),
    )

    args = parser.parse_args()

    # Resolve the list of config files to process
    all_configs = ordered_config_files()
    if not all_configs:
        logger.error(
            "No configs found. Check that the manifest exists at "
            f"{MANIFEST_FILE}"
        )
        sys.exit(1)

    if args.only:
        # Accept either the short name or the full filename
        target = args.only
        if not target.endswith(".json"):
            target = f"{target}_clean.json"
        if target not in all_configs:
            logger.error(
                f"Config '{target}' not found in manifest order. "
                f"Available: {all_configs}"
            )
            sys.exit(1)
        config_files = [target]
    else:
        config_files = all_configs

    # Print header
    logger.info("=" * 80)
    logger.info("DK COMPREHENSIVE BENCHMARK TEST")
    logger.info("=" * 80)
    logger.info(f"Configs to test : {len(config_files)}")
    logger.info(f"Queue           : {args.queue_name}")
    logger.info(f"Benchmark config: {BENCHMARK_CONFIG}")
    logger.info(f"HP ranges config: {HYPERPARAMETER_RANGES_CONFIG}")
    logger.info(f"Channel types   : {CHANNEL_TYPE_ASSIGNMENTS_CONFIG}")
    logger.info(f"Skip upload     : {args.skip_upload}")
    logger.info(f"Process queue   : {args.process_queue}")
    logger.info(f"Dry run         : {args.dry_run}")
    logger.info("=" * 80)
    logger.info("")

    failed: List[str] = []

    for idx, filename in enumerate(config_files, start=1):
        config = load_config(filename)
        gcs_path = gcs_path_for_config(config)

        config_name = config.get("name") or filename.replace("_clean.json", "")
        description = config.get("description", "")

        logger.info(f"[{idx}/{len(config_files)}] {config_name}")
        logger.info(f"  Description : {description}")
        logger.info(f"  GCS path    : {gcs_path}")

        # Step 1: Upload config to GCS
        if not args.skip_upload:
            ok = upload_config_to_gcs(config, gcs_path, dry_run=args.dry_run)
            if not ok:
                logger.error(f"  Skipping benchmark for {filename} due to upload failure.")
                failed.append(filename)
                continue

        # Step 2: Submit benchmark (skip_queue=True so we batch all submissions)
        ok = run_benchmark(
            gcs_path=gcs_path,
            queue_name=args.queue_name,
            skip_queue=not args.process_queue,
            dry_run=args.dry_run,
        )
        if not ok:
            failed.append(filename)

        logger.info("")

    # Step 3: Process queue (if requested and submissions were batched)
    if args.process_queue and not args.dry_run:
        process_queue(args.queue_name, dry_run=False)
    elif args.process_queue and args.dry_run:
        process_queue(args.queue_name, dry_run=True)

    # Summary
    logger.info("=" * 80)
    if failed:
        logger.error(
            f"❌ {len(failed)} config(s) failed: {', '.join(failed)}"
        )
        logger.error(
            "   Re-run with --skip-upload to retry failed benchmarks "
            "if the upload step already succeeded."
        )
        sys.exit(1)
    else:
        n = len(config_files)
        logger.info(f"✅ All {n} config(s) submitted successfully.")
        if not args.process_queue:
            logger.info("")
            logger.info(
                "📋 Next step: process the queue to run the training jobs:"
            )
            logger.info(
                f"   python scripts/process_queue_simple.py "
                f"--loop --cleanup --queue-name {args.queue_name}"
            )
        logger.info("")
        logger.info(
            "📊 Results will appear on the Benchmark Results page once "
            "jobs complete."
        )
        logger.info("=" * 80)


if __name__ == "__main__":
    main()
