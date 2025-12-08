#!/usr/bin/env python3
"""
Example script demonstrating how to read and use extracted parquet data.

This script shows various ways to access and analyze the parquet files
extracted from OutputModels.RDS.
"""

import pandas as pd
from google.cloud import storage


def read_parquet_component(bucket_name, run_path, component):
    """
    Read a parquet component from GCS.

    Args:
        bucket_name: GCS bucket name (e.g., "mmm-app-output")
        run_path: Path to run (e.g., "robyn/v1/US/1234567890")
        component: Component name (xDecompAgg, resultHypParam, mediaVecCollect, xDecompVecCollect)

    Returns:
        pandas.DataFrame
    """
    gcs_path = (
        f"gs://{bucket_name}/{run_path}/output_models_data/{component}.parquet"
    )
    print(f"Reading: {gcs_path}")
    return pd.read_parquet(gcs_path)


def example_1_read_model_metrics():
    """Example 1: Read and display model performance metrics"""
    print("\n" + "=" * 60)
    print("Example 1: Read Model Performance Metrics")
    print("=" * 60)

    # Replace with your actual values
    bucket = "mmm-app-output"
    run_path = "robyn/v1/US/1234567890"

    try:
        # Read hyperparameters and metrics
        df = read_parquet_component(bucket, run_path, "resultHypParam")

        print(f"\nFound {len(df)} candidate models")
        print("\nTop 5 models by R-squared (train):")
        print(df[["solID", "rsq_train", "nrmse_train", "decomp_rssd"]].head())

        # Find best model
        best_model = df.iloc[0]
        print(f"\nBest model: {best_model['solID']}")
        print(f"  R² (train): {best_model['rsq_train']:.4f}")
        print(f"  NRMSE (train): {best_model['nrmse_train']:.4f}")

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure to replace bucket and run_path with actual values")


def example_2_analyze_channel_contributions():
    """Example 2: Analyze channel contributions"""
    print("\n" + "=" * 60)
    print("Example 2: Analyze Channel Contributions")
    print("=" * 60)

    bucket = "mmm-app-output"
    run_path = "robyn/v1/US/1234567890"

    try:
        # Read decomposition data
        df = read_parquet_component(bucket, run_path, "xDecompAgg")

        print(f"\nFound {len(df)} decomposition records")

        # Assuming the dataframe has columns like: solID, channel, contribution, spend
        if "channel" in df.columns and "contribution" in df.columns:
            # Group by channel and sum contributions
            channel_contrib = (
                df.groupby("channel")["contribution"]
                .sum()
                .sort_values(ascending=False)
            )

            print("\nTop 10 channels by total contribution:")
            print(channel_contrib.head(10))
        else:
            print("\nAvailable columns:")
            print(df.columns.tolist())
            print("\nFirst few rows:")
            print(df.head())

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure to replace bucket and run_path with actual values")


def example_3_compare_multiple_runs():
    """Example 3: Compare metrics across multiple runs"""
    print("\n" + "=" * 60)
    print("Example 3: Compare Multiple Model Runs")
    print("=" * 60)

    bucket = "mmm-app-output"

    # List of runs to compare
    runs = [
        "robyn/v1/US/1234567890",
        "robyn/v1/US/1234567891",
        "robyn/v1/US/1234567892",
    ]

    comparison = []

    for run_path in runs:
        try:
            df = read_parquet_component(bucket, run_path, "resultHypParam")
            best_model = df.iloc[0]

            comparison.append(
                {
                    "run": run_path.split("/")[-1],
                    "model_id": best_model["solID"],
                    "rsq_train": best_model["rsq_train"],
                    "nrmse_train": best_model["nrmse_train"],
                    "decomp_rssd": best_model.get("decomp_rssd", None),
                }
            )
        except Exception as e:
            print(f"Skipping {run_path}: {e}")

    if comparison:
        comparison_df = pd.DataFrame(comparison)
        print("\nModel Performance Comparison:")
        print(comparison_df.to_string(index=False))
    else:
        print("No runs found. Update the runs list with actual GCS paths.")


def example_4_list_available_runs():
    """Example 4: List all available runs with parquet data"""
    print("\n" + "=" * 60)
    print("Example 4: List Available Runs")
    print("=" * 60)

    bucket_name = "mmm-app-output"

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        # Find all parquet files
        blobs = bucket.list_blobs(prefix="robyn/")

        runs_with_parquet = set()
        for blob in blobs:
            if "output_models_data" in blob.name and blob.name.endswith(
                ".parquet"
            ):
                # Extract run path (everything before /output_models_data/)
                run_path = blob.name.split("/output_models_data/")[0]
                runs_with_parquet.add(run_path)

        print(f"\nFound {len(runs_with_parquet)} runs with parquet data:")
        for run in sorted(runs_with_parquet)[:10]:
            print(f"  - {run}")

        if len(runs_with_parquet) > 10:
            print(f"  ... and {len(runs_with_parquet) - 10} more")

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure you have GCS access configured")


def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("OutputModels Parquet Data - Usage Examples")
    print("=" * 60)
    print("\nThis script demonstrates how to read and analyze")
    print("parquet data extracted from OutputModels.RDS files.")
    print("\nNote: Update bucket and run_path values with your actual data")

    # Run examples
    example_1_read_model_metrics()
    example_2_analyze_channel_contributions()
    example_3_compare_multiple_runs()
    example_4_list_available_runs()

    print("\n" + "=" * 60)
    print("For more information, see:")
    print("docs/OUTPUT_MODELS_PARQUET.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
