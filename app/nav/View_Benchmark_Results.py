"""
Benchmark Results Visualization Page (Hidden)

This page is NOT included in the main navigation.
Access it directly via query parameter: ?page=View_Benchmark_Results

Displays benchmark results with:
- CSV data table
- All 6 visualization plots
- Download buttons
"""

import io
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from google.cloud import storage
from PIL import Image

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(
    page_title="Benchmark Results Visualization",
    page_icon="📊",
    layout="wide",
)

# Title
st.title("📊 Benchmark Results Visualization")
st.markdown("*Hidden page - not in navigation*")
st.divider()

# Initialize GCS client
PROJECT_ID = "datawarehouse-422511"
GCS_BUCKET = "mmm-app-output"
BENCHMARK_ROOT = "benchmarks"

# The 4 best benchmark configurations available for quick selection
BEST_BENCHMARKS = [
    "dk_context_supply_geometric_75_90_daily_spend_to_clicks",
    "dk_context_supply_geometric_75_90_daily_mixed_by_funnel_clicks",
    "dk_context_expanded_test_geometric_75_90_daily_mixed_by_funnel_clicks",
    "dk_context_supply_plus_occ7d_geometric_75_90_daily_mixed_by_funnel_clicks",
]


@st.cache_resource
def get_storage_client():
    return storage.Client()


def _trigger_queue_tick(queue_name: str) -> bool:
    """
    Trigger an immediate queue tick via Cloud Tasks (if configured)
    or fall back to a direct in-process tick.

    Returns True if processing was triggered successfully.
    """
    try:
        from app_shared import (  # type: ignore[import-untyped]
            schedule_queue_tick_via_cloud_tasks,
            queue_tick_once_headless,
        )

        # Try Cloud Tasks first (event-driven, preferred)
        if schedule_queue_tick_via_cloud_tasks(queue_name, delay_seconds=0):
            return True

        # Fall back to direct in-process tick if Cloud Tasks isn't configured
        result = queue_tick_once_headless(queue_name)
        return result.get("ok", False)
    except Exception as exc:
        st.warning(f"Could not trigger queue tick: {exc}")
        return False


def list_benchmarks():
    """
    List benchmarks from GCS.

    Detects benchmarks via any of:
    - A ``plan.json`` file written immediately on submission
      (path: benchmarks/{benchmark_id}/plan.json)
    - A pre-aggregated ``results_*.csv`` (produced by collect-results / analyze)
    - Individual ``model_summary.json`` files written by each variant job
      (path: benchmarks/{benchmark_id}/{variant}/model_summary.json)
    """
    try:
        client = get_storage_client()
        bucket = client.bucket(GCS_BUCKET)

        prefix_to_search = f"{BENCHMARK_ROOT}/"
        blobs = bucket.list_blobs(prefix=prefix_to_search)
        benchmarks = set()

        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) < 2:
                continue
            # benchmarks/{benchmark_id}/plan.json  (submitted, may still be queued)
            if len(parts) == 3 and parts[2] == "plan.json":
                benchmarks.add(parts[1])
            # benchmarks/{benchmark_id}/results_*.csv
            elif (
                len(parts) >= 3
                and parts[2].startswith("results_")
                and blob.name.endswith(".csv")
            ):
                benchmarks.add(parts[1])
            # benchmarks/{benchmark_id}/{variant}/model_summary.json
            elif (
                len(parts) >= 4
                and parts[3] == "model_summary.json"
            ):
                benchmarks.add(parts[1])

        return sorted(benchmarks, reverse=True)

    except Exception as e:
        st.error(f"❌ Error listing benchmarks: {str(e)}")
        import traceback

        st.code(traceback.format_exc())
        return []


