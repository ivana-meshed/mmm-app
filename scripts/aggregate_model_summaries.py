"""
Model Summary Aggregation Utilities

This module provides functions to:
1. Read model summary JSON files from GCS
2. Aggregate summaries by country
3. Generate summaries for existing models
"""

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.cloud import storage

logger = logging.getLogger(__name__)


class ModelSummaryAggregator:
    """Aggregates model summaries from GCS"""

    def __init__(self, bucket_name: str, project_id: Optional[str] = None):
        """
        Initialize the aggregator

        Args:
            bucket_name: Name of the GCS bucket
            project_id: GCP project ID (optional)
        """
        self.bucket_name = bucket_name
        self.project_id = project_id or os.getenv("PROJECT_ID")
        self.client = storage.Client(project=self.project_id)
        self.bucket = self.client.bucket(bucket_name)

    def list_model_runs(
        self, country: Optional[str] = None, revision: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        List all model runs in GCS

        Args:
            country: Filter by country (optional)
            revision: Filter by revision (optional)

        Returns:
            List of dicts with run metadata
        """
        prefix = "robyn/"
        if revision:
            prefix += f"{revision}/"
        if country:
            prefix += f"{country}/"

        blobs = self.client.list_blobs(
            self.bucket_name, prefix=prefix, delimiter="/"
        )

        runs = []
        for blob in blobs:
            # Structure: robyn/{revision}/{country}/{timestamp}/
            parts = blob.name.split("/")
            if len(parts) >= 4 and parts[0] == "robyn":
                run_info = {
                    "revision": parts[1],
                    "country": parts[2],
                    "timestamp": parts[3],
                    "path": "/".join(parts[:4]),
                }
                # Check if model_summary.json exists
                summary_path = f"{run_info['path']}/model_summary.json"
                summary_blob = self.bucket.blob(summary_path)
                run_info["has_summary"] = summary_blob.exists()
                runs.append(run_info)

        return runs

    def read_summary(self, run_path: str) -> Optional[Dict[str, Any]]:
        """
        Read a model summary JSON from GCS

        Args:
            run_path: Path to the run folder (e.g., robyn/v1/US/123456/)

        Returns:
            Summary dict or None if not found
        """
        summary_path = f"{run_path}/model_summary.json"
        blob = self.bucket.blob(summary_path)

        if not blob.exists():
            logger.warning(f"Summary not found: {summary_path}")
            return None

        try:
            content = blob.download_as_text()
            return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to read summary {summary_path}: {e}")
            return None

    def aggregate_by_country(
        self, country: str, revision: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Aggregate all model summaries for a country

        Args:
            country: Country code
            revision: Filter by revision (optional)

        Returns:
            Aggregated summary dict
        """
        runs = self.list_model_runs(country=country, revision=revision)
        runs_with_summary = [r for r in runs if r["has_summary"]]

        summaries = []
        for run in runs_with_summary:
            summary = self.read_summary(run["path"])
            if summary:
                summaries.append(summary)

        # Create aggregated summary
        aggregated = {
            "country": country,
            "revision": revision,
            "aggregated_at": datetime.now(timezone.utc).isoformat(),
            "total_runs": len(summaries),
            "runs": summaries,
        }

        # Add statistics
        if summaries:
            # Count runs with Pareto models
            runs_with_pareto = sum(
                1 for s in summaries if s.get("has_pareto_models", False)
            )
            aggregated["runs_with_pareto_models"] = runs_with_pareto

            # Get best model across all runs (by NRMSE)
            best_models = [
                s.get("best_model") for s in summaries if s.get("best_model")
            ]
            if best_models:
                # Find model with lowest NRMSE
                valid_models = [
                    m for m in best_models if m.get("nrmse") is not None
                ]
                if valid_models:
                    best_overall = min(
                        valid_models, key=lambda m: m.get("nrmse", float("inf"))
                    )
                    aggregated["best_model_overall"] = best_overall

        return aggregated

    def save_country_summary(
        self, country: str, revision: Optional[str] = None
    ) -> str:
        """
        Save aggregated country summary to GCS

        Args:
            country: Country code
            revision: Filter by revision (optional)

        Returns:
            GCS path where summary was saved
        """
        summary = self.aggregate_by_country(country, revision)

        # Save to GCS
        # Path: robyn-summaries/{country}/summary.json
        # or robyn-summaries/{country}/{revision}_summary.json if revision
        # specified
        if revision:
            summary_path = f"robyn-summaries/{country}/{revision}_summary.json"
        else:
            summary_path = f"robyn-summaries/{country}/summary.json"

        blob = self.bucket.blob(summary_path)
        blob.upload_from_string(
            json.dumps(summary, indent=2), content_type="application/json"
        )

        logger.info(
            f"Saved country summary to gs://{self.bucket_name}/{summary_path}"
        )
        return summary_path

    def generate_summary_for_existing_run(self, run_path: str) -> Optional[str]:
        """
        Generate summary for an existing model run that doesn't have one

        This requires:
        1. Downloading OutputCollect.RDS and InputCollect.RDS
        2. Running the R script to extract summary
        3. Uploading the summary back to GCS

        Args:
            run_path: Path to the run (e.g., robyn/v1/US/123456/)

        Returns:
            Path to generated summary or None if failed
        """
        # Check if summary already exists
        summary_path = f"{run_path}/model_summary.json"
        if self.bucket.blob(summary_path).exists():
            logger.info(f"Summary already exists: {summary_path}")
            return summary_path

        # Check if OutputCollect.RDS exists
        output_collect_path = f"{run_path}/OutputCollect.RDS"
        output_blob = self.bucket.blob(output_collect_path)
        if not output_blob.exists():
            logger.warning(
                f"OutputCollect.RDS not found: {output_collect_path}"
            )
            return None

        # Download RDS files to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            output_rds = tmp_path / "OutputCollect.RDS"
            input_rds = tmp_path / "InputCollect.RDS"

            # Download OutputCollect.RDS
            output_blob.download_to_filename(str(output_rds))
            logger.info(f"Downloaded OutputCollect.RDS to {output_rds}")

            # Download InputCollect.RDS if available
            input_blob = self.bucket.blob(f"{run_path}/InputCollect.RDS")
            if input_blob.exists():
                input_blob.download_to_filename(str(input_rds))
                logger.info(f"Downloaded InputCollect.RDS to {input_rds}")

            # Parse run_path to get metadata
            parts = run_path.rstrip("/").split("/")
            if len(parts) >= 4:
                revision = parts[1]
                country = parts[2]
                timestamp = parts[3]
            else:
                logger.error(f"Invalid run_path format: {run_path}")
                return None

            # Run R script to generate summary
            r_script = (
                Path(__file__).parent.parent
                / "r"
                / "generate_summary_from_rds.R"
            )
            if not r_script.exists():
                logger.error(f"R script not found: {r_script}")
                return None

            summary_json = tmp_path / "model_summary.json"

            cmd = [
                "Rscript",
                str(r_script),
                "--output-collect",
                str(output_rds),
                "--input-collect",
                str(input_rds) if input_rds.exists() else "",
                "--country",
                country,
                "--revision",
                revision,
                "--timestamp",
                timestamp,
                "--output",
                str(summary_json),
            ]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=True,
                )
                logger.info(f"R script output: {result.stdout}")
                if result.stderr:
                    logger.warning(f"R script stderr: {result.stderr}")
            except subprocess.CalledProcessError as e:
                logger.error(f"R script failed: {e.stderr}")
                return None
            except subprocess.TimeoutExpired:
                logger.error("R script timed out")
                return None

            # Upload summary to GCS
            if summary_json.exists():
                blob = self.bucket.blob(summary_path)
                blob.upload_from_filename(str(summary_json))
                logger.info(
                    f"Uploaded summary to gs://{self.bucket_name}/"
                    f"{summary_path}"
                )
                return summary_path
            else:
                logger.error(f"Summary file not created: {summary_json}")
                return None


