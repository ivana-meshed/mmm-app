#!/usr/bin/env python3
"""
Example: Reading and using model summaries

This script demonstrates how to read and use model summary files
from GCS for analysis and comparison.
"""

import json
import os
from datetime import datetime

from google.cloud import storage


def read_model_summary(bucket_name, run_path):
    """
    Read a model summary from GCS

    Args:
        bucket_name: GCS bucket name
        run_path: Path to run (e.g., "robyn/v1/US/1234567890")

    Returns:
        dict: Model summary or None if not found
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"{run_path}/model_summary.json")

    if not blob.exists():
        print(f"Summary not found: {run_path}")
        return None

    content = blob.download_as_text()
    return json.loads(content)


def compare_models(bucket_name, run_paths):
    """
    Compare models from multiple runs

    Args:
        bucket_name: GCS bucket name
        run_paths: List of run paths to compare

    Returns:
        dict: Comparison results
    """
    summaries = []
    for path in run_paths:
        summary = read_model_summary(bucket_name, path)
        if summary:
            summaries.append(summary)

    if not summaries:
        print("No valid summaries found")
        return None

    # Compare best models
    print("\n=== Model Comparison ===\n")
    print(f"{'Run':<30} {'Model ID':<15} {'NRMSE':<10} {'R²':<10}")
    print("-" * 65)

    for s in summaries:
        best = s.get("best_model", {})
        run_id = f"{s['country']}/{s['timestamp']}"
        model_id = best.get("model_id", "N/A")
        nrmse = best.get("nrmse", "N/A")
        rsq = best.get("rsq_train", "N/A")

        if isinstance(nrmse, float):
            nrmse = f"{nrmse:.4f}"
        if isinstance(rsq, float):
            rsq = f"{rsq:.4f}"

        print(f"{run_id:<30} {model_id:<15} {nrmse:<10} {rsq:<10}")

    # Find best overall
    valid_models = [
        (s, s.get("best_model", {}))
        for s in summaries
        if s.get("best_model", {}).get("nrmse") is not None
    ]

    if valid_models:
        best_overall_summary, best_overall = min(
            valid_models, key=lambda x: x[1]["nrmse"]
        )
        print("\n=== Best Overall Model ===")
        print(
            f"Run: {best_overall_summary['country']}/"
            f"{best_overall_summary['timestamp']}"
        )
        print(f"Model ID: {best_overall['model_id']}")
        print(f"NRMSE: {best_overall['nrmse']:.4f}")
        print(f"R² (train): {best_overall.get('rsq_train', 'N/A')}")

    # Check for Pareto models
    pareto_runs = [s for s in summaries if s.get("has_pareto_models")]
    print(f"\n=== Pareto Analysis ===")
    print(f"Runs with Pareto models: {len(pareto_runs)}/{len(summaries)}")

    return {
        "summaries": summaries,
        "best_overall": best_overall if valid_models else None,
        "pareto_count": len(pareto_runs),
    }


def list_recent_runs(bucket_name, country, limit=10):
    """
    List recent model runs for a country

    Args:
        bucket_name: GCS bucket name
        country: Country code
        limit: Maximum number of runs to return

    Returns:
        list: Run paths sorted by timestamp (newest first)
    """
    client = storage.Client()
    prefix = f"robyn/"

    blobs = client.list_blobs(bucket_name, prefix=prefix)

    runs = []
    for blob in blobs:
        parts = blob.name.split("/")
        if (
            len(parts) >= 5
            and parts[0] == "robyn"
            and parts[2] == country
            and blob.name.endswith("model_summary.json")
        ):
            run_path = "/".join(parts[:4])
            timestamp = parts[3]
            runs.append((timestamp, run_path))

    # Sort by timestamp (newest first)
    runs.sort(reverse=True)

    return [path for _, path in runs[:limit]]


def main():
    """Example usage"""
    bucket_name = os.getenv("GCS_BUCKET", "mmm-app-output")
    country = "US"

    print(f"Fetching recent runs for {country}...")

    # Get recent runs
    recent_runs = list_recent_runs(bucket_name, country, limit=5)

    if not recent_runs:
        print(f"No runs found for {country}")
        return

    print(f"\nFound {len(recent_runs)} recent runs:")
    for i, path in enumerate(recent_runs, 1):
        print(f"{i}. {path}")

    # Compare them
    print("\n" + "=" * 70)
    compare_models(bucket_name, recent_runs)

    # Read and inspect first run in detail
    if recent_runs:
        print("\n" + "=" * 70)
        print("\n=== Detailed View of Most Recent Run ===\n")
        summary = read_model_summary(bucket_name, recent_runs[0])

        if summary:
            print(f"Country: {summary.get('country')}")
            print(f"Revision: {summary.get('revision')}")
            print(f"Timestamp: {summary.get('timestamp')}")
            print(
                f"Training time: "
                f"{summary.get('training_time_mins', 'N/A')} mins"
            )
            print(
                f"Has Pareto models: "
                f"{summary.get('has_pareto_models', False)}"
            )
            print(
                f"Candidate models: "
                f"{summary.get('candidate_model_count', 0)}"
            )

            if summary.get("pareto_models"):
                print(f"\nPareto models ({len(summary['pareto_models'])}):")
                for i, model in enumerate(summary["pareto_models"][:3], 1):
                    print(
                        f"  {i}. {model['model_id']} - "
                        f"NRMSE: {model.get('nrmse', 'N/A')}"
                    )


if __name__ == "__main__":
    main()