def _load_variant_statuses(benchmark_id: str) -> tuple:
    """
    Return (rows, queue_name) where rows is a per-variant status list.

    Each row dict has keys:
      - variant: str
      - status: str  ("PENDING" / "RUNNING" / "SUCCEEDED" / "FAILED" / "SKIPPED")
      - has_summary: bool
      - message: str (human-readable note)

    queue_name is the queue the benchmark was submitted to (from plan.json),
    or None if not recorded.
    """
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)

    # Load plan to get expected variants and queue name
    plan_blob = bucket.blob(f"{BENCHMARK_ROOT}/{benchmark_id}/plan.json")
    try:
        plan = json.loads(plan_blob.download_as_bytes())
    except Exception:
        return [], None

    queue_name = plan.get("queue_name")  # set by submit_variants_to_queue

    variants = [
        v.get("benchmark_variant", "")
        for v in plan.get("variants", [])
        if v.get("benchmark_variant")
    ]

    rows = []
    for variant in variants:
        prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/{variant}"

        # Check status.json
        status_blob = bucket.blob(f"{prefix}/status.json")
        try:
            status_data = json.loads(status_blob.download_as_bytes())
            state = status_data.get("state", "UNKNOWN")
        except Exception:
            state = "PENDING"
            status_data = {}

        # Check model_summary.json
        summary_blob = bucket.blob(f"{prefix}/model_summary.json")
        has_summary = summary_blob.exists()

        if state == "PENDING":
            message = "Job not started yet"
        elif state == "RUNNING":
            start = status_data.get("start_time", "")
            message = f"Running since {start}" if start else "Running…"
        elif state == "SUCCEEDED":
            dur = status_data.get("duration_minutes")
            if has_summary:
                message = f"Done in {dur:.1f} min" if dur else "Done"
            else:
                message = (
                    "⚠️ Completed but model_summary.json missing "
                    "(extract_model_summary.R may have failed — check console.log)"
                )
        elif state in ("FAILED", "ERROR"):
            err = status_data.get("error", status_data.get("message", ""))
            message = f"Failed: {err}" if err else "Failed"
        elif state == "SKIPPED":
            message = "Skipped"
        else:
            message = state

        rows.append(
            {
                "Variant": variant,
                "Status": state,
                "Summary": "✅" if has_summary else "—",
                "Note": message,
            }
        )

    return rows, queue_name


def _aggregate_variant_summaries(benchmark_id: str):
    """
    Aggregate per-variant model_summary.json files into a DataFrame.

    Scans ``benchmarks/{benchmark_id}/{variant}/model_summary.json`` and
    builds a row per variant with the key metrics used by the results table.
    Returns (DataFrame, source_description) or (None, reason).
    """
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)

    prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    # Expected path depth: benchmarks/{benchmark_id}/{variant}/model_summary.json
    # Parts: ["benchmarks", benchmark_id, variant, "model_summary.json"] → depth 4
    SUMMARY_PATH_DEPTH = 4
    summary_blobs = [
        b
        for b in blobs
        if b.name.endswith("/model_summary.json")
        and len(b.name.split("/")) == SUMMARY_PATH_DEPTH
    ]

    if not summary_blobs:
        return None, "no variant model_summary.json found"

    rows = []
    for blob in summary_blobs:
        variant_name = blob.name.split("/")[2]
        try:
            summary = json.loads(blob.download_as_bytes())
        except Exception:
            continue

        best_model = summary.get("best_model", {})
        decomp = summary.get("decomp_contribution", {}) or {}
        channel_roas = decomp.get("channel_roas") or {}
        channel_cpa = decomp.get("channel_cpa") or {}

        row = {
            "benchmark_variant": variant_name,
            "country": summary.get("country", ""),
            "adstock": summary.get("input_metadata", {}).get("adstock", ""),
            "train_size": str(summary.get("input_metadata", {}).get("train_size", "")),
            "iterations": summary.get("input_metadata", {}).get("iterations", ""),
            "trials": summary.get("input_metadata", {}).get("trials", ""),
            "resample_freq": summary.get("input_metadata", {}).get("resample_freq", "none"),
            "rsq_train": best_model.get("rsq_train"),
            "rsq_val": best_model.get("rsq_val"),
            "rsq_test": best_model.get("rsq_test"),
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
            "pareto_model_count": summary.get("pareto_model_count", 0),
            "training_time_mins": summary.get("training_time_mins"),
            "timestamp": summary.get("timestamp", ""),
        }
        rows.append(row)

    if not rows:
        return None, "could not parse any variant summaries"

    df = pd.DataFrame(rows)
    source = f"benchmarks/{benchmark_id}/ — {len(rows)} variant(s)"
    return df, source