def main():
    """CLI entry point for generating summaries"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate and aggregate model summaries"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", "mmm-app-output"),
        help="GCS bucket name",
    )
    parser.add_argument(
        "--project",
        default=os.getenv("PROJECT_ID"),
        help="GCP project ID",
    )
    parser.add_argument(
        "--country", help="Filter by country code (e.g., US, UK)"
    )
    parser.add_argument("--revision", help="Filter by revision")
    parser.add_argument(
        "--generate-missing",
        action="store_true",
        help="Generate summaries for runs that don't have them",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Aggregate summaries by country",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    aggregator = ModelSummaryAggregator(args.bucket, args.project)

    if args.generate_missing:
        runs = aggregator.list_model_runs(args.country, args.revision)
        runs_without_summary = [r for r in runs if not r["has_summary"]]

        logger.info(f"Found {len(runs_without_summary)} runs without summaries")

        for run in runs_without_summary:
            logger.info(f"Generating summary for {run['path']}")
            aggregator.generate_summary_for_existing_run(run["path"])

    if args.aggregate:
        if not args.country:
            # Get all unique countries
            runs = aggregator.list_model_runs()
            countries = set(r["country"] for r in runs)
            logger.info(f"Found {len(countries)} countries: {countries}")

            for country in countries:
                logger.info(f"Aggregating summaries for {country}")
                aggregator.save_country_summary(country, args.revision)
        else:
            aggregator.save_country_summary(args.country, args.revision)


if __name__ == "__main__":
    main()
