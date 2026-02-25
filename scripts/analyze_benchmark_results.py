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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from google.cloud import storage

try:
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns
    import numpy as np
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
                logger.debug(f"Loaded {len(entries)} queue entries from {queue_name}")
                return entries
        except Exception as e:
            logger.warning(f"Could not load queue {queue_name}: {e}")
        return []
    
    def map_variants_to_timestamps(
        self, variants: List[Dict], queue_entries: List[Dict]
    ) -> Dict[str, str]:
        """
        Map variant names to their actual result timestamps from queue.
        
        Extracts timestamp from gcs_prefix field in completed queue entries.
        """
        timestamp_map = {}
        
        for variant in variants:
            variant_name = variant.get("benchmark_variant", "")
            if not variant_name:
                continue
                
            # Find matching queue entry
            for entry in queue_entries:
                params = entry.get("params", {})
                entry_variant = params.get("benchmark_variant", "")
                
                if entry_variant == variant_name and entry.get("status") == "COMPLETED":
                    # Extract timestamp from gcs_prefix
                    # Format: gs://bucket/robyn/default/de/20260225_112345/
                    gcs_prefix = entry.get("gcs_prefix", "")
                    if gcs_prefix:
                        parts = gcs_prefix.rstrip("/").split("/")
                        if len(parts) >= 5:
                            timestamp = parts[-1]
                            timestamp_map[variant_name] = timestamp
                            logger.debug(f"Mapped {variant_name} -> {timestamp}")
                            break
        
        logger.info(f"Mapped {len(timestamp_map)} variants to timestamps")
        return timestamp_map

    def collect_results(self, benchmark_id: str, queue_name: str = "default-dev") -> Optional[pd.DataFrame]:
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
        timestamp_map = self.map_variants_to_timestamps(variants, queue_entries)

        # Collect results for each variant
        results = []
        for variant in variants:
            result = self._collect_variant_result(variant, benchmark_id, timestamp_map)
            if result:
                results.append(result)

        if not results:
            logger.warning("No results collected")
            return None

        df = pd.DataFrame(results)
        logger.info(f"Collected {len(df)} results")
        return df

    def _collect_variant_result(
        self, variant: Dict[str, Any], benchmark_id: str, timestamp_map: Dict[str, str] = None
    ) -> Optional[Dict[str, Any]]:
        """Collect result for a single variant."""
        country = variant.get("country", "")
        revision = variant.get("revision", "default")
        variant_name = variant.get("benchmark_variant", "")
        
        # Get timestamp from map (actual execution timestamp)
        timestamp = None
        if timestamp_map:
            timestamp = timestamp_map.get(variant_name)
        
        # If we have timestamp, use exact path
        if timestamp:
            exact_path = f"robyn/{revision}/{country}/{timestamp}/model_summary.json"
            try:
                blob = self.bucket.blob(exact_path)
                if blob.exists():
                    summary = json.loads(blob.download_as_bytes())
                    logger.debug(f"Found result for {variant_name} at {exact_path}")
                    return self._extract_metrics(summary, variant)
                else:
                    logger.debug(f"Exact path not found: {exact_path}")
            except Exception as e:
                logger.debug(f"Error loading exact path {exact_path}: {e}")
        
        # Fallback: Search for model_summary.json in expected location
        prefix = f"robyn/{revision}/{country}/"
        
        try:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            summaries = [b for b in blobs if "model_summary.json" in b.name]
            
            # Find most recent matching summary
            for blob in sorted(summaries, key=lambda b: b.time_created, reverse=True):
                try:
                    summary = json.loads(blob.download_as_bytes())
                    if self._matches_variant(summary, variant):
                        logger.debug(f"Found result for {variant_name} via fallback matching")
                        return self._extract_metrics(summary, variant)
                except Exception as e:
                    logger.debug(f"Error loading summary {blob.name}: {e}")
                    continue
                    
        except Exception as e:
            logger.debug(f"Error searching for results: {e}")
        
        logger.warning(f"No results found for variant: {variant_name}")
        return None

    def _matches_variant(
        self, summary: Dict[str, Any], variant: Dict[str, Any]
    ) -> bool:
        """Check if summary matches variant configuration."""
        # Match on country
        if summary.get("country", "").lower() != variant.get("country", "").lower():
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
        
        return True

    def _extract_metrics(
        self, summary: Dict[str, Any], variant: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract metrics from model summary."""
        best_model = summary.get("best_model", {})

        return {
            # Benchmark metadata
            "benchmark_test": variant.get("benchmark_test", ""),
            "benchmark_variant": variant.get("benchmark_variant", ""),
            "country": variant.get("country", ""),
            "revision": variant.get("revision", "default"),
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
            "timestamp": summary.get("timestamp", ""),
        }

    def export_csv(
        self, df: pd.DataFrame, benchmark_id: str, local_path: Optional[str] = None
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
            logger.error("Matplotlib/seaborn not available. Cannot generate plots.")
            return

        logger.info("Generating analysis plots...")
        
        # Set style
        sns.set_style("whitegrid")
        plt.rcParams['figure.figsize'] = (12, 8)

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
        ]

        for plot_name, plot_func in plots:
            try:
                fig = plot_func(df)
                if fig:
                    self._save_plot(
                        fig, plot_name, plots_dir, output_dir, format
                    )
                    plt.close(fig)
            except Exception as e:
                logger.error(f"Error generating {plot_name}: {e}")

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

        # Upload to GCS
        gcs_path = f"{gcs_dir}/{name}.{format}"
        blob = self.bucket.blob(gcs_path)
        blob.upload_from_filename(tmp_path)

        # Save to local if requested
        if local_dir:
            local_path = Path(local_dir) / f"{name}.{format}"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(local_path, bbox_inches="tight", dpi=150)

        # Clean up temp file
        os.unlink(tmp_path)

    def _plot_rsq_comparison(self, df: pd.DataFrame):
        """Plot R² comparison across variants."""
        fig, ax = plt.subplots(figsize=(14, 8))

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
        sns.barplot(data=melted, x="benchmark_variant", y="R²", hue="Split", ax=ax)
        ax.set_title("R² Comparison Across Variants", fontsize=16, fontweight="bold")
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("R²", fontsize=12)
        ax.set_ylim(0, 1)
        plt.xticks(rotation=45, ha="right")
        ax.legend(title="Data Split")
        ax.grid(axis="y", alpha=0.3)

        return fig

    def _plot_nrmse_comparison(self, df: pd.DataFrame):
        """Plot NRMSE comparison across variants."""
        fig, ax = plt.subplots(figsize=(14, 8))

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
        ax.set_title("NRMSE Comparison Across Variants", fontsize=16, fontweight="bold")
        ax.set_xlabel("Variant", fontsize=12)
        ax.set_ylabel("NRMSE (lower is better)", fontsize=12)
        plt.xticks(rotation=45, ha="right")
        ax.legend(title="Data Split")
        ax.grid(axis="y", alpha=0.3)

        return fig

    def _plot_decomp_rssd(self, df: pd.DataFrame):
        """Plot decomposition RSSD comparison."""
        fig, ax = plt.subplots(figsize=(14, 8))

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
        ax.grid(axis="x", alpha=0.3)

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
                plot_df["val_test_gap"] = plot_df["rsq_val"] - plot_df["rsq_test"]

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
                ax1.set_title("R² Performance Gaps", fontsize=14, fontweight="bold")
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
                ax2.set_title("NRMSE Performance Gaps", fontsize=14, fontweight="bold")
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

        return fig

    def _plot_best_models_summary(self, df: pd.DataFrame):
        """Plot summary of best models by different criteria."""
        fig, axes = plt.subplots(2, 2, figsize=(16, 14))

        # Best by R² validation
        if "rsq_val" in df.columns:
            ax = axes[0, 0]
            top_models = df.nlargest(10, "rsq_val")[
                ["benchmark_variant", "rsq_val"]
            ]
            ax.barh(top_models["benchmark_variant"], top_models["rsq_val"])
            ax.set_title("Top 10 Models by R² Validation", fontsize=14, fontweight="bold")
            ax.set_xlabel("R² Validation", fontsize=12)
            ax.grid(axis="x", alpha=0.3)

        # Best by NRMSE validation
        if "nrmse_val" in df.columns:
            ax = axes[0, 1]
            top_models = df.nsmallest(10, "nrmse_val")[
                ["benchmark_variant", "nrmse_val"]
            ]
            ax.barh(top_models["benchmark_variant"], top_models["nrmse_val"])
            ax.set_title(
                "Top 10 Models by NRMSE Validation", fontsize=14, fontweight="bold"
            )
            ax.set_xlabel("NRMSE Validation", fontsize=12)
            ax.grid(axis="x", alpha=0.3)

        # Best by decomp RSSD
        if "decomp_rssd" in df.columns:
            ax = axes[1, 0]
            top_models = df.nsmallest(10, "decomp_rssd")[
                ["benchmark_variant", "decomp_rssd"]
            ]
            ax.barh(top_models["benchmark_variant"], top_models["decomp_rssd"])
            ax.set_title("Top 10 Models by Decomp RSSD", fontsize=14, fontweight="bold")
            ax.set_xlabel("Decomp RSSD", fontsize=12)
            ax.grid(axis="x", alpha=0.3)

        # Generalization (smallest val-test gap)
        if "rsq_val" in df.columns and "rsq_test" in df.columns:
            ax = axes[1, 1]
            df_temp = df.copy()
            df_temp["val_test_gap"] = abs(df_temp["rsq_val"] - df_temp["rsq_test"])
            top_models = df_temp.nsmallest(10, "val_test_gap")[
                ["benchmark_variant", "val_test_gap"]
            ]
            ax.barh(top_models["benchmark_variant"], top_models["val_test_gap"])
            ax.set_title(
                "Top 10 Models by Generalization (Val-Test Gap)",
                fontsize=14,
                fontweight="bold",
            )
            ax.set_xlabel("Val-Test Gap (R²)", fontsize=12)
            ax.grid(axis="x", alpha=0.3)

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

        # Best variants
        if "rsq_val" in df.columns:
            best_idx = df["rsq_val"].idxmax()
            summary["best_by_rsq_val"] = df.loc[best_idx, "benchmark_variant"]

        if "nrmse_val" in df.columns:
            best_idx = df["nrmse_val"].idxmin()
            summary["best_by_nrmse_val"] = df.loc[best_idx, "benchmark_variant"]

        if "decomp_rssd" in df.columns:
            best_idx = df["decomp_rssd"].idxmin()
            summary["best_by_decomp_rssd"] = df.loc[best_idx, "benchmark_variant"]

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

    args = parser.parse_args()

    # Initialize analyzer
    analyzer = BenchmarkAnalyzer()

    # Collect results
    logger.info(f"Analyzing benchmark: {args.benchmark_id}")
    df = analyzer.collect_results(args.benchmark_id)

    if df is None or df.empty:
        logger.error("No results found to analyze")
        return 1

    logger.info(f"Collected {len(df)} results")

    # Export CSV
    csv_path = analyzer.export_csv(df, args.benchmark_id, args.output_dir)
    logger.info(f"Results exported to CSV: {csv_path}")

    # Generate summary statistics
    summary = analyzer.generate_summary_stats(df)
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY STATISTICS")
    logger.info("=" * 80)
    logger.info(f"Total variants: {summary['total_variants']}")
    
    if "best_by_rsq_val" in summary:
        logger.info(f"Best by R² validation: {summary['best_by_rsq_val']}")
    if "best_by_nrmse_val" in summary:
        logger.info(f"Best by NRMSE validation: {summary['best_by_nrmse_val']}")
    if "best_by_decomp_rssd" in summary:
        logger.info(f"Best by decomp RSSD: {summary['best_by_decomp_rssd']}")
    
    logger.info("=" * 80)

    # Generate plots
    if not args.no_plots:
        if not PLOTTING_AVAILABLE:
            logger.error("Cannot generate plots. Install matplotlib and seaborn:")
            logger.error("  pip install matplotlib seaborn")
            return 1

        analyzer.generate_plots(
            df, args.benchmark_id, args.output_dir, args.format
        )

    logger.info("\n✅ Analysis complete!")
    logger.info(f"\nView results:")
    logger.info(f"  CSV: gs://{GCS_BUCKET}/{BENCHMARK_ROOT}/{args.benchmark_id}/")
    logger.info(f"  Plots: gs://{GCS_BUCKET}/{BENCHMARK_ROOT}/{args.benchmark_id}/plots_*/")
    
    if args.output_dir:
        logger.info(f"  Local: {args.output_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
