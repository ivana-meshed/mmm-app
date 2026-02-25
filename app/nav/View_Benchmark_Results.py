"""
Benchmark Results Visualization Page (Hidden)

This page is NOT included in the main navigation.
Access it directly via query parameter: ?page=View_Benchmark_Results

Displays benchmark results with:
- CSV data table
- All 6 visualization plots
- Download buttons
"""

import streamlit as st
import sys
from pathlib import Path
import json
import pandas as pd
from google.cloud import storage
import io
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

@st.cache_resource
def get_storage_client():
    return storage.Client()

def list_benchmarks():
    """List available benchmarks from GCS."""
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)
    
    # List all benchmark directories
    blobs = bucket.list_blobs(prefix=f"{BENCHMARK_ROOT}/", delimiter="/")
    benchmarks = []
    
    # Get the prefixes (directories)
    for prefix in blobs.prefixes:
        benchmark_id = prefix.replace(f"{BENCHMARK_ROOT}/", "").rstrip("/")
        if benchmark_id:  # Skip empty
            benchmarks.append(benchmark_id)
    
    return sorted(benchmarks, reverse=True)

def load_benchmark_csv(benchmark_id):
    """Load the most recent CSV for a benchmark."""
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)
    
    # List all CSV files for this benchmark
    prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
    blobs = bucket.list_blobs(prefix=prefix)
    csv_blobs = [b for b in blobs if b.name.endswith(".csv")]
    
    if not csv_blobs:
        return None
    
    # Get most recent
    latest_csv = sorted(csv_blobs, key=lambda b: b.time_created, reverse=True)[0]
    
    # Download and parse
    csv_data = latest_csv.download_as_bytes()
    df = pd.read_csv(io.BytesIO(csv_data))
    
    return df, latest_csv.name

def load_benchmark_plots(benchmark_id):
    """Load all plots for a benchmark."""
    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET)
    
    # Find the most recent plots directory
    prefix = f"{BENCHMARK_ROOT}/{benchmark_id}/"
    blobs = bucket.list_blobs(prefix=prefix)
    
    # Find plot directories
    plot_dirs = set()
    for blob in blobs:
        if "plots_" in blob.name and blob.name.endswith(".png"):
            # Extract plot directory
            parts = blob.name.split("/")
            if len(parts) >= 3:
                plot_dir = "/".join(parts[:3])  # benchmarks/id/plots_timestamp
                plot_dirs.add(plot_dir)
    
    if not plot_dirs:
        return {}
    
    # Use most recent plot directory
    latest_plot_dir = sorted(list(plot_dirs), reverse=True)[0]
    
    # Load all plots from that directory
    plots = {}
    plot_blobs = bucket.list_blobs(prefix=latest_plot_dir + "/")
    
    for blob in plot_blobs:
        if blob.name.endswith(".png"):
            plot_name = blob.name.split("/")[-1].replace(".png", "")
            img_data = blob.download_as_bytes()
            plots[plot_name] = Image.open(io.BytesIO(img_data))
    
    return plots

# Sidebar - Benchmark Selection
with st.sidebar:
    st.header("Select Benchmark")
    
    # List benchmarks
    try:
        benchmarks = list_benchmarks()
        
        if not benchmarks:
            st.warning("No benchmarks found")
            st.stop()
        
        selected_benchmark = st.selectbox(
            "Benchmark ID",
            options=benchmarks,
            help="Select a benchmark to visualize"
        )
        
        if st.button("🔄 Refresh List"):
            st.cache_resource.clear()
            st.rerun()
            
    except Exception as e:
        st.error(f"Error loading benchmarks: {e}")
        st.stop()

# Main content
if selected_benchmark:
    st.info(f"**Selected Benchmark:** `{selected_benchmark}`")
    
    # Load CSV data
    st.subheader("📊 Results Data")
    
    try:
        result = load_benchmark_csv(selected_benchmark)
        if result is None:
            st.warning("No CSV results found for this benchmark")
            df = None
        else:
            df, csv_path = result
            st.success(f"Loaded: `{csv_path}`")
            
            # Show metrics summary
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Variants", len(df))
            with col2:
                if "rsq_val" in df.columns:
                    avg_rsq = df["rsq_val"].mean()
                    st.metric("Avg R² (val)", f"{avg_rsq:.3f}" if pd.notna(avg_rsq) else "N/A")
            with col3:
                if "nrmse_val" in df.columns:
                    avg_nrmse = df["nrmse_val"].mean()
                    st.metric("Avg NRMSE (val)", f"{avg_nrmse:.3f}" if pd.notna(avg_nrmse) else "N/A")
            with col4:
                if "decomp_rssd" in df.columns:
                    avg_rssd = df["decomp_rssd"].mean()
                    st.metric("Avg Decomp RSSD", f"{avg_rssd:.3f}" if pd.notna(avg_rssd) else "N/A")
            
            st.divider()
            
            # Display data table
            st.dataframe(
                df,
                use_container_width=True,
                height=400,
            )
            
            # Download button
            csv_data = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download CSV",
                data=csv_data,
                file_name=f"{selected_benchmark}_results.csv",
                mime="text/csv",
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
            plot_config = [
                ("rsq_comparison", "R² Comparison", "Compares R² across train/val/test splits for each variant"),
                ("nrmse_comparison", "NRMSE Comparison", "Compares NRMSE across train/val/test splits for each variant"),
                ("decomp_rssd", "Decomposition RSSD", "Shows decomposition quality (lower is better)"),
                ("train_val_test_gap", "Train/Val/Test Gap Analysis", "Scatter plots showing overfitting patterns"),
                ("metric_correlations", "Metric Correlations", "Heatmap of relationships between metrics"),
                ("best_models_summary", "Best Models Summary", "Top performers across different criteria"),
            ]
            
            # Display plots in order
            for plot_name, title, description in plot_config:
                if plot_name in plots:
                    st.markdown(f"### {title}")
                    st.caption(description)
                    st.image(plots[plot_name], use_container_width=True)
                    st.divider()
                else:
                    st.warning(f"Plot not found: {plot_name}")
            
    except Exception as e:
        st.error(f"Error loading plots: {e}")
        st.exception(e)

else:
    st.info("Select a benchmark from the sidebar to view results")
