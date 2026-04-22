#!/usr/bin/env python3
"""
Comprehensive DK Benchmark Test

Runs run_full_benchmark.py for every DK context configuration defined in
benchmark_analysis/dk_json_configs_clean/, using the production benchmark
settings.

For each configuration the script:
  1. Enriches the local config with the fields required by benchmark_mmm.py
     (``dep_var``, ``dep_var_type``, ``date_var``) that are absent from the
     raw config files but needed for the training jobs to work correctly.
  2. Validates the enriched config has all required fields before uploading.
  3. Uploads the enriched config to GCS as ``selected_columns.json`` at the
     path derived from the config's ``country``, ``selected_goal``, and
     ``timestamp`` fields:
         gs://<bucket>/training_data/<country>/<goal>/<timestamp>/selected_columns.json
  4. Invokes ``run_full_benchmark.py`` with ``--skip-queue`` so that all
     variants are submitted to the queue in one sweep without waiting for
     each config to finish before the next starts.
  5. After all submissions, optionally processes the queue once so that
     results from every config are produced together.

Results land in GCS under ``benchmarks/<benchmark_id>/`` and are
immediately visible on the **Benchmark Results** page of the Streamlit app.

Two manifests are available in benchmark_analysis/dk_json_configs_clean/:

  dk_context_reduced_manifest_clean.json   – production 4-config set
                                             (core / supply / structured /
                                              occ30d)  [DEFAULT]
  dk_context_testing_manifest_clean.json   – original 6-config test set

Usage:

─── ONE JOB PER CONFIG (--single-variant) ───────────────────────────────────

Test run — 6 configs, 1 job each (6 total), 100 iterations, 1 trial:

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --manifest dk_context_testing_manifest_clean.json \\
        --single-variant \\
        --iterations 100 --trials 1 \\
        --process-queue

Production run — 6 configs, 1 job each (6 total), full iterations/trials:

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default \\
        --manifest dk_context_testing_manifest_clean.json \\
        --single-variant \\
        --process-queue

─── WITH BENCHMARK VARIANTS (default — multiple jobs per config) ────────────

Test run — 6 configs × benchmark variants (dev queue, 100 iterations, 1 trial):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --manifest dk_context_testing_manifest_clean.json \\
        --iterations 100 --trials 1 \\
        --process-queue

Production run — 6 configs × benchmark variants (full iterations/trials):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default \\
        --manifest dk_context_testing_manifest_clean.json \\
        --process-queue

─── OTHER EXAMPLES ──────────────────────────────────────────────────────────

Production run — default 4-config manifest:

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default

Run all 5 configs (4 production + TV config) — test/dev run (100 iter, 1 trial):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --extra-config dk_final_with_tv_config.json \\
        --iterations 100 --trials 1 --process-queue

Dry-run (prints commands and enriched configs without executing):

    python scripts/run_dk_benchmark_all_configs.py \\
        --queue-name default-dev \\
        --manifest dk_context_testing_manifest_clean.json \\
        --single-variant \\
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
DEFAULT_MANIFEST_FILE = (
    DK_CONFIGS_DIR / "dk_context_reduced_manifest_clean.json"
)

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
GCS_BASE = f"gs://{GCS_BUCKET}"
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")

# Fixed benchmark / hyperparameter config paths (relative to repo root)
BENCHMARK_CONFIG = (
    "benchmarks/comprehensive_benchmark_fleet_marketplace_prod.json"
)
# Single-variant config: 1 job per data config (weekly + geometric + 75/90 +
# mixed_by_funnel_clicks). Used with --single-variant to avoid variant explosion.
SINGLE_VARIANT_BENCHMARK_CONFIG = (
    "benchmarks/single_variant_baseline.json"
)
HYPERPARAMETER_RANGES_CONFIG = (
    "benchmarks/generic_hyperparameter_ranges_v2_prod.json"
)
CHANNEL_TYPE_ASSIGNMENTS_CONFIG = (
    "benchmarks/channel_type_assignments_prod.json"
)

# Fields that every selected_columns.json uploaded to GCS must contain.
# benchmark_mmm.py reads all of these from the base config.
REQUIRED_UPLOAD_FIELDS = [
    "country",
    "selected_goal",
    "paid_media_spends",
    "paid_media_vars",
    "var_to_spend_mapping",
    "organic_vars",
    "context_vars",
    "factor_vars",
    "all_selected_drivers",
    "data_version",
    "dep_var",
    "dep_var_type",
    "date_var",
    "timestamp",
]

# Configs to run in recommended order (names without the _clean.json suffix).
# Derived from the manifest's recommended_order; the manifest uses the
# original filenames (without "_clean"), so we map them here.
# New-style manifests (e.g. dk_context_reduced_manifest_clean.json) list
# the _clean.json filenames directly under "files" and need no mapping.
_MANIFEST_TO_CLEAN = {
    "dk_context_minimal.json": "dk_context_minimal_clean.json",
    "dk_context_supply.json": "dk_context_supply_clean.json",
    "dk_context_supply_plus_occ7d.json": "dk_context_supply_plus_occ7d_clean.json",
    "dk_context_occ_current_test.json": "dk_context_occ_current_test_clean.json",
    "dk_context_expanded_test.json": "dk_context_expanded_test_clean.json",
    "dk_context_occ30d_test.json": "dk_context_occ30d_test_clean.json",
    # Reduced config set (new-style; already _clean filenames)
    "dk_context_reduced_core_clean.json": "dk_context_reduced_core_clean.json",
    "dk_context_reduced_supply_clean.json": "dk_context_reduced_supply_clean.json",
    "dk_context_structured_clean.json": "dk_context_structured_clean.json",
    "dk_context_occ30d_reduced_clean.json": "dk_context_occ30d_reduced_clean.json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config enrichment & validation
# ---------------------------------------------------------------------------


def enrich_config_for_upload(config: Dict) -> Dict:
    """
    Return a copy of ``config`` enriched with fields required by
    benchmark_mmm.py that may be absent from the raw local config files.

    Fields added (only when not already present):
    - ``dep_var``      – set to ``selected_goal`` (e.g. "BOOKINGS").  The R
                         script renames the parquet's "UPLOAD_VALUE" column
                         to this name before training, so Robyn sees the real
                         metric name in all output artefacts and plots.
    - ``dep_var_type`` – set to "conversion" (count-based metric; applies to DK BOOKINGS)
    - ``date_var``     – set to "date" (standard date column name in DK data)

    All other existing fields are preserved unchanged.
    """
    enriched = dict(config)

    # dep_var: use the business metric name from selected_goal (e.g. "BOOKINGS").
    # The Streamlit upload pipeline stores the KPI column as "UPLOAD_VALUE" in
    # the mapped parquet, but run_all.R renames it to dep_var before training so
    # Robyn outputs use the real metric name throughout.
    if not enriched.get("dep_var"):
        enriched["dep_var"] = enriched.get("selected_goal", "")

    # dep_var_type: DK BOOKINGS is a conversion (count) metric.
    if not enriched.get("dep_var_type"):
        enriched["dep_var_type"] = "conversion"

    # date_var: name of the date column in the training data.
    if not enriched.get("date_var"):
        enriched["date_var"] = "date"

    return enriched


def validate_config_for_upload(config: Dict) -> List[str]:
    """
    Validate that ``config`` contains all fields required for a
    selected_columns.json upload.

    Returns a list of missing field names (empty list = valid).
    """
    missing = []
    for field in REQUIRED_UPLOAD_FIELDS:
        value = config.get(field)
        # Consider empty string / empty list as missing
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------


def gcs_path_for_config(config: Dict) -> str:
    """
    Build the GCS path where this config should live as selected_columns.json.

    Format:
        gs://<bucket>/training_data/<country>/<goal>/<timestamp>/selected_columns.json
    """
    country = config["country"].lower()
    goal = config["selected_goal"]
    timestamp = config["timestamp"]
    return (
        f"{GCS_BASE}/training_data/{country}/{goal}"
        f"/{timestamp}/selected_columns.json"
    )


def upload_config_to_gcs(
    config: Dict, gcs_path: str, dry_run: bool
) -> bool:
    """
    Enrich, validate, and upload ``config`` to GCS as selected_columns.json.

    Returns True on success (or in dry-run mode), False on error.
    """
    enriched = enrich_config_for_upload(config)

    # Validate before uploading
    missing = validate_config_for_upload(enriched)
    if missing:
        logger.error(
            f"  ❌ Config validation failed — missing fields: {missing}"
        )
        return False

    if dry_run:
        logger.info(f"  [dry-run] Would upload to: {gcs_path}")
        logger.info(
            f"  [dry-run] Enriched fields: "
            f"dep_var={enriched['dep_var']!r}, "
            f"dep_var_type={enriched['dep_var_type']!r}, "
            f"date_var={enriched['date_var']!r}"
        )
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
            json.dump(enriched, tmp, indent=2)
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


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_benchmark(
    gcs_path: str,
    queue_name: str,
    dry_run: bool,
    iterations: Optional[int] = None,
    trials: Optional[int] = None,
    benchmark_id: Optional[str] = None,
    variant_prefix: Optional[str] = None,
    single_variant: bool = False,
) -> bool:
    """
    Invoke run_full_benchmark.py for a single config GCS path.

    Always passes --skip-queue and --skip-analysis so that queue processing
    and result collection happen once (globally) after all configs are
    submitted, rather than per-config.

    When ``single_variant=True`` the ``single_variant_baseline.json`` benchmark
    config is used instead of the full grid config, resulting in exactly 1 job
    per data config (weekly + geometric + 75/90 + mixed_by_funnel_clicks).

    Returns True on success (returncode == 0), False otherwise.
    """
    benchmark_config = (
        SINGLE_VARIANT_BENCHMARK_CONFIG if single_variant else BENCHMARK_CONFIG
    )
    cmd = [
        sys.executable,
        "scripts/run_full_benchmark.py",
        "--path",
        gcs_path,
        "--config",
        benchmark_config,
        "--full-run",
        "--hyperparameter-ranges-config",
        HYPERPARAMETER_RANGES_CONFIG,
        "--channel-type-assignments-config",
        CHANNEL_TYPE_ASSIGNMENTS_CONFIG,
        "--queue-name",
        queue_name,
        # Always skip per-config queue processing and analysis; these run
        # once globally after all configs are submitted.
        "--skip-queue",
        "--skip-analysis",
    ]
    if iterations is not None:
        cmd += ["--iterations", str(iterations)]
    if trials is not None:
        cmd += ["--trials", str(trials)]
    if benchmark_id:
        cmd += ["--benchmark-id", benchmark_id]
    if variant_prefix:
        cmd += ["--variant-prefix", variant_prefix]

    logger.info(f"  Running: {' '.join(cmd)}")

    if dry_run:
        logger.info("  [dry-run] Command not executed.")
        return True

    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        logger.error(
            f"  ❌ run_full_benchmark.py exited with code "
            f"{result.returncode}"
        )
        return False
    return True


def run_analysis(
    benchmark_id: str, queue_name: str, dry_run: bool
) -> None:
    """
    Run analyze_benchmark_results.py with --scan-gcs to collect ALL variant
    results from benchmarks/{benchmark_id}/{variant}/model_summary.json.
    Generates the aggregated results_*.csv visible on the Benchmark page.
    """
    cmd = [
        sys.executable,
        "scripts/analyze_benchmark_results.py",
        "--benchmark-id",
        benchmark_id,
        "--queue-name",
        queue_name,
        "--scan-gcs",
        "--min-r2",
        "0",
    ]
    logger.info(f"\n{'='*80}")
    logger.info("COLLECTING RESULTS (building combined CSV for Benchmark page) …")
    logger.info(f"{'='*80}")
    logger.info(f"Running: {' '.join(cmd)}")
    if dry_run:
        logger.info("[dry-run] Command not executed.")
        return
    subprocess.run(cmd, cwd=str(REPO_ROOT))


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
# Manifest helpers
# ---------------------------------------------------------------------------


def load_manifest(manifest_file: Optional[Path] = None) -> Dict:
    """Load a DK testing manifest.

    ``manifest_file`` defaults to ``DEFAULT_MANIFEST_FILE`` when omitted.
    """
    path = manifest_file or DEFAULT_MANIFEST_FILE
    with open(path) as f:
        return json.load(f)


def ordered_config_files(
    manifest_file: Optional[Path] = None,
) -> List[str]:
    """Return clean config filenames in the manifest's recommended order.

    Supports two manifest formats:

    - **Old style** (``dk_context_testing_manifest_clean.json``): uses
      ``recommended_order`` / ``test_variants`` containing bare filenames
      (e.g. ``"dk_context_minimal.json"``) that are mapped to their
      ``_clean`` equivalents via ``_MANIFEST_TO_CLEAN``.
    - **New style** (``dk_context_reduced_manifest_clean.json``): uses
      ``"files"`` listing the ``_clean.json`` filenames directly.
    """
    manifest = load_manifest(manifest_file)
    ordered = []

    # New-style manifest: filenames already in "files"
    if "files" in manifest:
        for name in manifest["files"]:
            clean = _MANIFEST_TO_CLEAN.get(name, name)
            ordered.append(clean)
        return ordered

    # Old-style manifest: "recommended_order" with bare filenames
    for name in manifest.get(
        "recommended_order", manifest.get("test_variants", [])
    ):
        clean = _MANIFEST_TO_CLEAN.get(name)
        if clean:
            ordered.append(clean)
    return ordered


def load_config(filename: str) -> Dict:
    """Load a JSON config from the DK configs directory."""
    with open(DK_CONFIGS_DIR / filename) as f:
        return json.load(f)


def load_config_from_path(path: Path) -> Dict:
    """Load a JSON config from an arbitrary filesystem path."""
    with open(path) as f:
        return json.load(f)


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
  # Production run — all 4 manifest configs, upload, submit and process queue:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default --process-queue

  # 6 configs, 1 job each — test run (100 iterations, 1 trial):
  python scripts/run_dk_benchmark_all_configs.py \\
      --queue-name default-dev \\
      --manifest dk_context_testing_manifest_clean.json \\
      --single-variant \\
      --iterations 100 --trials 1 --process-queue

  # 6 configs, 1 job each — full production run:
  python scripts/run_dk_benchmark_all_configs.py \\
      --queue-name default \\
      --manifest dk_context_testing_manifest_clean.json \\
      --single-variant \\
      --process-queue

  # 6 configs × benchmark variants — test run (100 iterations, 1 trial):
  python scripts/run_dk_benchmark_all_configs.py \\
      --queue-name default-dev \\
      --manifest dk_context_testing_manifest_clean.json \\
      --iterations 100 --trials 1 --process-queue

  # 6 configs × benchmark variants — full production run:
  python scripts/run_dk_benchmark_all_configs.py \\
      --queue-name default \\
      --manifest dk_context_testing_manifest_clean.json \\
      --process-queue

  # All 5 configs (4 manifest + TV) — dev/test run (100 iterations, 1 trial):
  python scripts/run_dk_benchmark_all_configs.py \\
      --queue-name default-dev \\
      --extra-config dk_final_with_tv_config.json \\
      --iterations 100 --trials 1 --process-queue

  # Dry-run — print commands without executing:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default --dry-run

  # Skip GCS upload (configs already uploaded):
  python scripts/run_dk_benchmark_all_configs.py --queue-name default --skip-upload

  # Run a single config:
  python scripts/run_dk_benchmark_all_configs.py --queue-name default --only dk_context_reduced_core
        """,
    )

    parser.add_argument(
        "--manifest",
        metavar="FILENAME",
        default=None,
        help=(
            "Manifest filename (relative to benchmark_analysis/"
            "dk_json_configs_clean/) that lists the configs to run. "
            "Defaults to dk_context_reduced_manifest_clean.json "
            "(4 production configs). "
            "Use 'dk_context_testing_manifest_clean.json' for the "
            "original 6-config test set."
        ),
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
            "them. Useful for verifying paths and enriched config fields "
            "before a live run."
        ),
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help=(
            "Skip uploading configs to GCS. Use when the "
            "selected_columns.json files already exist at the expected "
            "GCS paths."
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
            "Run a single config by its short name, e.g. "
            "'dk_context_minimal' or 'dk_context_supply_plus_occ7d'. "
            "When omitted all configs in the manifest's "
            "recommended_order are processed."
        ),
    )

    parser.add_argument(
        "--single-variant",
        dest="single_variant",
        action="store_true",
        help=(
            "Submit exactly 1 job per data config instead of expanding the "
            "full benchmark variant grid. Uses the single_variant_baseline "
            "config (weekly, geometric, 75/90 split, mixed_by_funnel_clicks). "
            "Results in N_configs jobs total rather than N_configs × variants. "
            "Useful for a quick comparison of data configs before committing "
            "to a full benchmark sweep."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=(
            "Override the number of Robyn iterations for every variant "
            "(overrides the --full-run default of 5000). "
            "E.g. --iterations 100 for a quick smoke-test."
        ),
    )

    parser.add_argument(
        "--trials",
        type=int,
        default=None,
        help=(
            "Override the number of Robyn trials for every variant "
            "(overrides the --full-run default of 5). "
            "E.g. --trials 1 for a quick smoke-test."
        ),
    )

    parser.add_argument(
        "--benchmark-id",
        dest="benchmark_id",
        default=None,
        help=(
            "Use this value as the shared benchmark ID for all configs in "
            "this run. When omitted a new ID is generated at start-up. "
            "Pass an existing ID to add more configs to a previous run."
        ),
    )

    parser.add_argument(
        "--extra-config",
        dest="extra_configs",
        metavar="FILENAME",
        action="append",
        default=[],
        help=(
            "Additional JSON config file to include alongside the manifest "
            "configs. Can be a bare filename (looked up in "
            "benchmark_analysis/dk_json_configs_clean/) or an absolute path. "
            "May be specified multiple times, e.g. "
            "--extra-config dk_final_with_tv_config.json "
            "--extra-config my_other_config.json"
        ),
    )

    args = parser.parse_args()

    # ── Resolve manifest path ─────────────────────────────────────────────────
    manifest_path: Optional[Path] = None
    if args.manifest:
        candidate = Path(args.manifest)
        # If the user supplied an absolute path or a path that resolves from
        # cwd, use it as-is; otherwise treat it as a bare filename and look
        # it up inside DK_CONFIGS_DIR.
        if candidate.exists():
            manifest_path = candidate.resolve()
        else:
            manifest_path = DK_CONFIGS_DIR / candidate.name
        if not manifest_path.exists():
            logger.error(
                f"Manifest file not found: {manifest_path}. "
                f"Available manifests: "
                + ", ".join(
                    p.name
                    for p in DK_CONFIGS_DIR.glob("*manifest*.json")
                )
            )
            sys.exit(1)

    # ── Shared benchmark ID ───────────────────────────────────────────────────
    # All configs submitted by this invocation share ONE benchmark_id so they
    # appear as a single entry on the Benchmark Results page.
    # Variant names are prefixed with the config's short name so GCS paths
    # remain unique: benchmarks/{benchmark_id}/{config_name}_{variant}/
    from datetime import datetime, timezone

    shared_benchmark_id = (
        args.benchmark_id
        if args.benchmark_id
        else f"dk_benchmark_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )

    # Resolve the list of config files to process
    effective_manifest = manifest_path or DEFAULT_MANIFEST_FILE
    all_configs = ordered_config_files(manifest_path)
    if not all_configs:
        logger.error(
            "No configs found. Check that the manifest exists at "
            f"{effective_manifest}"
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

    # ── Resolve extra configs ─────────────────────────────────────────────────
    # Each extra config is loaded from either the DK configs dir (bare
    # filename) or from an absolute/relative path given directly by the user.
    # They are appended to config_files as (config_dict, label) tuples so the
    # main loop can distinguish them from manifest filenames.
    extra_config_entries: List[tuple] = []
    for raw in args.extra_configs:
        candidate = Path(raw)
        # Resolve: prefer absolute/cwd-relative path; fall back to configs dir
        if candidate.exists():
            config_path = candidate.resolve()
        else:
            config_path = DK_CONFIGS_DIR / candidate.name
        if not config_path.exists():
            logger.error(
                f"Extra config not found: {raw!r}. "
                f"Looked in: {DK_CONFIGS_DIR}"
            )
            sys.exit(1)
        extra_cfg = load_config_from_path(config_path)
        label = extra_cfg.get("name") or config_path.stem
        extra_config_entries.append((extra_cfg, label, config_path.name))
        logger.info(f"Extra config    : {config_path.name} ({label})")

    total_configs = len(config_files) + len(extra_config_entries)

    # Print header
    logger.info("=" * 80)
    logger.info("DK COMPREHENSIVE BENCHMARK TEST")
    logger.info("=" * 80)
    logger.info(f"Manifest        : {effective_manifest.name}")
    logger.info(
        f"Configs to test : {total_configs}"
        + (
            f" ({len(config_files)} manifest + "
            f"{len(extra_config_entries)} extra)"
            if extra_config_entries
            else ""
        )
    )
    logger.info(f"Queue           : {args.queue_name}")
    logger.info(f"Benchmark config: {BENCHMARK_CONFIG}")
    logger.info(f"HP ranges config: {HYPERPARAMETER_RANGES_CONFIG}")
    logger.info(f"Channel types   : {CHANNEL_TYPE_ASSIGNMENTS_CONFIG}")
    logger.info(f"Shared benchmark: {shared_benchmark_id}")
    logger.info(f"Skip upload     : {args.skip_upload}")
    logger.info(f"Process queue   : {args.process_queue}")
    logger.info(f"Single variant  : {args.single_variant}")
    logger.info(f"Dry run         : {args.dry_run}")
    if args.iterations is not None:
        logger.info(f"Iterations      : {args.iterations} (override)")
    if args.trials is not None:
        logger.info(f"Trials          : {args.trials} (override)")
    logger.info("=" * 80)
    logger.info("")

    failed: List[str] = []

    # ── Process manifest configs ──────────────────────────────────────────────
    for idx, filename in enumerate(config_files, start=1):
        config = load_config(filename)
        gcs_path = gcs_path_for_config(config)

        config_name = (
            config.get("name") or filename.replace("_clean.json", "")
        )
        description = config.get("description", "")

        logger.info(f"[{idx}/{total_configs}] {config_name}")
        logger.info(f"  Description    : {description}")
        logger.info(f"  GCS path       : {gcs_path}")
        logger.info(f"  Variant prefix : {config_name}")

        # Step 1: Upload enriched config to GCS
        if not args.skip_upload:
            ok = upload_config_to_gcs(
                config, gcs_path, dry_run=args.dry_run
            )
            if not ok:
                logger.error(
                    f"  Skipping benchmark for {filename} due to "
                    f"upload failure."
                )
                failed.append(filename)
                continue

        # Step 2: Submit benchmark variants to queue.
        # Queue processing and analysis happen once globally after all configs.
        ok = run_benchmark(
            gcs_path=gcs_path,
            queue_name=args.queue_name,
            dry_run=args.dry_run,
            iterations=args.iterations,
            trials=args.trials,
            benchmark_id=shared_benchmark_id,
            variant_prefix=config_name,
            single_variant=args.single_variant,
        )
        if not ok:
            failed.append(filename)

        logger.info("")

    # ── Process extra configs ─────────────────────────────────────────────────
    for extra_idx, (config, config_name, src_name) in enumerate(
        extra_config_entries, start=len(config_files) + 1
    ):
        gcs_path = gcs_path_for_config(config)
        description = config.get("description", "")

        logger.info(f"[{extra_idx}/{total_configs}] {config_name} (extra)")
        logger.info(f"  Source         : {src_name}")
        logger.info(f"  Description    : {description}")
        logger.info(f"  GCS path       : {gcs_path}")
        logger.info(f"  Variant prefix : {config_name}")

        if not args.skip_upload:
            ok = upload_config_to_gcs(
                config, gcs_path, dry_run=args.dry_run
            )
            if not ok:
                logger.error(
                    f"  Skipping benchmark for {src_name} due to "
                    f"upload failure."
                )
                failed.append(src_name)
                continue

        ok = run_benchmark(
            gcs_path=gcs_path,
            queue_name=args.queue_name,
            dry_run=args.dry_run,
            iterations=args.iterations,
            trials=args.trials,
            benchmark_id=shared_benchmark_id,
            variant_prefix=config_name,
            single_variant=args.single_variant,
        )
        if not ok:
            failed.append(src_name)

        logger.info("")

    # Step 3: Process queue (if requested)
    if args.process_queue:
        process_queue(args.queue_name, dry_run=args.dry_run)
        # Step 4: Collect all results into one CSV for the Benchmark Results page.
        # Uses --scan-gcs to scan benchmarks/{id}/{variant}/model_summary.json
        # directly, so results from ALL configs are included regardless of which
        # config last wrote plan.json.
        run_analysis(
            shared_benchmark_id, args.queue_name, dry_run=args.dry_run
        )

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
        n = total_configs
        logger.info(f"✅ All {n} config(s) submitted successfully.")
        logger.info(f"📌 Shared benchmark ID : {shared_benchmark_id}")
        logger.info(
            f"   GCS location       : "
            f"gs://{GCS_BUCKET}/benchmarks/{shared_benchmark_id}/"
        )
        if not args.process_queue:
            logger.info("")
            logger.info(
                "📋 Next steps: process the queue, then collect results:"
            )
            logger.info(
                f"   python scripts/process_queue_simple.py "
                f"--loop --cleanup --queue-name {args.queue_name}"
            )
            logger.info(
                f"   python scripts/analyze_benchmark_results.py "
                f"--benchmark-id {shared_benchmark_id} "
                f"--queue-name {args.queue_name} --scan-gcs"
            )
        logger.info("")
        logger.info(
            "📊 Results will appear on the Benchmark Results page under "
            f"benchmark ID '{shared_benchmark_id}' once jobs complete."
        )
        logger.info("=" * 80)


if __name__ == "__main__":
    main()