def load_benchmark_csv(benchmark_id):
    """
    Load benchmark results for *benchmark_id*.

    Priority:
    1. Most recent pre-aggregated ``results_*.csv`` (collect-results output).
    2. Live aggregation from individual per-variant ``model_summary.json`` files.

    Returns (DataFrame, source_description) or None if nothing is available.
    """
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)

    # 1. Try pre-aggregated CSV
    prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
    blobs = bucket.list_blobs(prefix=prefix)
    csv_blobs = [b for b in blobs if b.name.endswith(".csv")]

    if csv_blobs:
        latest_csv = sorted(csv_blobs, key=lambda b: b.time_created, reverse=True)[0]
        csv_data = latest_csv.download_as_bytes()
        df = pd.read_csv(io.BytesIO(csv_data))
        return df, latest_csv.name

    # 2. Fall back to live aggregation from per-variant summaries
    df, source = _aggregate_variant_summaries(benchmark_id)
    if df is not None:
        return df, source

    return None


def load_benchmark_plots(benchmark_id):
    """Load all plots for a benchmark.

    Merges plots from all analyze-run directories so that a partially-failed
    run (which only uploaded some plots before encountering a GCS error) does
    not hide plots that were successfully uploaded in a previous run.  For
    each plot name the most-recently-created file is used.
    """
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)

    prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    # Collect one blob per plot name; prefer the most recently created one.
    plot_blobs: dict = {}  # plot_name -> blob
    for blob in blobs:
        if "plots_" not in blob.name or not blob.name.endswith(".png"):
            continue
        plot_name = blob.name.split("/")[-1].replace(".png", "")
        existing = plot_blobs.get(plot_name)
        if existing is None or blob.time_created > existing.time_created:
            plot_blobs[plot_name] = blob

    if not plot_blobs:
        return {}

    plots = {}
    for plot_name, blob in plot_blobs.items():
        try:
            img_data = blob.download_as_bytes()
            plots[plot_name] = Image.open(io.BytesIO(img_data))
        except Exception:
            pass

    return plots


# Sidebar - Benchmark Selection
with st.sidebar:
    st.header("Select Benchmark")

    _available_benchmarks = list_benchmarks() or BEST_BENCHMARKS

    selected_benchmarks = st.multiselect(
        "Benchmark ID(s)",
        options=_available_benchmarks,
        default=_available_benchmarks[:1] if _available_benchmarks else [],
        help="Select one or more benchmarks to visualize",
    )

    if st.button("🔄 Refresh"):
        st.cache_resource.clear()
        st.rerun()

# ── Combined results table (shown when >1 benchmark selected) ──────────────
if len(selected_benchmarks) > 1:
    st.subheader("📊 Combined Results")
    combined_frames = []
    for _bid in selected_benchmarks:
        try:
            _res = load_benchmark_csv(_bid)
            if _res is not None:
                _df, _ = _res
                _df = _df.copy()
                _df.insert(0, "benchmark_id", _bid)
                combined_frames.append(_df)
        except Exception:
            pass
    if combined_frames:
        combined_df = pd.concat(combined_frames, ignore_index=True)
        st.dataframe(combined_df, use_container_width=True, height=400)
        _combined_csv = combined_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Combined CSV",
            data=_combined_csv,
            file_name="benchmarks_combined.csv",
            mime="text/csv",
        )
        st.divider()
    else:
        st.info("No results data available yet for the selected benchmarks.")
        st.divider()

