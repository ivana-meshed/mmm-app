#!/usr/bin/env python3
"""
Benchmark Results Analysis Script

Collects results from a benchmark run, exports to CSV, and generates
analysis plots to help identify optimal MMM configurations.

Usage:
    python scripts/analyze_benchmark_results.py --benchmark-id <benchmark_id>
    python scripts/analyze_benchmark_results.py --benchmark-id <benchmark_id> --output-dir ./results
    python scripts/analyze_benchmark_results.py --benchmark-id <benchmark_id> --format png

Features:
- Collects all results from benchmark run
- Exports to CSV
- Generates comparison plots:
  - R² comparison by variant
  - NRMSE comparison by variant
  - Decomposition RSSD comparison
  - Train/val/test gap analysis
  - Metric correlations
- Saves plots to GCS and optionally local directory
"""

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend for headless environments
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns

    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib/seaborn not available. Install with:")
    print("  pip install matplotlib seaborn")

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


class BenchmarkAnalyzer:
    """Analyzes benchmark results and generates plots."""

    def __init__(self, bucket_name: str = GCS_BUCKET):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def load_queue_entries(self, queue_name: str = "default-dev") -> List[Dict]:
        """Load queue entries to find actual job execution timestamps."""
        try:
            queue_path = f"robyn-queues/{queue_name}/queue.json"
            blob = self.bucket.blob(queue_path)
            if blob.exists():
                queue_data = json.loads(blob.download_as_bytes())
                entries = queue_data.get("entries", [])
                logger.debug(
                    f"Loaded {len(entries)} queue entries from {queue_name}"
                )
                return entries
        except Exception as e:
            logger.warning(f"Could not load queue {queue_name}: {e}")
        return []

    def map_variants_to_timestamps(
        self,
        variants: List[Dict],
        queue_entries: List[Dict],
        benchmark_id: str = "",
    ) -> Dict[str, str]:
        """
        Map variant names to their actual result timestamps from queue.

        Checks (in priority order):
        1. ``timestamp`` field set directly by process_queue_simple.py
        2. ``expected_result_path`` (parse last path segment)
        3. ``gcs_prefix`` (legacy field, for backward compatibility)

        Accepts both COMPLETED and RUNNING entries so results can be read
        while jobs are still in progress (useful for partial analysis).
        Variants whose entry has none of these path fields are not mapped and
        will fall back to recency-based search in ``_collect_variant_result``.

        When ``benchmark_id`` is provided only queue entries whose
        ``params.benchmark_id`` matches are considered.  Without this guard
        the lookup can return a stale timestamp from a *previous* benchmark
        run for the same variant name, causing the exact-path lookup to fail
        (benchmark jobs write to ``benchmarks/{id}/{variant}/`` not to
        ``robyn/{revision}/{country}/{timestamp}/``).
        """
        timestamp_map = {}

        for variant in variants:
            variant_name = variant.get("benchmark_variant", "")
            if not variant_name:
                continue

            # Find matching completed queue entry
            for entry in queue_entries:
                params = entry.get("params", {})
                entry_variant = params.get("benchmark_variant", "")

                # Skip entries from other benchmark runs to avoid using stale
                # timestamps from previous runs of the same variant name.
                if benchmark_id:
                    entry_benchmark_id = params.get("benchmark_id", "")
                    if entry_benchmark_id != benchmark_id:
                        continue

                if entry_variant == variant_name and entry.get("status") in (
                    "COMPLETED",
                    "RUNNING",
                ):
                    timestamp = None

                    # 1. Direct timestamp field (set by process_queue_simple.py)
                    ts = entry.get("timestamp", "")
                    if ts:
                        timestamp = ts

                    # 2. Parse from expected_result_path
                    if not timestamp:
                        erp = entry.get("expected_result_path", "")
                        if erp:
                            parts = erp.rstrip("/").split("/")
                            if parts:
                                timestamp = parts[-1]

                    # 3. Legacy gcs_prefix field
                    if not timestamp:
                        gcs_prefix = entry.get("gcs_prefix", "")
                        if gcs_prefix:
                            parts = gcs_prefix.rstrip("/").split("/")
                            if len(parts) >= 5:
                                timestamp = parts[-1]

                    if timestamp:
                        timestamp_map[variant_name] = timestamp
                        logger.debug(f"Mapped {variant_name} -> {timestamp}")
                        break

        logger.info(f"Mapped {len(timestamp_map)} variants to timestamps")
        return timestamp_map

    def collect_results(
        self, benchmark_id: str, queue_name: str = "default-dev"
    ) -> Optional[pd.DataFrame]:
        """
        Collect results from a benchmark run.

        Returns DataFrame with all benchmark results.
        """
        logger.info(f"Collecting results for benchmark: {benchmark_id}")

        # Load benchmark plan
        plan_path = f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json"
        try:
            blob = self.bucket.blob(plan_path)
            if not blob.exists():
                logger.error(f"Benchmark plan not found: {plan_path}")
                return None

            plan = json.loads(blob.download_as_bytes())
            variants = plan.get("variants", [])
            logger.info(f"Found {len(variants)} variants in benchmark plan")
        except Exception as e:
            logger.error(f"Error loading benchmark plan: {e}")
            return None

        # Load queue entries to find actual timestamps
        queue_entries = self.load_queue_entries(queue_name)
        timestamp_map = self.map_variants_to_timestamps(
            variants, queue_entries, benchmark_id=benchmark_id
        )

        # Collect results for each variant
        results = []
        for variant in variants:
            result = self._collect_variant_result(
                variant, benchmark_id, timestamp_map
            )
            if result:
                results.append(result)

        if not results:
            logger.warning("No results collected")
            return None

        df = pd.DataFrame(results)
        logger.info(f"Collected {len(df)} results")
        return df

    def collect_results_from_gcs_scan(
        self, benchmark_id: str
    ) -> Optional[pd.DataFrame]:
        """
        Collect results by scanning GCS directly.

        Reads every ``benchmarks/{benchmark_id}/{variant}/model_summary.json``
        without requiring a plan.json or queue entries.  This is the primary
        collection strategy for multi-config runs where plan.json would only
        contain the last config's variants.

        Returns a DataFrame or None when no results are found.
        """
        prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
        logger.info(f"Scanning GCS for results under: gs://{self.bucket_name}/{prefix}")

        try:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
        except Exception as e:
            logger.error(f"Failed to list GCS blobs: {e}")
            return None

        # Only depth-4 paths: benchmarks/{id}/{variant}/model_summary.json
        summary_blobs = [
            b for b in blobs
            if b.name.endswith("/model_summary.json")
            and len(b.name.split("/")) == 4
        ]

        if not summary_blobs:
            logger.warning(f"No model_summary.json files found under {prefix}")
            return None

        logger.info(f"Found {len(summary_blobs)} model_summary.json file(s)")

        rows = []
        for blob in summary_blobs:
            variant_name = blob.name.split("/")[2]
            try:
                summary = json.loads(blob.download_as_bytes())
            except Exception as e:
                logger.warning(f"Could not read {blob.name}: {e}")
                continue

            best_model = summary.get("best_model", {})
            decomp = summary.get("decomp_contribution", {}) or {}
            input_meta = summary.get("input_metadata", {}) or {}
            channel_roas = decomp.get("channel_roas") or {}
            channel_cpa = decomp.get("channel_cpa") or {}

            # Prefer fields from summary; fall back to input_metadata
            adstock = summary.get("adstock") or input_meta.get("adstock", "")
            train_size = summary.get("train_size") or input_meta.get("train_size", "")
            resample_freq = summary.get("resample_freq") or input_meta.get("resample_freq", "none")

            iterations_raw = summary.get("iterations") or input_meta.get("iterations")
            trials_raw = summary.get("trials") or input_meta.get("trials")
            try:
                iterations = int(iterations_raw) if iterations_raw else None
            except (ValueError, TypeError):
                iterations = None
            try:
                trials = int(trials_raw) if trials_raw else None
            except (ValueError, TypeError):
                trials = None

            row = {
                "benchmark_test": summary.get("benchmark_test", ""),
                "benchmark_variant": variant_name,
                "country": summary.get("country") or input_meta.get("country", ""),
                "revision": summary.get("revision", "default"),
                "preset_label": summary.get("preset_label", ""),
                "window_label": summary.get("window_label", ""),
                "adstock": adstock,
                "train_size": str(train_size),
                "iterations": iterations,
                "trials": trials,
                "resample_freq": resample_freq,
                "rsq_train": best_model.get("rsq_train"),
                "rsq_val": best_model.get("rsq_val"),
                "rsq_test": best_model.get("rsq_test"),
                "nrmse": best_model.get("nrmse"),
                "nrmse_train": best_model.get("nrmse_train"),
                "nrmse_val": best_model.get("nrmse_val"),
                "nrmse_test": best_model.get("nrmse_test"),
                "decomp_rssd": best_model.get("decomp_rssd"),
                "mape": best_model.get("mape"),
                "paid_media_share": decomp.get("paid_media_share"),
                "baseline_share": decomp.get("baseline_share"),
                "organic_share": decomp.get("organic_share"),
                "context_share": decomp.get("context_share"),
                "allocator_stability_roas_cv": decomp.get("allocator_stability_roas_cv"),
                "channel_roas_json": json.dumps(channel_roas) if channel_roas else "",
                "channel_cpa_json": json.dumps(channel_cpa) if channel_cpa else "",
                "model_id": (
                    f"{variant_name}_{summary.get('timestamp', '')}"
                    if summary.get("timestamp")
                    else variant_name
                ),
                "timestamp": summary.get("timestamp", ""),
            }
            rows.append(row)
            logger.debug(f"  ✓ {variant_name}")

        if not rows:
            logger.warning("Could not parse any model_summary.json files")
            return None

        df = pd.DataFrame(rows)
        logger.info(f"Collected {len(df)} result(s) via GCS scan")
        return df

    def _collect_variant_result(
        self,
        variant: Dict[str, Any],
        benchmark_id: str,
        timestamp_map: Dict[str, str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Collect result for a single variant."""
        country = variant.get("country", "")
        revision = variant.get("revision", "default")
        variant_name = variant.get("benchmark_variant", "")

        logger.info(f"Collecting result for variant: {variant_name}")
        logger.debug(
            f"  Variant config: adstock={variant.get('adstock')}, train_size={variant.get('train_size')}"
        )

        # PRIMARY: Check benchmark-specific path first.
        # When a job is submitted with benchmark_id + benchmark_variant, the R
        # training script stores results at benchmarks/{benchmark_id}/{variant}/
        # (see r/run_all.R). This is the canonical location for benchmark jobs.
        if benchmark_id and variant_name:
            benchmark_path = (
                f"benchmarks/{benchmark_id}/{variant_name}/model_summary.json"
            )
            logger.info(f"  Trying benchmark path: {benchmark_path}")
            try:
                blob = self.bucket.blob(benchmark_path)
                if blob.exists():
                    summary = json.loads(blob.download_as_bytes())
                    logger.info(f"  ✓ Found result at benchmark path")
                    return self._extract_metrics(summary, variant)
                else:
                    logger.debug(f"  ✗ Benchmark path not found: {benchmark_path}")
            except Exception as e:
                logger.error(
                    f"  ✗ Error loading benchmark path {benchmark_path}: {e}"
                )

        # Get timestamp from map (actual execution timestamp)
        timestamp = None
        if timestamp_map:
            timestamp = timestamp_map.get(variant_name)
            if timestamp:
                logger.info(f"  Using timestamp from queue: {timestamp}")
            else:
                logger.warning(
                    f"  No timestamp found in map for {variant_name}"
                )

        # If we have timestamp, use exact path
        if timestamp:
            exact_path = (
                f"robyn/{revision}/{country}/{timestamp}/model_summary.json"
            )
            logger.info(f"  Trying exact path: {exact_path}")
            try:
                blob = self.bucket.blob(exact_path)
                if blob.exists():
                    summary = json.loads(blob.download_as_bytes())
                    logger.info(f"  ✓ Found result at exact path")
                    logger.debug(
                        f"  Summary has: adstock={summary.get('adstock')}, train_size={summary.get('train_size')}"
                    )
                    return self._extract_metrics(summary, variant)
                else:
                    logger.warning(f"  ✗ Exact path not found: {exact_path}")
            except Exception as e:
                logger.error(f"  ✗ Error loading exact path {exact_path}: {e}")

        # Last resort: Search for model_summary.json in legacy location
        prefix = f"robyn/{revision}/{country}/"
        logger.info(f"  Falling back to search in: {prefix}")

        try:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            summaries = [b for b in blobs if "model_summary.json" in b.name]
            logger.info(f"  Found {len(summaries)} model_summary.json files")

            # Find most recent matching summary
            for i, blob in enumerate(
                sorted(summaries, key=lambda b: b.time_created, reverse=True)
            ):
                try:
                    logger.debug(
                        f"  Checking blob {i+1}/{len(summaries)}: {blob.name}"
                    )
                    summary = json.loads(blob.download_as_bytes())
                    if self._matches_variant(summary, variant):
                        logger.info(
                            f"  ✓ Found matching result via fallback at {blob.name}"
                        )
                        return self._extract_metrics(summary, variant)
                    else:
                        logger.debug(f"    Doesn't match variant config")
                except Exception as e:
                    logger.debug(f"    Error loading {blob.name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"  ✗ Error searching for results: {e}")

        # Before giving up, check for failure artifacts written by the R panic
        # trap (panic_error.json / status.json).  These are uploaded even when
        # the training job crashes, so their presence tells us *why* the job
        # failed.
        if benchmark_id and variant_name:
            self._log_failure_diagnostics(benchmark_id, variant_name)

        logger.error(f"❌ NO RESULTS FOUND for variant: {variant_name}")
        return None

    def _log_failure_diagnostics(
        self, benchmark_id: str, variant_name: str
    ) -> None:
        """Log failure diagnostics from panic_error.json / status.json.

        The R training script uploads these artifacts to
        ``benchmarks/{id}/{variant}/`` whenever the job exits abnormally
        (via the panic trap installed in run_all.R).  Reading them here
        surfaces the root-cause error message without requiring access to
        Cloud Run logs.
        """
        prefix = f"benchmarks/{benchmark_id}/{variant_name}"

        for artifact in ("panic_error.json", "status.json"):
            blob_path = f"{prefix}/{artifact}"
            try:
                blob = self.bucket.blob(blob_path)
                if not blob.exists():
                    continue
                payload = json.loads(blob.download_as_bytes())
                state = payload.get("state", "")
                if artifact == "panic_error.json":
                    msg = payload.get("message", "")
                    step = payload.get("step", "")
                    logger.warning(
                        f"  💥 {variant_name} crashed"
                        + (f" at step '{step}'" if step else "")
                        + (f": {msg}" if msg else "")
                    )
                    return  # panic_error.json is more informative; stop here
                elif state and state != "RUNNING":
                    # status.json present but no panic_error.json means the job
                    # completed without writing a panic payload (e.g. an early
                    # crash before the panic trap was installed, or a successful
                    # run that simply produced no model_summary.json).
                    logger.warning(
                        f"  ⚠️  {variant_name} status.json shows state={state!r}"
                        " (no panic_error.json found)"
                    )
                    return
                elif state == "RUNNING":
                    logger.warning(
                        f"  ⚠️  {variant_name} status.json still shows"
                        " state='RUNNING' — job may have been killed without"
                        " writing failure artifacts"
                    )
                    return
            except Exception as exc:
                logger.debug(
                    f"  Could not read {blob_path}: {exc}"
                )

    def _matches_variant(
        self, summary: Dict[str, Any], variant: Dict[str, Any]
    ) -> bool:
        """Check if summary matches variant configuration."""
        # Match on country
        if (
            summary.get("country", "").lower()
            != variant.get("country", "").lower()
        ):
            return False

        # Match on adstock if available
        variant_adstock = variant.get("adstock", "")
        summary_adstock = summary.get("adstock", "")
        if variant_adstock and summary_adstock:
            if variant_adstock.lower() != summary_adstock.lower():
                return False

        # Match on train_size if available
        variant_train = variant.get("train_size")
        summary_train = summary.get("train_size")
        if variant_train and summary_train:
            # Convert to float for comparison
            try:
                if abs(float(variant_train) - float(summary_train)) > 0.01:
                    return False
            except (ValueError, TypeError):
                pass

        # Match on iterations if available
        variant_iter = variant.get("iterations")
        summary_iter = summary.get("iterations")
        if variant_iter and summary_iter:
            if int(variant_iter) != int(summary_iter):
                return False

        # Match on resample_freq (weekly vs daily) when present in summary
        variant_freq = variant.get("resample_freq", "")
        summary_freq = summary.get("resample_freq", "")
        if variant_freq and summary_freq:
            # Normalise: treat empty / "none" / None as equivalent
            def _norm_freq(f: str) -> str:
                return (f or "none").strip().lower()

            if _norm_freq(variant_freq) != _norm_freq(summary_freq):
                return False

        # Match on paid_media_vars (distinguishes spend_to_clicks vs
        # mixed_by_funnel etc.) when available in both plan and summary
        variant_pmv = variant.get("paid_media_vars") or []
        summary_pmv = (
            (summary.get("input_metadata") or {}).get("paid_media_vars") or []
        )
        if variant_pmv and summary_pmv:
            if sorted(variant_pmv) != sorted(summary_pmv):
                return False

        return True

    def _extract_metrics(
        self, summary: Dict[str, Any], variant: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract metrics from model summary."""
        best_model = summary.get("best_model", {})

        # Extract config from summary first, fall back to variant
        # This is crucial because model_summary.json has the actual config used
        adstock = summary.get("adstock") or variant.get("adstock", "")
        train_size = summary.get("train_size") or variant.get("train_size", "")

        # Convert iterations and trials to int (they should be numeric)
        iterations_raw = summary.get("iterations") or variant.get(
            "iterations", 0
        )
        trials_raw = summary.get("trials") or variant.get("trials", 0)
        try:
            iterations = int(iterations_raw) if iterations_raw else 0
        except (ValueError, TypeError):
            iterations = 0
        try:
            trials = int(trials_raw) if trials_raw else 0
        except (ValueError, TypeError):
            trials = 0

        resample_freq = summary.get("resample_freq") or variant.get(
            "resample_freq", "none"
        )

        # Log what we extracted for debugging
        variant_name = variant.get("benchmark_variant", "unknown")
        logger.debug(f"Extracting metrics for {variant_name}:")
        logger.debug(
            f"  adstock: {adstock} (from {'summary' if summary.get('adstock') else 'variant'})"
        )
        logger.debug(
            f"  train_size: {train_size} (from {'summary' if summary.get('train_size') else 'variant'})"
        )
        logger.debug(
            f"  iterations: {iterations} (from {'summary' if summary.get('iterations') else 'variant'})"
        )
        logger.debug(f"  rsq_val: {best_model.get('rsq_val')}")

        # Decomposition contributions (new fields from extract_model_summary.R)
        decomp = summary.get("decomp_contribution", {}) or {}
        channel_roas = decomp.get("channel_roas") or {}
        channel_cpa = decomp.get("channel_cpa") or {}

        return {
            # Benchmark metadata
            "benchmark_test": variant.get("benchmark_test", ""),
            "benchmark_variant": variant_name,
            "country": variant.get("country", ""),
            "revision": variant.get("revision", "default"),
            # Hyperparameter preset label (empty when no preset sweep)
            "preset_label": variant.get("preset_label", ""),
            # Seasonality window label (empty when no window sweep)
            "window_label": variant.get("window_label", ""),
            # Configuration (from summary primarily)
            "adstock": adstock,
            "train_size": str(train_size),
            "iterations": iterations,  # Keep as int
            "trials": trials,  # Keep as int
            "resample_freq": resample_freq,
            # Model fit metrics
            "rsq_train": best_model.get("rsq_train"),
            "rsq_val": best_model.get("rsq_val"),
            "rsq_test": best_model.get("rsq_test"),
            "nrmse": best_model.get("nrmse"),
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
            # Model metadata — unique per variant × run using variant name +
            # timestamp so the ID is human-readable and never clashes across
            # variants (Robyn's own IDs like 1_2_5 repeat between runs).
            "model_id": (
                f"{variant_name}_{summary.get('timestamp', '')}"
                if summary.get("timestamp")
                else variant_name
            ),
            "timestamp": summary.get("timestamp", ""),
        }

    def export_csv(
        self,
        df: pd.DataFrame,
        benchmark_id: str,
        local_path: Optional[str] = None,
    ) -> str:
        """Export results to CSV."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Export to GCS
        gcs_path = f"{BENCHMARK_ROOT}/{benchmark_id}/results_{timestamp}.csv"
        csv_data = df.to_csv(index=False)
        blob = self.bucket.blob(gcs_path)
        blob.upload_from_string(csv_data, content_type="text/csv")
        logger.info(f"Exported CSV to: gs://{self.bucket_name}/{gcs_path}")

        # Export to local if requested
        if local_path:
            local_file = Path(local_path) / f"results_{timestamp}.csv"
            local_file.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(local_file, index=False)
            logger.info(f"Exported CSV to: {local_file}")

        return gcs_path

    def generate_plots(
        self,
        df: pd.DataFrame,
        benchmark_id: str,
        output_dir: Optional[str] = None,
        format: str = "png",
    ):
        """Generate analysis plots."""
        if not PLOTTING_AVAILABLE:
            logger.error(
                "Matplotlib/seaborn not available. Cannot generate plots."
            )
            return

        logger.info("Generating analysis plots...")

        # Set style
        sns.set_style("whitegrid")
        plt.rcParams["figure.figsize"] = (12, 8)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        plots_dir = f"{BENCHMARK_ROOT}/{benchmark_id}/plots_{timestamp}"

        # Generate each plot
        plots = [
            ("rsq_comparison", self._plot_rsq_comparison),
            ("nrmse_comparison", self._plot_nrmse_comparison),
            ("decomp_rssd", self._plot_decomp_rssd),
            ("train_val_test_gap", self._plot_train_val_test_gap),
            ("metric_correlations", self._plot_metric_correlations),
            ("best_models_summary", self._plot_best_models_summary),
            ("driver_waterfall", self._plot_driver_waterfall),
            ("roas_by_channel", self._plot_roas_by_channel),
            ("cpa_by_channel", self._plot_cpa_by_channel),
        ]

        for plot_name, plot_func in plots:
            try:
                fig = plot_func(df)
                if fig is not None:
                    self._save_plot(
                        fig, plot_name, plots_dir, output_dir, format
                    )
                    plt.close(fig)
            except Exception as e:
                logger.error(
                    f"Error generating {plot_name}: {e}\n"
                    f"{traceback.format_exc()}"
                )
                # Ensure any partially-created figure is closed
                try:
                    plt.close("all")
                except Exception:
                    pass

        logger.info(f"Plots saved to: gs://{self.bucket_name}/{plots_dir}/")
        if output_dir:
            logger.info(f"Plots also saved to: {output_dir}")

    def _save_plot(
        self,
        fig,
        name: str,
        gcs_dir: str,
        local_dir: Optional[str],
        format: str,
    ):
        """Save plot to GCS and optionally local directory."""
        # Save to temporary file first
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=f".{format}", delete=False
        ) as tmp:
            fig.savefig(tmp.name, bbox_inches="tight", dpi=150)
            tmp_path = tmp.name

        try:
            # Upload to GCS
            gcs_path = f"{gcs_dir}/{name}.{format}"
            blob = self.bucket.blob(gcs_path)
            blob.upload_from_filename(tmp_path)

            # Save to local if requested
            if local_dir:
                local_path = Path(local_dir) / f"{name}.{format}"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(local_path, bbox_inches="tight", dpi=150)
        finally:
            # Always remove the temp file, even when the upload fails
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _plot_rsq_comparison(self, df: pd.DataFrame):
        """Plot R² comparison across variants."""
        # Scale width by variants × hue groups so all bars are comfortably
        # visible.  3 hue categories (train/val/test) means each variant
        # occupies roughly 3× the x-space of a single bar.
        n_variants = len(df)
        fig_width = max(14, n_variants * 1.2)
        fig, ax = plt.subplots(figsize=(fig_width, 8))

        # Prepare data
        rsq_cols = ["rsq_train", "rsq_val", "rsq_test"]
        plot_df = df[["benchmark_variant"] + rsq_cols].copy()
        plot_df = plot_df.dropna(subset=rsq_cols, how="all")

        if plot_df.empty:
            logger.warning("No R² data to plot")
            return None

        # Melt for grouped bar plot
        melted = plot_df.melt(
            id_vars=["benchmark_variant"],
            value_vars=rsq_cols,
            var_name="Split",
            value_name="R²",
        )

        # Plot
        sns.barplot(
            data=melted, x="benchmark_variant", y="R²", hue="Split", ax=ax
        )
        ax.set_title(
            "R² Comparison Across Variants", fontsize=16, fontweight="bold"
        )
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("R²", fontsize=12)
        # Dynamic y-axis: include 0 as baseline, show negative values
        y_min = min(0.0, float(melted["R²"].min(skipna=True)) * 1.05)
        y_max = max(1.0, float(melted["R²"].max(skipna=True)) * 1.05)
        ax.set_ylim(y_min, y_max)
        ax.axhline(y=0, color="black", linestyle="--", alpha=0.3, linewidth=0.8)

        # Rotate labels if many variants
        if n_variants > 15:
            plt.xticks(rotation=45, ha="right", fontsize=9)
        else:
            plt.xticks(rotation=45, ha="right")

        ax.legend(title="Data Split")
        ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        return fig

    def _plot_nrmse_comparison(self, df: pd.DataFrame):
        """Plot NRMSE comparison across variants."""
        # Scale width like rsq_comparison: 3 hue groups need more x-space.
        n_variants = len(df)
        fig_width = max(14, n_variants * 1.2)
        fig, ax = plt.subplots(figsize=(fig_width, 8))

        # Prepare data
        nrmse_cols = ["nrmse_train", "nrmse_val", "nrmse_test"]
        plot_df = df[["benchmark_variant"] + nrmse_cols].copy()
        plot_df = plot_df.dropna(subset=nrmse_cols, how="all")

        if plot_df.empty:
            logger.warning("No NRMSE data to plot")
            return None

        # Melt for grouped bar plot
        melted = plot_df.melt(
            id_vars=["benchmark_variant"],
            value_vars=nrmse_cols,
            var_name="Split",
            value_name="NRMSE",
        )

        # Plot
        sns.barplot(
            data=melted, x="benchmark_variant", y="NRMSE", hue="Split", ax=ax
        )
        ax.set_title(
            "NRMSE Comparison Across Variants", fontsize=16, fontweight="bold"
        )
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("NRMSE (lower is better)", fontsize=12)

        # Rotate labels if many variants
        if n_variants > 15:
            plt.xticks(rotation=45, ha="right", fontsize=9)
        else:
            plt.xticks(rotation=45, ha="right")

        ax.legend(title="Data Split")
        ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        return fig

    def _plot_decomp_rssd(self, df: pd.DataFrame):
        """Plot decomposition RSSD comparison."""
        # Adjust figure size based on number of variants
        n_variants = len(df)
        fig_height = max(8, n_variants * 0.3)  # Scale height with variants
        fig, ax = plt.subplots(figsize=(14, fig_height))

        plot_df = df[["benchmark_variant", "decomp_rssd"]].copy()
        plot_df = plot_df.dropna(subset=["decomp_rssd"])

        if plot_df.empty:
            logger.warning("No decomp_rssd data to plot")
            return None

        # Sort by decomp_rssd
        plot_df = plot_df.sort_values("decomp_rssd")

        # Plot
        ax.barh(plot_df["benchmark_variant"], plot_df["decomp_rssd"])
        ax.set_title(
            "Decomposition RSSD by Variant", fontsize=16, fontweight="bold"
        )
        ax.set_xlabel("Decomposition RSSD (lower is better)", fontsize=12)
        ax.set_ylabel("Variant", fontsize=12)

        # Adjust label size for many variants
        if n_variants > 20:
            ax.tick_params(axis="y", labelsize=8)

        ax.grid(axis="x", alpha=0.3)

        fig.tight_layout()
        return fig

    def _plot_train_val_test_gap(self, df: pd.DataFrame):
        """Plot train/val/test performance gap analysis."""
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

        # R² gaps
        if "rsq_train" in df and "rsq_val" in df and "rsq_test" in df:
            plot_df = df[
                ["benchmark_variant", "rsq_train", "rsq_val", "rsq_test"]
            ].copy()
            plot_df = plot_df.dropna()

            if not plot_df.empty:
                plot_df["train_val_gap"] = (
                    plot_df["rsq_train"] - plot_df["rsq_val"]
                )
                plot_df["val_test_gap"] = (
                    plot_df["rsq_val"] - plot_df["rsq_test"]
                )

                ax1.scatter(
                    plot_df["train_val_gap"],
                    plot_df["val_test_gap"],
                    s=100,
                    alpha=0.6,
                )
                for idx, row in plot_df.iterrows():
                    ax1.annotate(
                        row["benchmark_variant"],
                        (row["train_val_gap"], row["val_test_gap"]),
                        fontsize=8,
                        alpha=0.7,
                    )

                ax1.axhline(y=0, color="r", linestyle="--", alpha=0.3)
                ax1.axvline(x=0, color="r", linestyle="--", alpha=0.3)
                ax1.set_xlabel("Train-Val Gap (R²)", fontsize=12)
                ax1.set_ylabel("Val-Test Gap (R²)", fontsize=12)
                ax1.set_title(
                    "R² Performance Gaps", fontsize=14, fontweight="bold"
                )
                ax1.grid(alpha=0.3)

        # NRMSE gaps
        if "nrmse_train" in df and "nrmse_val" in df and "nrmse_test" in df:
            plot_df = df[
                ["benchmark_variant", "nrmse_train", "nrmse_val", "nrmse_test"]
            ].copy()
            plot_df = plot_df.dropna()

            if not plot_df.empty:
                plot_df["train_val_gap"] = (
                    plot_df["nrmse_val"] - plot_df["nrmse_train"]
                )
                plot_df["val_test_gap"] = (
                    plot_df["nrmse_test"] - plot_df["nrmse_val"]
                )

                ax2.scatter(
                    plot_df["train_val_gap"],
                    plot_df["val_test_gap"],
                    s=100,
                    alpha=0.6,
                )
                for idx, row in plot_df.iterrows():
                    ax2.annotate(
                        row["benchmark_variant"],
                        (row["train_val_gap"], row["val_test_gap"]),
                        fontsize=8,
                        alpha=0.7,
                    )

                ax2.axhline(y=0, color="r", linestyle="--", alpha=0.3)
                ax2.axvline(x=0, color="r", linestyle="--", alpha=0.3)
                ax2.set_xlabel("Train-Val Gap (NRMSE)", fontsize=12)
                ax2.set_ylabel("Val-Test Gap (NRMSE)", fontsize=12)
                ax2.set_title(
                    "NRMSE Performance Gaps", fontsize=14, fontweight="bold"
                )
                ax2.grid(alpha=0.3)

        plt.tight_layout()
        return fig

    def _plot_metric_correlations(self, df: pd.DataFrame):
        """Plot correlation matrix of key metrics."""
        fig, ax = plt.subplots(figsize=(12, 10))

        # Select numeric columns
        metric_cols = [
            "rsq_train",
            "rsq_val",
            "rsq_test",
            "nrmse_train",
            "nrmse_val",
            "nrmse_test",
            "decomp_rssd",
            "mape",
        ]
        available_cols = [col for col in metric_cols if col in df.columns]

        if len(available_cols) < 2:
            logger.warning("Not enough metrics for correlation plot")
            return None

        corr = df[available_cols].corr()

        # Check if all correlations are NaN (happens in test runs with no variation)
        if corr.isna().all().all():
            logger.warning(
                "No correlation data - metrics show no variation (test run?)"
            )
            ax.text(
                0.5,
                0.5,
                "[!] No Correlation Data\n\n"
                "Metrics show no variation across variants.\n"
                "This typically happens in test runs with low iterations.\n\n"
                "Consider using --full-run for meaningful comparison.",
                ha="center",
                va="center",
                fontsize=14,
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
            )
            ax.set_title("Metric Correlations", fontsize=16, fontweight="bold")
            ax.set_xticks([])
            ax.set_yticks([])
            return fig

        # Plot
        sns.heatmap(
            corr,
            annot=True,
            fmt=".2f",
            cmap="coolwarm",
            center=0,
            square=True,
            ax=ax,
            cbar_kws={"label": "Correlation"},
        )
        ax.set_title("Metric Correlations", fontsize=16, fontweight="bold")

        fig.tight_layout()
        return fig

    def _plot_best_models_summary(self, df: pd.DataFrame):
        """Plot summary of best models by different criteria."""
        fig, axes = plt.subplots(2, 2, figsize=(16, 14))

        def _has_data(col: str) -> bool:
            return col in df.columns and df[col].notna().any()

        def _no_data_label(ax, title: str, note: str = "") -> None:
            ax.set_title(title, fontsize=14, fontweight="bold")
            ax.text(
                0.5,
                0.5,
                "No data available" + (f"\n({note})" if note else ""),
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=13,
                color="gray",
            )
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis("off")

        # ── Subplot 1: Best by R² ─────────────────────────────────────────
        # Prefer rsq_val; fall back to rsq_train if val metrics are absent.
        ax = axes[0, 0]
        rsq_col = (
            "rsq_val"
            if _has_data("rsq_val")
            else ("rsq_train" if _has_data("rsq_train") else None)
        )
        rsq_label = (
            "R² Validation"
            if rsq_col == "rsq_val"
            else ("R² Train" if rsq_col else "R²")
        )
        if rsq_col:
            top_models = df.nlargest(10, rsq_col)[
                ["benchmark_variant", rsq_col]
            ]
            ax.barh(top_models["benchmark_variant"], top_models[rsq_col])
            ax.set_title(
                f"Top 10 Models by {rsq_label}",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel(rsq_label, fontsize=12)
            ax.tick_params(axis="y", labelsize=9)
            ax.grid(axis="x", alpha=0.3)
        else:
            _no_data_label(ax, "Top 10 Models by R²", "rsq not available")

        # ── Subplot 2: Best by NRMSE ──────────────────────────────────────
        # Prefer nrmse_val; fall back to plain nrmse.
        ax = axes[0, 1]
        nrmse_col = (
            "nrmse_val"
            if _has_data("nrmse_val")
            else ("nrmse" if _has_data("nrmse") else None)
        )
        nrmse_label = (
            "NRMSE Validation"
            if nrmse_col == "nrmse_val"
            else ("NRMSE (overall)" if nrmse_col else "NRMSE")
        )
        if nrmse_col:
            top_models = df.nsmallest(10, nrmse_col)[
                ["benchmark_variant", nrmse_col]
            ]
            ax.barh(top_models["benchmark_variant"], top_models[nrmse_col])
            ax.set_title(
                f"Top 10 Models by {nrmse_label}",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel(nrmse_label, fontsize=12)
            ax.tick_params(axis="y", labelsize=9)
            ax.grid(axis="x", alpha=0.3)
        else:
            _no_data_label(ax, "Top 10 Models by NRMSE", "nrmse not available")

        # ── Subplot 3: Best by Decomp RSSD ────────────────────────────────
        ax = axes[1, 0]
        if _has_data("decomp_rssd"):
            top_models = df.nsmallest(10, "decomp_rssd")[
                ["benchmark_variant", "decomp_rssd"]
            ]
            ax.barh(
                top_models["benchmark_variant"], top_models["decomp_rssd"]
            )
            ax.set_title(
                "Top 10 Models by Decomp RSSD",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel("Decomp RSSD", fontsize=12)
            ax.tick_params(axis="y", labelsize=9)
            ax.grid(axis="x", alpha=0.3)
        else:
            _no_data_label(
                ax, "Top 10 Models by Decomp RSSD", "decomp_rssd not available"
            )

        # ── Subplot 4: Generalization (val-test gap) ──────────────────────
        ax = axes[1, 1]
        if _has_data("rsq_val") and _has_data("rsq_test"):
            df_temp = df.copy()
            df_temp["val_test_gap"] = abs(
                df_temp["rsq_val"] - df_temp["rsq_test"]
            )
            valid = df_temp["val_test_gap"].notna()
            if valid.any():
                top_models = df_temp[valid].nsmallest(10, "val_test_gap")[
                    ["benchmark_variant", "val_test_gap"]
                ]
                ax.barh(
                    top_models["benchmark_variant"],
                    top_models["val_test_gap"],
                )
                ax.set_title(
                    "Top 10 Models by Generalization (Val-Test Gap)",
                    fontsize=14,
                    fontweight="bold",
                )
                ax.set_xlabel("Val-Test Gap (R²)", fontsize=12)
                ax.tick_params(axis="y", labelsize=9)
                ax.grid(axis="x", alpha=0.3)
            else:
                _no_data_label(
                    ax,
                    "Top 10 Models by Generalization",
                    "val/test R² not available",
                )
        else:
            _no_data_label(
                ax,
                "Top 10 Models by Generalization",
                "val/test R² not available",
            )

        try:
            plt.tight_layout(pad=2.0)  # More padding for readability
        except Exception as e:
            logger.warning(f"tight_layout failed for best_models_summary: {e}")
        return fig

    def _plot_driver_waterfall(self, df: pd.DataFrame):
        """Stacked bar: paid-media / organic / context / baseline share per variant."""
        share_cols = [
            "paid_media_share",
            "organic_share",
            "context_share",
            "baseline_share",
        ]
        available = [c for c in share_cols if c in df.columns]
        if not available:
            logger.warning(
                "No decomposition share columns – skipping waterfall"
            )
            return None

        plot_df = df[["benchmark_variant"] + available].copy()
        plot_df = plot_df.dropna(subset=available, how="all")
        if plot_df.empty:
            logger.warning("No decomposition share data to plot")
            return None

        n_variants = len(plot_df)
        fig_width = max(14, n_variants * 0.5)
        fig, ax = plt.subplots(figsize=(fig_width, 8))

        colors = {
            "paid_media_share": "#2196F3",
            "organic_share": "#4CAF50",
            "context_share": "#FF9800",
            "baseline_share": "#9E9E9E",
        }
        labels = {
            "paid_media_share": "Paid Media",
            "organic_share": "Organic",
            "context_share": "Context",
            "baseline_share": "Baseline (trend/season/intercept)",
        }

        bottom = np.zeros(len(plot_df))
        for col in available:
            vals = plot_df[col].fillna(0).values
            ax.bar(
                plot_df["benchmark_variant"],
                vals,
                bottom=bottom,
                label=labels.get(col, col),
                color=colors.get(col, None),
                alpha=0.85,
            )
            bottom += vals

        ax.set_title(
            "Driver Contribution Shares by Variant",
            fontsize=16,
            fontweight="bold",
        )
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("Share of Total Response", fontsize=12)
        ax.set_ylim(0, 1.05)
        ax.legend(title="Driver", bbox_to_anchor=(1.01, 1), loc="upper left")
        ax.grid(axis="y", alpha=0.3)

        if n_variants > 15:
            plt.xticks(rotation=45, ha="right", fontsize=9)
        else:
            plt.xticks(rotation=45, ha="right")

        plt.tight_layout()
        return fig

    def _plot_roas_by_channel(self, df: pd.DataFrame):
        """Grouped bar chart of ROAS per channel across variants."""
        if "channel_roas_json" not in df.columns:
            logger.warning(
                "No channel_roas_json column – skipping ROAS by channel plot"
            )
            return None

        # Parse the JSON strings into a wide DataFrame
        roas_rows = []
        for _, row in df.iterrows():
            raw = row.get("channel_roas_json", "")
            if not raw or raw == "{}":
                continue
            try:
                roas_dict = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(roas_dict, dict):
                    continue
                entry = {"benchmark_variant": row["benchmark_variant"]}
                entry.update(
                    {
                        ch: float(v)
                        for ch, v in roas_dict.items()
                        if v is not None and isinstance(v, (int, float))
                    }
                )
                roas_rows.append(entry)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        if not roas_rows:
            logger.warning("No parseable channel ROAS data – skipping plot")
            return None

        roas_df = pd.DataFrame(roas_rows).set_index("benchmark_variant")
        channel_cols = [c for c in roas_df.columns if roas_df[c].notna().any()]
        if not channel_cols:
            return None

        roas_df = roas_df[channel_cols].dropna(how="all")
        if roas_df.empty:
            return None

        n_variants = len(roas_df)
        n_channels = len(channel_cols)
        fig_width = max(14, n_variants * n_channels * 0.25)
        fig, ax = plt.subplots(figsize=(fig_width, 8))

        melted = roas_df.reset_index().melt(
            id_vars="benchmark_variant",
            value_vars=channel_cols,
            var_name="Channel",
            value_name="ROAS",
        )

        # Cap y-axis at the 98th percentile to handle extreme outliers.
        p98 = float(melted["ROAS"].quantile(0.98))
        actual_max = float(melted["ROAS"].max())
        y_ceiling = p98 * 1.05

        sns.barplot(
            data=melted,
            x="benchmark_variant",
            y="ROAS",
            hue="Channel",
            ax=ax,
        )
        if y_ceiling > 0:
            ax.set_ylim(0, y_ceiling)

        title = "ROAS by Channel and Variant"
        subtitle_parts = []
        if actual_max > y_ceiling and y_ceiling > 0:
            clipped_channels = (
                melted.groupby("Channel")["ROAS"]
                .max()
                .pipe(lambda s: s[s > y_ceiling].index.tolist())
            )
            subtitle_parts.append(
                f"y-axis capped at {y_ceiling:.4g}; "
                f"actual max {actual_max:.4g} – "
                f"clipped: {', '.join(clipped_channels)}"
            )
        # Note when ROAS values are very small (proxy/sessions-based KPIs)
        if actual_max < 0.1:
            subtitle_parts.append(
                "Note: small values expected when dep_var is a "
                "proxy (sessions/clicks) rather than revenue"
            )
        if subtitle_parts:
            title += "\n(" + "; ".join(subtitle_parts) + ")"

        ax.set_title(title, fontsize=14, fontweight="bold")
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("ROAS (Return on Ad Spend)", fontsize=12)
        ax.legend(
            title="Channel",
            bbox_to_anchor=(1.01, 1),
            loc="upper left",
            fontsize=8,
        )
        ax.grid(axis="y", alpha=0.3)

        if n_variants > 10:
            plt.xticks(rotation=45, ha="right", fontsize=9)
        else:
            plt.xticks(rotation=45, ha="right")

        plt.tight_layout()
        return fig

    def _plot_cpa_by_channel(self, df: pd.DataFrame):
        """Grouped bar chart of CPA per channel across variants."""
        if "channel_cpa_json" not in df.columns:
            logger.warning(
                "No channel_cpa_json column – skipping CPA by channel plot"
            )
            return None

        # Parse the JSON strings into a wide DataFrame
        cpa_rows = []
        for _, row in df.iterrows():
            raw = row.get("channel_cpa_json", "")
            if not raw or raw == "{}":
                continue
            try:
                cpa_dict = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(cpa_dict, dict):
                    continue
                entry = {"benchmark_variant": row["benchmark_variant"]}
                entry.update(
                    {
                        ch: float(v)
                        for ch, v in cpa_dict.items()
                        if v is not None and isinstance(v, (int, float))
                    }
                )
                cpa_rows.append(entry)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        if not cpa_rows:
            logger.warning("No parseable channel CPA data – skipping plot")
            return None

        cpa_df = pd.DataFrame(cpa_rows).set_index("benchmark_variant")
        channel_cols = [c for c in cpa_df.columns if cpa_df[c].notna().any()]
        if not channel_cols:
            return None

        cpa_df = cpa_df[channel_cols].dropna(how="all")
        if cpa_df.empty:
            return None

        n_variants = len(cpa_df)
        n_channels = len(channel_cols)
        fig_width = max(14, n_variants * n_channels * 0.25)
        fig, ax = plt.subplots(figsize=(fig_width, 8))

        melted = cpa_df.reset_index().melt(
            id_vars="benchmark_variant",
            value_vars=channel_cols,
            var_name="Channel",
            value_name="CPA",
        )

        # Cap y-axis at the 98th percentile so extreme outlier channels
        # (e.g. low-spend partners with very high CPA) don't compress the
        # scale for all other channels.  Annotate when capping occurs.
        p98 = float(melted["CPA"].quantile(0.98))
        actual_max = float(melted["CPA"].max())
        y_ceiling = p98 * 1.05  # 5 % headroom above the 98th pct

        sns.barplot(
            data=melted,
            x="benchmark_variant",
            y="CPA",
            hue="Channel",
            ax=ax,
        )
        if y_ceiling > 0:
            ax.set_ylim(0, y_ceiling)

        title = "CPA by Channel and Variant"
        if actual_max > y_ceiling:
            clipped_channels = (
                melted.groupby("Channel")["CPA"]
                .max()
                .pipe(lambda s: s[s > y_ceiling].index.tolist())
            )
            title += (
                f"\n(y-axis capped at {y_ceiling:,.0f}; "
                f"actual max {actual_max:,.0f} – "
                f"clipped: {', '.join(clipped_channels)})"
            )
            logger.info(
                f"CPA plot: y-axis capped at {y_ceiling:,.0f} "
                f"(actual max {actual_max:,.0f} from "
                f"{clipped_channels})"
            )

        ax.set_title(title, fontsize=14, fontweight="bold")
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("CPA (Cost Per Acquisition)", fontsize=12)
        ax.legend(
            title="Channel",
            bbox_to_anchor=(1.01, 1),
            loc="upper left",
            fontsize=8,
        )
        ax.grid(axis="y", alpha=0.3)

        if n_variants > 10:
            plt.xticks(rotation=45, ha="right", fontsize=9)
        else:
            plt.xticks(rotation=45, ha="right")

        plt.tight_layout()
        return fig

    def generate_summary_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Generate summary statistics."""
        summary = {
            "total_variants": len(df),
            "metrics": {},
        }

        # Compute stats for each metric
        metric_cols = [
            "rsq_train",
            "rsq_val",
            "rsq_test",
            "nrmse_train",
            "nrmse_val",
            "nrmse_test",
            "decomp_rssd",
            "mape",
            "paid_media_share",
            "baseline_share",
            "organic_share",
            "context_share",
            "allocator_stability_roas_cv",
        ]

        for col in metric_cols:
            if col in df.columns:
                summary["metrics"][col] = {
                    "mean": float(df[col].mean()),
                    "std": float(df[col].std()),
                    "min": float(df[col].min()),
                    "max": float(df[col].max()),
                    "median": float(df[col].median()),
                }

        # Best variants (skip if all NaN - happens in test runs)
        if "rsq_val" in df.columns and not df["rsq_val"].isna().all():
            try:
                best_idx = df["rsq_val"].idxmax()
                summary["best_by_rsq_val"] = df.loc[
                    best_idx, "benchmark_variant"
                ]
            except (ValueError, KeyError):
                logger.warning(
                    "Could not determine best variant by R² validation"
                )

        if "nrmse_val" in df.columns and not df["nrmse_val"].isna().all():
            try:
                best_idx = df["nrmse_val"].idxmin()
                summary["best_by_nrmse_val"] = df.loc[
                    best_idx, "benchmark_variant"
                ]
            except (ValueError, KeyError):
                logger.warning(
                    "Could not determine best variant by NRMSE validation"
                )

        if "decomp_rssd" in df.columns and not df["decomp_rssd"].isna().all():
            try:
                best_idx = df["decomp_rssd"].idxmin()
                summary["best_by_decomp_rssd"] = df.loc[
                    best_idx, "benchmark_variant"
                ]
            except (ValueError, KeyError):
                logger.warning(
                    "Could not determine best variant by decomp RSSD"
                )

        if (
            "allocator_stability_roas_cv" in df.columns
            and not df["allocator_stability_roas_cv"].isna().all()
        ):
            try:
                best_idx = df["allocator_stability_roas_cv"].idxmin()
                summary["best_by_allocator_stability"] = df.loc[
                    best_idx, "benchmark_variant"
                ]
            except (ValueError, KeyError):
                logger.warning(
                    "Could not determine best variant by allocator stability"
                )

        # Check for test run quality issues
        if "iterations" in df.columns:
            try:
                # Convert to numeric, handling any string issues
                iterations_col = pd.to_numeric(
                    df["iterations"], errors="coerce"
                )
                avg_iterations = iterations_col.mean()
                if not pd.isna(avg_iterations) and avg_iterations < 100:
                    summary["test_run_warning"] = True
                    summary["avg_iterations"] = int(avg_iterations)
            except Exception as e:
                logger.warning(f"Could not check test run quality: {e}")

        return summary


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze benchmark results and generate plots"
    )
    parser.add_argument(
        "--benchmark-id",
        required=True,
        help="Benchmark ID to analyze",
    )
    parser.add_argument(
        "--output-dir",
        help="Local directory to save plots and CSV (optional)",
    )
    parser.add_argument(
        "--format",
        choices=["png", "pdf", "svg"],
        default="png",
        help="Plot format (default: png)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip plot generation, only export CSV",
    )
    parser.add_argument(
        "--queue-name",
        default=os.getenv("QUEUE_NAME", "default-dev"),
        help=(
            "Queue name used for the benchmark run "
            "(default: $QUEUE_NAME env var or 'default-dev'). "
            "Used to map variant names to result timestamps."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--scan-gcs",
        dest="scan_gcs",
        action="store_true",
        help=(
            "Collect results by scanning benchmarks/{id}/{variant}/model_summary.json "
            "directly in GCS instead of using plan.json + robyn/ paths. "
            "Required for multi-config runs where plan.json only contains the "
            "last config's variants."
        ),
    )
    parser.add_argument(
        "--min-r2",
        dest="min_r2",
        type=float,
        default=0,
        help=(
            "Minimum R² threshold to include a result in plots (default: 0, "
            "i.e. no filtering). Uses rsq_val when available, otherwise "
            "rsq_train. The CSV is always exported with all results regardless "
            "of this threshold."
        ),
    )

    args = parser.parse_args()

    # Set logging level
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger.setLevel(logging.DEBUG)
        logger.info("🔍 DEBUG MODE ENABLED")
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    # Initialize analyzer
    analyzer = BenchmarkAnalyzer()

    # Collect results — prefer direct GCS scan for multi-config runs
    logger.info(f"Analyzing benchmark: {args.benchmark_id}")
    if args.scan_gcs:
        logger.info("Using direct GCS scan (--scan-gcs)")
        df = analyzer.collect_results_from_gcs_scan(args.benchmark_id)
        if df is None or df.empty:
            logger.warning("GCS scan returned no results; falling back to plan.json method")
            df = analyzer.collect_results(args.benchmark_id, args.queue_name)
    else:
        df = analyzer.collect_results(args.benchmark_id, args.queue_name)

    if df is None or df.empty:
        logger.warning(
            "No results found to analyze — all benchmark jobs may have failed. "
            "Check the variant diagnostics logged above for crash details."
        )
        logger.warning(
            "Tip: check GCS for panic_error.json / console.log under "
            f"benchmarks/{args.benchmark_id}/<variant>/"
        )
        return 0

    logger.info(f"Collected {len(df)} results")

    # Export CSV with ALL variants — no R² filtering here so the results
    # page always shows the complete picture including low-quality runs.
    csv_path = analyzer.export_csv(df, args.benchmark_id, args.output_dir)
    logger.info(f"Results exported to CSV: {csv_path}")

    # Apply R² filter ONLY for plot generation so plots stay readable.
    # The CSV written above already contains every variant.
    if args.min_r2 is not None and args.min_r2 > 0:
        # Prefer rsq_val; fall back to rsq_train when val is absent
        if "rsq_val" in df.columns and df["rsq_val"].notna().any():
            r2_col = "rsq_val"
        elif "rsq_train" in df.columns and df["rsq_train"].notna().any():
            r2_col = "rsq_train"
        else:
            r2_col = None

        if r2_col:
            before = len(df)
            df = df[
                df[r2_col].isna() | (df[r2_col] >= args.min_r2)
            ].copy()
            removed = before - len(df)
            if removed:
                logger.info(
                    f"Excluding {removed} result(s) with {r2_col} < "
                    f"{args.min_r2} from plots (kept {len(df)} of {before})"
                )
        else:
            logger.warning(
                "No R² columns found; skipping plot quality filter"
            )

        if df.empty:
            logger.warning(
                f"No results remaining for plots after R² filter "
                f"(min_r2={args.min_r2}); skipping plot generation."
            )
            return 0

    # Generate summary statistics
    summary = analyzer.generate_summary_stats(df)
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY STATISTICS")
    logger.info("=" * 80)
    logger.info(f"Total variants: {summary['total_variants']}")

    # Check for test run warning
    if summary.get("test_run_warning"):
        logger.warning("")
        logger.warning("⚠️  TEST RUN DETECTED")
        logger.warning(
            f"Average iterations: {summary.get('avg_iterations', 'unknown')}"
        )
        logger.warning("")
        logger.warning(
            "Results may show little variation between configurations."
        )
        logger.warning("For meaningful comparison, consider running with:")
        logger.warning("  - 1000+ iterations")
        logger.warning("  - 3+ trials")
        logger.warning("")
        logger.warning("Use --full-run flag for production analysis.")
        logger.warning("")

    if "best_by_rsq_val" in summary:
        logger.info(f"Best by R² validation: {summary['best_by_rsq_val']}")
    if "best_by_nrmse_val" in summary:
        logger.info(f"Best by NRMSE validation: {summary['best_by_nrmse_val']}")
    if "best_by_decomp_rssd" in summary:
        logger.info(f"Best by decomp RSSD: {summary['best_by_decomp_rssd']}")
    if "best_by_allocator_stability" in summary:
        logger.info(
            f"Best allocator stability (lowest ROAS CV): "
            f"{summary['best_by_allocator_stability']}"
        )

    logger.info("=" * 80)

    # Generate plots
    if not args.no_plots:
        if not PLOTTING_AVAILABLE:
            logger.error(
                "Cannot generate plots. Install matplotlib and seaborn:"
            )
            logger.error("  pip install matplotlib seaborn")
            return 1

        analyzer.generate_plots(
            df, args.benchmark_id, args.output_dir, args.format
        )

    logger.info("\n✅ Analysis complete!")
    logger.info(f"\nView results:")
    logger.info(
        f"  CSV: gs://{GCS_BUCKET}/{BENCHMARK_ROOT}/{args.benchmark_id}/"
    )
    logger.info(
        f"  Plots: gs://{GCS_BUCKET}/{BENCHMARK_ROOT}/{args.benchmark_id}/plots_*/"
    )

    if args.output_dir:
        logger.info(f"  Local: {args.output_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