# ── Per-benchmark detail ───────────────────────────────────────────────────
for selected_benchmark in selected_benchmarks:
    _expander_label = f"📁 {selected_benchmark}"
    with st.expander(_expander_label, expanded=len(selected_benchmarks) == 1):
        st.info(f"**Selected Benchmark:** `{selected_benchmark}`")

        # Load CSV data
        st.subheader("📊 Results Data")

        try:
            result = load_benchmark_csv(selected_benchmark)
            if result is None:
                st.warning(
                    "⏳ No results available yet for this benchmark. "
                    "Results will appear automatically as each variant job completes."
                )
                # Show per-variant job status to help diagnose why results
                # are missing (jobs pending, running, failed, or succeeded
                # without generating model_summary.json).
                with st.expander("🔍 Variant job status (click to expand)", expanded=True):
                    try:
                        status_rows, queue_name = _load_variant_statuses(
                            selected_benchmark
                        )
                        if status_rows:
                            status_df = pd.DataFrame(status_rows)
                            # Color-code Status column
                            def _style_status(val: str) -> str:
                                colors = {
                                    "SUCCEEDED": "color: green",
                                    "RUNNING": "color: orange",
                                    "FAILED": "color: red",
                                    "ERROR": "color: red",
                                    "PENDING": "color: grey",
                                    "SKIPPED": "color: grey",
                                }
                                return colors.get(val, "")

                            st.dataframe(
                                status_df.style.map(
                                    _style_status, subset=["Status"]
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )
                            # Summary counts
                            counts = status_df["Status"].value_counts().to_dict()
                            cols = st.columns(len(counts))
                            for col, (status, cnt) in zip(cols, counts.items()):
                                col.metric(status, cnt)

                            # "Trigger queue" button when there are pending jobs
                            pending_count = counts.get("PENDING", 0)
                            if pending_count > 0:
                                st.divider()
                                q_label = (
                                    f"`{queue_name}`"
                                    if queue_name
                                    else "the queue"
                                )
                                st.warning(
                                    f"**{pending_count} job(s) are PENDING** in {q_label}. "
                                    "They have been submitted but not yet picked up by the "
                                    "queue processor. Click the button below to trigger "
                                    "processing now."
                                )
                                if st.button(
                                    f"▶ Trigger queue processing ({queue_name or 'default'})",
                                    type="primary",
                                    key="trigger_queue_btn",
                                ):
                                    triggered_queue = queue_name or "default"
                                    with st.spinner(
                                        f"Triggering queue `{triggered_queue}`…"
                                    ):
                                        ok = _trigger_queue_tick(triggered_queue)
                                    if ok:
                                        st.success(
                                            "✅ Queue processing triggered. "
                                            "Jobs will start shortly — "
                                            "refresh this page in a few minutes."
                                        )
                                    else:
                                        st.info(
                                            "Queue tick sent. If jobs don't start, "
                                            "go to **Run Experiment → Queue Monitor** "
                                            f"and select queue `{triggered_queue}` "
                                            "to start processing manually."
                                        )
                        else:
                            st.info(
                                "No variant information found. "
                                "plan.json may be missing or unreadable."
                            )
                    except Exception as diag_err:
                        st.warning(f"Could not load variant status: {diag_err}")
                df = None
            else:
                df, csv_path = result
                st.success(f"Loaded: `{csv_path}`")

                # Check for test run warning
                test_run_warning = False
                if "iterations" in df.columns:
                    avg_iterations = df["iterations"].mean()
                    if pd.notna(avg_iterations) and avg_iterations < 100:
                        test_run_warning = True
                        st.warning(
                            f"⚠️ **Test Run Results Detected**\n\n"
                            f"This benchmark used only **{int(avg_iterations)} iterations** on average.\n\n"
                            f"Results may appear similar across variants because models haven't converged. "
                            f"For meaningful comparison, consider running with:\n"
                            f"- **1000+ iterations**\n"
                            f"- **3+ trials**\n\n"
                            f"Use `--full-run`, `--extended-run`, or `--production-run` flag for production analysis."
                        )

                # Check for poor generalization (negative test R²)
                if "rsq_test" in df.columns:
                    bad_test = df[
                        df["rsq_test"].notna() & (df["rsq_test"] < 0)
                    ]
                    if not bad_test.empty:
                        bad_names = ", ".join(
                            f"`{v}`" for v in bad_test["benchmark_variant"].tolist()
                        )
                        st.warning(
                            f"⚠️ **Poor Test Generalization Detected**\n\n"
                            f"{len(bad_test)} variant(s) have **negative R² on the test split** "
                            f"(worse than a simple mean predictor): {bad_names}.\n\n"
                            "This usually indicates overfitting to the train/val period or a "
                            "structural break in the test data. Consider:\n"
                            "- Reviewing the date window and train/val/test split boundaries\n"
                            "- Trying different adstock and hyperparameter ranges\n"
                            "- Checking for outliers or anomalies in the test period"
                        )

                # Show metrics summary
                col1, col2, col3, col4, col5 = st.columns(5)
                with col1:
                    st.metric("Total Variants", len(df))
                with col2:
                    if "rsq_val" in df.columns:
                        avg_rsq = df["rsq_val"].mean()
                        st.metric(
                            "Avg R² (val)",
                            f"{avg_rsq:.3f}" if pd.notna(avg_rsq) else "N/A",
                        )
                with col3:
                    if "nrmse_val" in df.columns:
                        avg_nrmse = df["nrmse_val"].mean()
                        st.metric(
                            "Avg NRMSE (val)",
                            f"{avg_nrmse:.3f}" if pd.notna(avg_nrmse) else "N/A",
                        )
                with col4:
                    if "decomp_rssd" in df.columns:
                        avg_rssd = df["decomp_rssd"].mean()
                        st.metric(
                            "Avg Decomp RSSD",
                            f"{avg_rssd:.3f}" if pd.notna(avg_rssd) else "N/A",
                        )
                with col5:
                    if "allocator_stability_roas_cv" in df.columns:
                        avg_cv = df["allocator_stability_roas_cv"].mean()
                        st.metric(
                            "ROAS CV (Allocator Stability)",
                            f"{avg_cv:.3f}" if pd.notna(avg_cv) else "N/A",
                            help="Lower is better. CV of ROAS across Pareto-optimal models — measures how stable channel ROAS estimates are.",
                        )

                st.divider()

                # Display data table
                st.dataframe(
                    df,
                    use_container_width=True,
                    height=400,
                )

                # Download button
                csv_data = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="📥 Download CSV",
                    data=csv_data,
                    file_name=f"{selected_benchmark}_results.csv",
                    mime="text/csv",
                )

                # ── Preset comparison ──────────────────────────────────────
                # Only shown when the benchmark included a preset-comparison
                # sweep (i.e. the 'preset_label' column is non-empty).
                if (
                    df is not None
                    and "preset_label" in df.columns
                    and df["preset_label"].notna().any()
                    and (df["preset_label"] != "").any()
                ):
                    st.divider()
                    st.subheader("🔀 Preset Comparison")
                    st.caption(
                        "Results aggregated by hyperparameter preset. "
                        "Metrics show the mean across all variants that share the same preset."
                    )

                    preset_df = df[
                        df["preset_label"].notna() & (df["preset_label"] != "")
                    ].copy()
                    available_metrics = [
                        c
                        for c in [
                            "rsq_val",
                            "nrmse_val",
                            "decomp_rssd",
                            "allocator_stability_roas_cv",
                        ]
                        if c in preset_df.columns
                    ]

                    if available_metrics and not preset_df.empty:
                        agg = (
                            preset_df.groupby("preset_label")[available_metrics]
                            .mean()
                            .round(4)
                            .reset_index()
                            .rename(
                                columns={
                                    "preset_label": "Preset",
                                    "rsq_val": "R² (val)",
                                    "nrmse_val": "NRMSE (val)",
                                    "decomp_rssd": "Decomp RSSD",
                                    "allocator_stability_roas_cv": "ROAS CV",
                                }
                            )
                        )
                        # Preset order: conservative → balanced → exploratory → fb → meshed
                        _preset_order = [
                            "conservative",
                            "balanced",
                            "exploratory",
                            "fb",
                            "meshed",
                        ]
                        agg["_order"] = agg["Preset"].apply(
                            lambda p: (
                                _preset_order.index(p) if p in _preset_order else 99
                            )
                        )
                        agg = agg.sort_values("_order").drop(columns=["_order"])

                        st.dataframe(agg, use_container_width=True, hide_index=True)

                        # Bar charts for each available metric
                        import plotly.graph_objects as go  # type: ignore

                        metric_labels = {
                            "R² (val)": ("Higher is better", False),
                            "NRMSE (val)": ("Lower is better", True),
                            "Decomp RSSD": ("Lower is better", True),
                            "ROAS CV": (
                                "Lower is better — allocator stability",
                                True,
                            ),
                        }
                        display_cols = [c for c in agg.columns if c != "Preset"]
                        ncols = min(len(display_cols), 2)
                        cols = st.columns(ncols)
                        for idx, metric in enumerate(display_cols):
                            note, lower_better = metric_labels.get(
                                metric, ("", False)
                            )
                            fig = go.Figure(
                                go.Bar(
                                    x=agg["Preset"],
                                    y=agg[metric],
                                    text=agg[metric].astype(str),
                                    textposition="outside",
                                )
                            )
                            fig.update_layout(
                                title=f"{metric}<br><sup>{note}</sup>",
                                xaxis_title="Preset",
                                yaxis_title=metric,
                                height=320,
                                margin=dict(t=60, b=30, l=40, r=20),
                            )
                            cols[idx % ncols].plotly_chart(
                                fig, use_container_width=True
                            )

                # ── Adstock comparison ─────────────────────────────────────
                # Only shown when the benchmark included multiple adstock types
                # (i.e. the 'adstock' column has more than one distinct value).
                if (
                    df is not None
                    and "adstock" in df.columns
                    and df["adstock"].notna().any()
                    and df["adstock"].nunique() > 1
                ):
                    st.divider()
                    st.subheader("⚗️ Adstock Type Comparison")
                    st.caption(
                        "Results aggregated by adstock transformation type. "
                        "Metrics show the mean across all variants that share the same adstock type. "
                        "Produced when `--all-adstock` is used."
                    )

                    adstock_df = df[df["adstock"].notna()].copy()
                    available_metrics = [
                        c
                        for c in [
                            "rsq_val",
                            "nrmse_val",
                            "decomp_rssd",
                            "allocator_stability_roas_cv",
                        ]
                        if c in adstock_df.columns
                    ]

                    if available_metrics and not adstock_df.empty:
                        agg_adstock = (
                            adstock_df.groupby("adstock")[available_metrics]
                            .mean()
                            .round(4)
                            .reset_index()
                            .rename(
                                columns={
                                    "adstock": "Adstock Type",
                                    "rsq_val": "R² (val)",
                                    "nrmse_val": "NRMSE (val)",
                                    "decomp_rssd": "Decomp RSSD",
                                    "allocator_stability_roas_cv": "ROAS CV",
                                }
                            )
                        )
                        # Canonical order: geometric first, then weibull variants
                        _adstock_order = ["geometric", "weibull_cdf", "weibull_pdf"]
                        agg_adstock["_order"] = agg_adstock["Adstock Type"].apply(
                            lambda a: (
                                _adstock_order.index(a)
                                if a in _adstock_order
                                else 99
                            )
                        )
                        agg_adstock = agg_adstock.sort_values("_order").drop(
                            columns=["_order"]
                        )

                        st.dataframe(
                            agg_adstock, use_container_width=True, hide_index=True
                        )

                        import plotly.graph_objects as go  # type: ignore  # noqa: F811

                        metric_labels = {
                            "R² (val)": ("Higher is better", False),
                            "NRMSE (val)": ("Lower is better", True),
                            "Decomp RSSD": ("Lower is better", True),
                            "ROAS CV": (
                                "Lower is better — allocator stability",
                                True,
                            ),
                        }
                        display_cols = [
                            c for c in agg_adstock.columns if c != "Adstock Type"
                        ]
                        ncols = min(len(display_cols), 2)
                        cols = st.columns(ncols)
                        for idx, metric in enumerate(display_cols):
                            note, lower_better = metric_labels.get(
                                metric, ("", False)
                            )
                            fig = go.Figure(
                                go.Bar(
                                    x=agg_adstock["Adstock Type"],
                                    y=agg_adstock[metric],
                                    text=agg_adstock[metric].astype(str),
                                    textposition="outside",
                                )
                            )
                            fig.update_layout(
                                title=f"{metric}<br><sup>{note}</sup>",
                                xaxis_title="Adstock Type",
                                yaxis_title=metric,
                                height=320,
                                margin=dict(t=60, b=30, l=40, r=20),
                            )
                            cols[idx % ncols].plotly_chart(
                                fig, use_container_width=True
                            )

                # ── Window comparison ──────────────────────────────────────
                # Only shown when the benchmark included multiple seasonality
                # window lengths (i.e. the 'window_label' column is non-empty).
                if (
                    df is not None
                    and "window_label" in df.columns
                    and df["window_label"].notna().any()
                    and (df["window_label"] != "").any()
                    and df["window_label"].nunique() > 1
                ):
                    st.divider()
                    st.subheader("📅 Window Length Comparison")
                    st.caption(
                        "Results aggregated by seasonality window length. "
                        "Metrics show the mean across all variants that share the same window. "
                        "Produced when `--all-windows` is used."
                    )

                    window_df = df[
                        df["window_label"].notna() & (df["window_label"] != "")
                    ].copy()
                    available_metrics = [
                        c
                        for c in [
                            "rsq_val",
                            "nrmse_val",
                            "decomp_rssd",
                            "allocator_stability_roas_cv",
                        ]
                        if c in window_df.columns
                    ]

                    if available_metrics and not window_df.empty:
                        agg_window = (
                            window_df.groupby("window_label")[available_metrics]
                            .mean()
                            .round(4)
                            .reset_index()
                            .rename(
                                columns={
                                    "window_label": "Window",
                                    "rsq_val": "R² (val)",
                                    "nrmse_val": "NRMSE (val)",
                                    "decomp_rssd": "Decomp RSSD",
                                    "allocator_stability_roas_cv": "ROAS CV",
                                }
                            )
                        )
                        # Canonical order: full → 3y → 2y (longest to shortest)
                        _window_order = ["full", "3y", "2y"]
                        agg_window["_order"] = agg_window["Window"].apply(
                            lambda w: (
                                _window_order.index(w) if w in _window_order else 99
                            )
                        )
                        agg_window = agg_window.sort_values("_order").drop(
                            columns=["_order"]
                        )

                        st.dataframe(
                            agg_window, use_container_width=True, hide_index=True
                        )

                        import plotly.graph_objects as go  # type: ignore  # noqa: F811

                        metric_labels = {
                            "R² (val)": ("Higher is better", False),
                            "NRMSE (val)": ("Lower is better", True),
                            "Decomp RSSD": ("Lower is better", True),
                            "ROAS CV": (
                                "Lower is better — allocator stability",
                                True,
                            ),
                        }
                        display_cols = [
                            c for c in agg_window.columns if c != "Window"
                        ]
                        ncols = min(len(display_cols), 2)
                        cols = st.columns(ncols)
                        for idx, metric in enumerate(display_cols):
                            note, lower_better = metric_labels.get(
                                metric, ("", False)
                            )
                            fig = go.Figure(
                                go.Bar(
                                    x=agg_window["Window"],
                                    y=agg_window[metric],
                                    text=agg_window[metric].astype(str),
                                    textposition="outside",
                                )
                            )
                            fig.update_layout(
                                title=f"{metric}<br><sup>{note}</sup>",
                                xaxis_title="Window",
                                yaxis_title=metric,
                                height=320,
                                margin=dict(t=60, b=30, l=40, r=20),
                            )
                            cols[idx % ncols].plotly_chart(
                                fig, use_container_width=True
                            )

        except Exception as e:
            st.error(f"Error loading CSV: {e}")
            df = None

        st.divider()

        # Load and display plots
        st.subheader("📈 Visualization Plots")

        try:
            plots = load_benchmark_plots(selected_benchmark)

            if not plots:
                st.warning("No plots found for this benchmark")
            else:
                st.success(f"Loaded {len(plots)} plots")

                # Define plot order and titles
                # Core metric plots — always expected; warn if missing.
                core_plots = [
                    (
                        "rsq_comparison",
                        "R² Comparison",
                        "Compares R² across train/val/test splits for each variant",
                    ),
                    (
                        "nrmse_comparison",
                        "NRMSE Comparison",
                        "Compares NRMSE across train/val/test splits for each variant",
                    ),
                    (
                        "decomp_rssd",
                        "Decomposition RSSD",
                        "Shows decomposition quality (lower is better)",
                    ),
                    (
                        "train_val_test_gap",
                        "Train/Val/Test Gap Analysis",
                        "Scatter plots showing overfitting patterns",
                    ),
                    (
                        "metric_correlations",
                        "Metric Correlations",
                        "Heatmap of relationships between metrics",
                    ),
                    (
                        "best_models_summary",
                        "Best Models Summary",
                        "Top performers across different criteria",
                    ),
                ]

                # Enrichment plots — derived from decomposition data in
                # model_summary.json.  Only available when the training image
                # includes the extract_model_summary.R decomp extraction logic
                # (introduced with the per-channel hyperparameter feature).
                enrichment_plots = [
                    (
                        "driver_waterfall",
                        "Driver Contribution Shares",
                        "Stacked bar: paid-media / organic / context / baseline share of total response per variant",  # noqa: E501
                    ),
                    (
                        "roas_by_channel",
                        "ROAS by Channel",
                        "Return on Ad Spend per paid-media channel across variants",
                    ),
                    (
                        "cpa_by_channel",
                        "CPA by Channel",
                        "Cost Per Acquisition per paid-media channel across variants",  # noqa: E501
                    ),
                ]

                # Display core plots (warn loudly if missing)
                for plot_name, title, description in core_plots:
                    if plot_name in plots:
                        st.markdown(f"### {title}")
                        st.caption(description)
                        st.image(plots[plot_name], use_container_width=True)
                        st.divider()
                    else:
                        st.warning(f"Plot not available: {plot_name}")

                # Display enrichment plots (silent info if missing — data may not
                # be present for benchmarks run with older training images)
                enrichment_missing = [
                    (n, t, d) for n, t, d in enrichment_plots if n not in plots
                ]
                for plot_name, title, description in enrichment_plots:
                    if plot_name in plots:
                        st.markdown(f"### {title}")
                        st.caption(description)
                        st.image(plots[plot_name], use_container_width=True)
                        st.divider()

                if enrichment_missing:
                    missing_titles = ", ".join(t for _, t, _ in enrichment_missing)
                    st.info(
                        f"ℹ️ **Enrichment plots not available** "
                        f"({missing_titles}). "
                        "These require decomposition data extracted by "
                        "`extract_model_summary.R`. They are produced when the "
                        "benchmark is re-analysed after training with a current "
                        "training image, or when `analyze_benchmark_results.py` "
                        "is run once the jobs complete."
                    )

        except Exception as e:
            st.error(f"Error loading plots: {e}")
            st.exception(e)


if not selected_benchmarks:
    st.info("Select a benchmark from the sidebar to view results")
