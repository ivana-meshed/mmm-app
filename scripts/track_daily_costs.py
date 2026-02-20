#!/usr/bin/env python3
"""
Daily Google Cloud Services Cost Tracking Script

This script tracks daily costs for the MMM Trainer application, broken down by:
- mmm-app-dev-web (dev web service)
- mmm-app-web (prod web service)  
- mmm-app-training (prod training jobs)
- mmm-app-dev-training (dev training jobs)

Within each service, costs are further broken down by:
- User requests costs
- Scheduler requests costs (invocations)
- Compute CPU costs
- Compute memory costs
- Registry costs (Artifact Registry)
- Storage costs (Cloud Storage)
- Scheduler service costs (base service fee)
- Secret Manager costs
- Networking costs
- GitHub Actions costs (CI/CD automation, including registry cleanup)
- Other relevant costs

Special Features:
- Dedicated "Scheduler & Automation Costs" section showing:
  * Cloud Scheduler service fees
  * Scheduler invocation costs (queue processing)
  * GitHub Actions costs (weekly cleanup and CI/CD)

Usage:
    python scripts/track_daily_costs.py [--days DAYS] [--output OUTPUT]

Requirements:
    - Google Cloud BigQuery billing export enabled
    - google-cloud-bigquery Python library
    - Appropriate GCP permissions (BigQuery Data Viewer)
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from google.api_core import exceptions as gcp_exceptions
    from google.cloud import bigquery
except ImportError:
    print(
        "Error: google-cloud-bigquery is required. "
        "Install with: pip install google-cloud-bigquery"
    )
    sys.exit(1)


# Configuration
PROJECT_ID = os.environ.get("PROJECT_ID", "datawarehouse-422511")
BILLING_DATASET = os.environ.get("BILLING_DATASET", "mmm_billing")
BILLING_ACCOUNT_NUM = os.environ.get(
    "BILLING_ACCOUNT_NUM", "01B2F0_BCBFB7_2051C5"
)
TABLE_NAME = f"gcp_billing_export_resource_v1_{BILLING_ACCOUNT_NUM}"

# Service identifiers
SERVICE_MAPPING = {
    "mmm-app-dev-web": {
        "service": "Cloud Run",
        "resource_filter": "mmm-app-dev-web",
        "type": "web",
        "env": "dev",
    },
    "mmm-app-web": {
        "service": "Cloud Run",
        "resource_filter": "mmm-app-web",
        "type": "web",
        "env": "prod",
    },
    "mmm-app-dev-training": {
        "service": "Cloud Run",
        "resource_filter": "mmm-app-dev-training",
        "type": "training",
        "env": "dev",
    },
    "mmm-app-training": {
        "service": "Cloud Run",
        "resource_filter": "mmm-app-training",
        "type": "training",
        "env": "prod",
    },
}


def get_date_range(days_back: int) -> Tuple[str, str]:
    """Calculate date range for cost query."""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)
    return start_date.isoformat(), end_date.isoformat()


def build_cost_query(start_date: str, end_date: str, project_id: str) -> str:
    """Build BigQuery SQL query for cost data."""
    query = f"""
    WITH resource_costs AS (
      SELECT
        DATE(_PARTITIONTIME) as usage_date,
        service.description as service_name,
        sku.description as sku_description,
        resource.name as resource_full_name,
        REGEXP_EXTRACT(resource.name, r'/([^/]+)$') as resource_name,
        labels.value as label_value,
        labels.key as label_key,
        SUM(cost) as cost,
        SUM(IFNULL((
          SELECT SUM(c.amount)
          FROM UNNEST(credits) c
        ), 0)) as credits,
        SUM(cost) + SUM(IFNULL((
          SELECT SUM(c.amount)
          FROM UNNEST(credits) c
        ), 0)) as total_cost,
        usage.amount as usage_amount,
        usage.unit as usage_unit
      FROM `{project_id}.{BILLING_DATASET}.{TABLE_NAME}`
      LEFT JOIN UNNEST(labels) as labels
      WHERE
        DATE(_PARTITIONTIME) >= '{start_date}'
        AND DATE(_PARTITIONTIME) <= '{end_date}'
        AND project.id = '{project_id}'
        AND (
          -- Cloud Run services (multiple possible descriptions)
          service.description LIKE '%Cloud Run%'
          -- Artifact Registry
          OR service.description LIKE '%Artifact Registry%'
          -- Cloud Storage
          OR service.description LIKE '%Storage%'
          OR service.description LIKE '%Cloud Storage%'
          -- Cloud Scheduler
          OR service.description LIKE '%Scheduler%'
          OR service.description LIKE '%Cloud Scheduler%'
          -- Secret Manager
          OR service.description LIKE '%Secret Manager%'
          -- Cloud Build (for GitHub Actions)
          OR service.description LIKE '%Cloud Build%'
          -- Also catch by SKU description
          OR sku.description LIKE '%Cloud Run%'
          OR sku.description LIKE '%Artifact Registry%'
          OR sku.description LIKE '%Cloud Storage%'
          OR sku.description LIKE '%Cloud Scheduler%'
          OR sku.description LIKE '%Secret Manager%'
          OR sku.description LIKE '%Cloud Build%'
          -- Catch resources that match our service names
          OR resource.name LIKE '%mmm-app%'
          OR resource.name LIKE '%robyn-queue%'
          OR resource.name LIKE '%sf-private-key%'
          OR resource.name LIKE '%github%'
        )
      GROUP BY 
        usage_date, 
        service_name, 
        sku_description,
        resource_full_name,
        resource_name,
        label_key,
        label_value,
        usage_amount,
        usage_unit
    )
    SELECT
      usage_date,
      service_name,
      sku_description,
      resource_full_name,
      resource_name,
      label_key,
      label_value,
      SUM(cost) as cost,
      SUM(credits) as credits,
      SUM(total_cost) as total_cost,
      ANY_VALUE(usage_amount) as usage_amount,
      ANY_VALUE(usage_unit) as usage_unit
    FROM resource_costs
    GROUP BY 
      usage_date,
      service_name,
      sku_description,
      resource_full_name,
      resource_name,
      label_key,
      label_value
    ORDER BY usage_date DESC, total_cost DESC
    """
    return query


def categorize_cost(sku_description: str, resource_name: Optional[str]) -> str:
    """Categorize costs by type (user requests, scheduler, registry, etc)."""
    sku_lower = sku_description.lower()

    # Cloud Run request costs (check first for specificity)
    if "request" in sku_lower or "invocation" in sku_lower:
        # Try to determine if scheduler or user based on patterns
        if resource_name:
            resource_lower = resource_name.lower()
            if (
                "queue" in resource_lower
                or "scheduler" in resource_lower
                or "tick" in resource_lower
            ):
                return "scheduler_requests"
        return "user_requests"

    # Cloud Run CPU/Memory costs
    if "cpu" in sku_lower or "vcpu" in sku_lower:
        return "compute_cpu"

    if "memory" in sku_lower or "ram" in sku_lower:
        return "compute_memory"

    # Artifact Registry costs
    if "artifact" in sku_lower or "registry" in sku_lower:
        return "registry"

    # Cloud Storage costs
    if "storage" in sku_lower and "artifact" not in sku_lower:
        return "storage"

    if "gcs" in sku_lower or "bucket" in sku_lower:
        return "storage"

    # Cloud Scheduler costs (the service itself, not the invocations)
    # Note: Cloud Scheduler base service fee is $0.10/month per job
    # This is separate from invocation costs which are categorized as requests
    if "scheduler" in sku_lower and "job" in sku_lower:
        return "scheduler_service"
    if "cron" in sku_lower:
        return "scheduler_service"

    # Networking costs
    if (
        "network" in sku_lower
        or "egress" in sku_lower
        or "ingress" in sku_lower
    ):
        return "networking"

    # Secret Manager costs
    if "secret" in sku_lower or "secret manager" in sku_lower:
        return "secrets"

    # Cloud Build costs (GitHub Actions workflows)
    if "build" in sku_lower or "cloud build" in sku_lower:
        return "github_actions"

    # Default category
    return "other"


def identify_service(
    resource_name: Optional[str],
    sku_description: str,
    label_key: Optional[str] = None,
    label_value: Optional[str] = None,
    resource_full_name: Optional[str] = None,
) -> Optional[str]:
    """Identify which MMM service this cost belongs to."""

    # Check labels first (Cloud Run services often have service name in labels)
    if label_key and label_value:
        label_key_lower = label_key.lower()
        label_value_lower = label_value.lower()

        # Check for service name in labels
        if "service" in label_key_lower or "service_name" in label_key_lower:
            if "mmm-app-dev-web" in label_value_lower:
                return "mmm-app-dev-web"
            if "mmm-app-web" in label_value_lower:
                return "mmm-app-web"
            if "mmm-app-dev-training" in label_value_lower:
                return "mmm-app-dev-training"
            if "mmm-app-training" in label_value_lower:
                return "mmm-app-training"

        # Check label value directly for service names
        if "mmm-app-dev-web" in label_value_lower:
            return "mmm-app-dev-web"
        if "mmm-app-web" in label_value_lower:
            return "mmm-app-web"
        if "mmm-app-dev-training" in label_value_lower:
            return "mmm-app-dev-training"
        if "mmm-app-training" in label_value_lower:
            return "mmm-app-training"

        # Check for scheduler in labels
        if "robyn-queue-tick-dev" in label_value_lower:
            return "mmm-app-dev-web"
        if (
            "robyn-queue-tick" in label_value_lower
            and "dev" not in label_value_lower
        ):
            return "mmm-app-web"

    # Check full resource name (before extraction)
    if resource_full_name:
        resource_full_lower = resource_full_name.lower()
        if "mmm-app-dev-web" in resource_full_lower:
            return "mmm-app-dev-web"
        if "mmm-app-web" in resource_full_lower:
            return "mmm-app-web"
        if "mmm-app-dev-training" in resource_full_lower:
            return "mmm-app-dev-training"
        if "mmm-app-training" in resource_full_lower:
            return "mmm-app-training"
        if "robyn-queue-tick-dev" in resource_full_lower:
            return "mmm-app-dev-web"
        if "robyn-queue-tick" in resource_full_lower:
            return "mmm-app-web"

    # Check extracted resource name (last segment of path)
    if resource_name:
        resource_lower = resource_name.lower()
        if "mmm-app-dev-web" in resource_lower:
            return "mmm-app-dev-web"
        if "mmm-app-web" in resource_lower:
            return "mmm-app-web"
        if "mmm-app-dev-training" in resource_lower:
            return "mmm-app-dev-training"
        if "mmm-app-training" in resource_lower:
            return "mmm-app-training"
        if "robyn-queue-tick-dev" in resource_lower:
            return "mmm-app-dev-web"
        if "robyn-queue-tick" in resource_lower:
            return "mmm-app-web"

    # For registry and storage, return as shared costs
    if not resource_name and not resource_full_name:
        if "artifact" in sku_description.lower():
            return "registry"
        if "storage" in sku_description.lower():
            return "storage"

    return None


def process_costs(
    query_results: List[Dict[str, Any]], debug: bool = False
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Process query results into structured cost breakdown."""
    # Structure: {date: {service: {category: cost}}}
    costs_by_date: Dict[str, Dict[str, Dict[str, float]]] = {}

    # Debug: Track what we're seeing
    if debug:
        debug_info = {
            "service_names": set(),
            "sku_descriptions": set(),
            "resource_names": set(),
            "unidentified_count": 0,
            "identified_services": {},
        }

    for row in query_results:
        usage_date = str(row.get("usage_date", ""))
        service_name = row.get("service_name", "")
        sku_description = row.get("sku_description", "")
        resource_name = row.get("resource_name")
        resource_full_name = row.get("resource_full_name")
        label_key = row.get("label_key")
        label_value = row.get("label_value")
        total_cost = float(row.get("total_cost", 0))

        # Debug output
        if debug:
            debug_info["service_names"].add(service_name)
            debug_info["sku_descriptions"].add(sku_description)
            if resource_name:
                debug_info["resource_names"].add(resource_name)
            if resource_full_name:
                if "resource_full_names" not in debug_info:
                    debug_info["resource_full_names"] = set()
                debug_info["resource_full_names"].add(resource_full_name)
            if label_key and label_value:
                if "labels" not in debug_info:
                    debug_info["labels"] = set()
                debug_info["labels"].add(f"{label_key}={label_value}")

        # Skip zero or negative costs
        if total_cost <= 0:
            continue

        # Identify which MMM service this belongs to
        mmm_service = identify_service(
            resource_name,
            sku_description,
            label_key,
            label_value,
            resource_full_name,
        )

        # Debug: Track identification results
        if debug:
            if mmm_service:
                if mmm_service not in debug_info["identified_services"]:
                    debug_info["identified_services"][mmm_service] = []
                debug_info["identified_services"][mmm_service].append(
                    {
                        "service_name": service_name,
                        "sku": sku_description,
                        "resource": resource_name,
                        "cost": total_cost,
                    }
                )
            else:
                debug_info["unidentified_count"] += 1

        # Categorize the cost
        cost_category = categorize_cost(sku_description, resource_name)

        # Initialize nested dictionaries
        if usage_date not in costs_by_date:
            costs_by_date[usage_date] = {}

        # Handle shared costs (registry, storage)
        if mmm_service in ["registry", "storage"]:
            # Distribute shared costs across all services
            services_to_update = list(SERVICE_MAPPING.keys())
            cost_per_service = total_cost / len(services_to_update)

            for service in services_to_update:
                if service not in costs_by_date[usage_date]:
                    costs_by_date[usage_date][service] = {}
                if cost_category not in costs_by_date[usage_date][service]:
                    costs_by_date[usage_date][service][cost_category] = 0.0

                costs_by_date[usage_date][service][
                    cost_category
                ] += cost_per_service
        elif mmm_service:
            # Direct service cost
            if mmm_service not in costs_by_date[usage_date]:
                costs_by_date[usage_date][mmm_service] = {}
            if cost_category not in costs_by_date[usage_date][mmm_service]:
                costs_by_date[usage_date][mmm_service][cost_category] = 0.0

            costs_by_date[usage_date][mmm_service][cost_category] += total_cost

    # Print debug information
    if debug:
        print("\n" + "=" * 80)
        print("DEBUG: Billing Data Analysis")
        print("=" * 80)
        print(
            f"\nUnique Service Names Found ({len(debug_info['service_names'])}):"
        )
        for svc in sorted(debug_info["service_names"]):
            print(f"  - {svc}")

        print(
            f"\nUnique SKU Descriptions Found ({len(debug_info['sku_descriptions'])}):"
        )
        for sku in sorted(debug_info["sku_descriptions"])[
            :20
        ]:  # Limit to first 20
            print(f"  - {sku}")
        if len(debug_info["sku_descriptions"]) > 20:
            print(f"  ... and {len(debug_info['sku_descriptions']) - 20} more")

        print(
            f"\nUnique Resource Names Found ({len(debug_info['resource_names'])}):"
        )
        for res in sorted(debug_info["resource_names"])[
            :20
        ]:  # Limit to first 20
            print(f"  - {res}")
        if len(debug_info["resource_names"]) > 20:
            print(f"  ... and {len(debug_info['resource_names']) - 20} more")

        # Show full resource names if available
        if (
            "resource_full_names" in debug_info
            and debug_info["resource_full_names"]
        ):
            print(
                f"\nFull Resource Names Found ({len(debug_info['resource_full_names'])}):"
            )
            for res in sorted(debug_info["resource_full_names"])[
                :10
            ]:  # Limit to first 10
                print(f"  - {res}")
            if len(debug_info["resource_full_names"]) > 10:
                print(
                    f"  ... and {len(debug_info['resource_full_names']) - 10} more"
                )

        # Show labels if available
        if "labels" in debug_info and debug_info["labels"]:
            print(f"\nLabels Found ({len(debug_info['labels'])}):")
            for label in sorted(debug_info["labels"])[:20]:  # Limit to first 20
                print(f"  - {label}")
            if len(debug_info["labels"]) > 20:
                print(f"  ... and {len(debug_info['labels']) - 20} more")

        print(f"\nService Identification Results:")
        print(
            f"  - Successfully identified: {len(debug_info['identified_services'])} service types"
        )
        for service, records in debug_info["identified_services"].items():
            print(f"  - {service}: {len(records)} records")
            # Show first few examples
            for i, rec in enumerate(records[:3]):
                print(
                    f"      Example {i+1}: {rec['service_name']} | {rec['sku'][:60]}... | ${rec['cost']:.4f}"
                )

        print(f"  - Unidentified records: {debug_info['unidentified_count']}")
        print("=" * 80 + "\n")

    return costs_by_date


def format_currency(amount: float) -> str:
    """Format amount as currency."""
    return f"${amount:.2f}"


def calculate_github_actions_costs(days_back: int) -> Dict[str, float]:
    """
    Calculate estimated GitHub Actions costs for repository workflows.

    GitHub Actions costs are external to GCP and based on:
    - Private repo: $0.008 per minute (Linux runners)
    - Weekly cleanup workflow: ~2-5 minutes per run
    - Estimated: $0.016-0.04 per week

    Returns:
        Dictionary with estimated GitHub Actions costs
    """
    # Weekly cleanup workflow runs on Sundays at 2 AM UTC
    # Estimated runtime: 2-5 minutes (use 3.5 min average)
    workflow_minutes_per_run = 3.5
    cost_per_minute = 0.008  # Linux runner cost

    # Calculate number of weeks in the period
    weeks_in_period = days_back / 7.0

    # Estimated cleanup runs (1 per week)
    cleanup_runs = max(1, int(weeks_in_period))

    # Calculate total estimated cost
    cleanup_cost = cleanup_runs * workflow_minutes_per_run * cost_per_minute

    return {
        "cleanup_workflow": cleanup_cost,
        "total": cleanup_cost,
        "cleanup_runs": cleanup_runs,
        "avg_minutes_per_run": workflow_minutes_per_run,
    }


def print_cost_summary(
    costs_by_date: Dict[str, Dict[str, Dict[str, float]]],
    days_back: int,
) -> None:
    """Print formatted cost summary to console."""
    print("=" * 80)
    print(f"Daily Google Cloud Services Cost Report ({days_back} days)")
    print("=" * 80)
    print()

    # Calculate totals
    service_totals: Dict[str, Dict[str, float]] = {}
    grand_total = 0.0

    for date, services in sorted(costs_by_date.items(), reverse=True):
        print(f"Date: {date}")
        print("-" * 80)

        date_total = 0.0
        for service, categories in sorted(services.items()):
            service_total = sum(categories.values())
            date_total += service_total

            if service not in service_totals:
                service_totals[service] = {}

            print(f"  {service}: {format_currency(service_total)}")

            for category, cost in sorted(
                categories.items(), key=lambda x: x[1], reverse=True
            ):
                print(f"    - {category}: {format_currency(cost)}")

                if category not in service_totals[service]:
                    service_totals[service][category] = 0.0
                service_totals[service][category] += cost

        print(f"  Daily Total: {format_currency(date_total)}")
        print()
        grand_total += date_total

    # Print summary totals
    print("=" * 80)
    print("Summary by Service")
    print("=" * 80)
    print()

    # Track scheduler-related costs for special reporting
    total_scheduler_requests = 0.0
    total_scheduler_service = 0.0

    for service, categories in sorted(service_totals.items()):
        service_total = sum(categories.values())
        print(f"{service}: {format_currency(service_total)}")

        for category, cost in sorted(
            categories.items(), key=lambda x: x[1], reverse=True
        ):
            pct = (cost / service_total * 100) if service_total > 0 else 0
            print(f"  - {category}: {format_currency(cost)} ({pct:.1f}%)")

            # Track scheduler costs across all services
            if category == "scheduler_requests":
                total_scheduler_requests += cost
            elif category == "scheduler_service":
                total_scheduler_service += cost
        print()

    # Calculate and display GitHub Actions costs
    github_costs = calculate_github_actions_costs(days_back)

    print("=" * 80)
    print("Cloud Scheduler Costs Breakdown")
    print("=" * 80)
    print()
    print(f"Scheduler Service Fee: {format_currency(total_scheduler_service)}")
    print(f"  (Base service fee: ~$0.10/month per scheduler job)")
    print()
    print(f"Scheduler Invocations: {format_currency(total_scheduler_requests)}")
    print(f"  (Queue tick invocations: ~4,320/month at 10-minute intervals)")
    print()
    scheduler_total = total_scheduler_requests + total_scheduler_service
    print(f"Total Scheduler Costs: {format_currency(scheduler_total)}")
    print()

    print("=" * 80)
    print("GitHub Actions Costs (External - Not in GCP Billing)")
    print("=" * 80)
    print()
    print(f"Weekly Registry Cleanup Workflow:")
    print(f"  - Estimated runs in period: {github_costs['cleanup_runs']}")
    print(
        f"  - Average runtime: {github_costs['avg_minutes_per_run']:.1f} minutes/run"
    )
    print(f"  - Cost per minute: $0.008 (Linux runner)")
    print(
        f"  - Total estimated cost: {format_currency(github_costs['cleanup_workflow'])}"
    )
    print()
    print(
        f"Total GitHub Actions (Estimated): {format_currency(github_costs['total'])}"
    )
    monthly_github = github_costs["total"] * 30 / days_back
    print(f"Monthly Projection: {format_currency(monthly_github)}")
    print()
    print("Note: GitHub Actions costs are charged separately by GitHub,")
    print("      not included in GCP billing export.")
    print()

    print("=" * 80)
    print(f"GCP Grand Total: {format_currency(grand_total)}")
    print(
        f"GCP Daily Average: {format_currency(grand_total / max(days_back, 1))}"
    )
    monthly_gcp = grand_total * 30 / days_back
    print(f"GCP Monthly Projection: {format_currency(monthly_gcp)}")
    print()
    print(
        f"Combined Total (GCP + GitHub Actions): {format_currency(grand_total + github_costs['total'])}"
    )
    print(
        f"Combined Monthly Projection: {format_currency(monthly_gcp + monthly_github)}"
    )
    print("=" * 80)

    # Special section for scheduler and automation costs
    print()
    print("=" * 80)
    print("Scheduler & Automation Costs Breakdown")
    print("=" * 80)
    print()

    scheduler_costs = {
        "scheduler_service": 0.0,
        "scheduler_requests": 0.0,
        "github_actions": 0.0,
    }

    for service, categories in service_totals.items():
        for category, cost in categories.items():
            if category in scheduler_costs:
                scheduler_costs[category] += cost

    total_automation = sum(scheduler_costs.values())

    if total_automation > 0:
        print(
            f"Total Scheduler & Automation Costs: {format_currency(total_automation)}"
        )
        print(
            f"Monthly Projection: {format_currency(total_automation * 30 / days_back)}"
        )
        print()
        print("Breakdown:")
        print(
            f"  - Scheduler Service Fee: {format_currency(scheduler_costs['scheduler_service'])}"
        )
        print(
            f"    (Base Cloud Scheduler service charge, ~$0.10/month per job)"
        )
        print(
            f"  - Scheduler Invocations: {format_currency(scheduler_costs['scheduler_requests'])}"
        )
        print(f"    (Cloud Run container time for queue processing)")
        print(
            f"  - GitHub Actions (CI/CD): {format_currency(scheduler_costs['github_actions'])}"
        )
        print(f"    (Artifact Registry cleanup and other automation)")
        print()
        print("Notes:")
        print("  - Scheduler runs every 10 minutes (4,320 invocations/month)")
        print("  - Artifact cleanup runs weekly via GitHub Actions")
        print("  - These are automated operational costs")
    else:
        print("No scheduler or automation costs found in this period.")
        print("(May not appear in short time periods)")

    print("=" * 80)


def export_to_csv(
    costs_by_date: Dict[str, Dict[str, Dict[str, float]]],
    output_file: str,
    days_back: int,
) -> None:
    """Export cost data to CSV file, including GitHub Actions costs."""
    rows = []

    for date, services in sorted(costs_by_date.items(), reverse=True):
        for service, categories in sorted(services.items()):
            for category, cost in sorted(categories.items()):
                rows.append(
                    {
                        "date": date,
                        "service": service,
                        "category": category,
                        "cost": cost,
                        "source": "GCP",
                    }
                )

    # Add GitHub Actions costs as separate entries
    github_costs = calculate_github_actions_costs(days_back)
    # Distribute costs evenly across days
    daily_github_cost = github_costs["total"] / days_back

    for date in sorted(costs_by_date.keys(), reverse=True):
        rows.append(
            {
                "date": date,
                "service": "github-actions",
                "category": "cleanup_workflow",
                "cost": daily_github_cost,
                "source": "GitHub",
            }
        )

    if not rows:
        print(f"No data to export to {output_file}")
        return

    with open(output_file, "w", newline="") as csvfile:
        fieldnames = ["date", "service", "category", "cost", "source"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Cost data exported to: {output_file}")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Track daily Google Cloud costs for MMM Trainer"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back (default: 30)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output CSV file path (optional)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=PROJECT_ID,
        help=f"GCP Project ID (default: {PROJECT_ID})",
    )
    parser.add_argument(
        "--use-user-credentials",
        action="store_true",
        help="Use user credentials from 'gcloud auth' instead of GOOGLE_APPLICATION_CREDENTIALS",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output to diagnose cost categorization issues",
    )
    args = parser.parse_args()

    print(f"Fetching cost data for project: {args.project}")
    print(f"Date range: Last {args.days} days")
    print()

    # Check for GOOGLE_APPLICATION_CREDENTIALS environment variable
    gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac_path and not args.use_user_credentials:
        print("=" * 70)
        print("⚠️  WARNING: GOOGLE_APPLICATION_CREDENTIALS is set")
        print("=" * 70)
        print(f"Path: {gac_path}")
        print()
        print("This environment variable takes priority over user credentials.")
        print("The script will use the service account in this file,")
        print("NOT your user credentials from 'gcloud auth'.")
        print()
        print("If you're getting permission errors:")
        print("1. Use the --use-user-credentials flag:")
        print(
            f"   python scripts/track_daily_costs.py --days {args.days} --use-user-credentials"
        )
        print()
        print("2. Or unset this variable:")
        print("   unset GOOGLE_APPLICATION_CREDENTIALS")
        print()
        print("3. Or verify the service account has these permissions:")
        print("  - roles/bigquery.user (project level)")
        print("  - roles/bigquery.dataViewer (dataset level)")
        print("=" * 70)
        print()

    # Get date range
    start_date, end_date = get_date_range(args.days)

    # Initialize BigQuery client
    try:
        # If user wants to use their credentials, temporarily unset GAC
        if args.use_user_credentials and gac_path:
            print(
                "Using user credentials (ignoring GOOGLE_APPLICATION_CREDENTIALS)"
            )
            print()
            # Temporarily remove the environment variable for this process
            saved_gac = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            try:
                client = bigquery.Client(project=args.project)
            finally:
                # Restore it so other code in the process isn't affected
                if saved_gac:
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved_gac
        else:
            client = bigquery.Client(project=args.project)
    except Exception as e:
        print(f"Error: Failed to initialize BigQuery client: {e}")
        print(
            "Make sure you have run: " "gcloud auth application-default login"
        )
        sys.exit(1)

    # Build and execute query
    query = build_cost_query(start_date, end_date, args.project)

    try:
        print("Querying BigQuery billing export...")
        query_job = client.query(query)
        results = list(query_job)
        print(f"Retrieved {len(results)} billing records")
        print()
    except gcp_exceptions.NotFound:
        print(
            f"Error: Billing export table not found: "
            f"{args.project}.{BILLING_DATASET}.{TABLE_NAME}"
        )
        print()
        print("To enable BigQuery billing export:")
        print("1. Go to: https://console.cloud.google.com/billing")
        print("2. Select your billing account")
        print("3. Go to 'Billing export' → 'BigQuery export'")
        print("4. Configure dataset and table")
        print("5. Wait 24 hours for data to populate")
        sys.exit(1)
    except gcp_exceptions.Forbidden as e:
        print(f"Error: Permission denied when accessing BigQuery")
        print()
        print("You need the following IAM permissions:")
        print("  - bigquery.jobs.create (to run queries)")
        print("  - bigquery.tables.getData (to read billing data)")
        print()

        # Check if GOOGLE_APPLICATION_CREDENTIALS is set
        gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path:
            print("=" * 70)
            print("🔍 DETECTED ISSUE: GOOGLE_APPLICATION_CREDENTIALS is set")
            print("=" * 70)
            print(f"Path: {gac_path}")
            print()
            print("This is likely causing your permission error!")
            print()
            print("The script is using the SERVICE ACCOUNT in this file,")
            print("NOT your user credentials from 'gcloud auth'.")
            print()
            print("SOLUTION - Choose one:")
            print()
            print("Option 1: Use the --use-user-credentials flag (recommended)")
            print(
                f"   python scripts/track_daily_costs.py --days {args.days} --use-user-credentials"
            )
            print(
                "   (This keeps GOOGLE_APPLICATION_CREDENTIALS set for other tools)"
            )
            print()
            print("Option 2: Temporarily unset the environment variable")
            print("   unset GOOGLE_APPLICATION_CREDENTIALS")
            print(f"   python scripts/track_daily_costs.py --days {args.days}")
            print()
            print("Option 3: Grant the service account permissions")
            print(
                "  gcloud projects add-iam-policy-binding " f"{args.project} \\"
            )
            print(f'    --member="serviceAccount:{{SA_EMAIL}}" \\')
            print('    --role="roles/bigquery.user"')
            print("=" * 70)
            print()

        # Regular troubleshooting steps
        print("=" * 70)
        print("TROUBLESHOOTING STEPS:")
        print("=" * 70)
        print()

        print("Step 1: Verify your current permissions")
        print("  Run this to check your roles:")
        print(f"    gcloud projects get-iam-policy {args.project} \\")
        print('      --flatten="bindings[].members" \\')
        print('      --filter="bindings.members:user:YOUR_EMAIL@example.com"')
        print()

        print("Step 2: If you just granted permissions, WAIT 2-5 MINUTES")
        print("  IAM permissions can take time to propagate.")
        print("  After waiting, try the script again.")
        print()

        print("Step 3: Grant dataset-level permissions (if not already done)")
        print("  Project-level permissions may not be enough. Try:")
        print(
            f"    bq show --format=prettyjson {args.project}:{BILLING_DATASET}"
        )
        print("  If that fails, grant dataset access via Console:")
        print(
            f"    https://console.cloud.google.com/bigquery?"
            f"project={args.project}&ws=!1m4!1m3!3m2!1s{args.project}!2s{BILLING_DATASET}"
        )
        print()

        print("Step 4: Clear cached credentials and re-authenticate")
        print("  gcloud auth application-default revoke")
        print("  gcloud auth application-default login")
        print("  (Then wait 1-2 minutes before trying again)")
        print()

        print("Step 5: If still not working, grant these specific roles:")
        print("  Option A - Project level (BigQuery User role):")
        print("  gcloud projects add-iam-policy-binding " f"{args.project} \\")
        print('    --member="user:YOUR_EMAIL@example.com" \\')
        print('    --role="roles/bigquery.user"')
        print()
        print("  Option B - Dataset level (via Console):")
        print(f"    1. Go to BigQuery Console")
        print(f"    2. Find dataset: {BILLING_DATASET}")
        print('    3. Click "Share" → "Permissions"')
        print('    4. Add your user with "BigQuery Data Viewer" role')
        print()

        print("Step 6: Check for organization policies")
        print("  Your organization may have policies blocking access.")
        print("  Contact your GCP administrator if above steps don't work.")
        print()

        print("=" * 70)
        print(f"Original error: {e}")
        print("=" * 70)
        sys.exit(1)
    except gcp_exceptions.PermissionDenied as e:
        print(f"Error: Permission denied when accessing BigQuery")
        print()
        print("You need the following IAM permissions:")
        print("  - bigquery.jobs.create (to run queries)")
        print("  - bigquery.tables.getData (to read billing data)")
        print()
        print("Please ask your GCP administrator to grant you access")
        print(f"to the billing dataset: {BILLING_DATASET}")
        print()
        print(f"Original error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error querying BigQuery: {e}")
        print()
        print("Troubleshooting steps:")
        print("1. Verify authentication:")
        print("   gcloud auth application-default login")
        print()
        print("2. Check project access:")
        print(f"   gcloud projects describe {args.project}")
        print()
        print("3. Verify billing export table exists:")
        print(f"   bq show {args.project}:{BILLING_DATASET}.{TABLE_NAME}")
        sys.exit(1)

    if not results:
        print(
            "No billing data found for the specified date range. "
            "This could mean:"
        )
        print("  - No costs were incurred during this period")
        print("  - Billing export is not properly configured")
        print("  - Data has not yet been exported " "(can take up to 24 hours)")
        sys.exit(0)

    # Convert results to list of dicts
    results_list = [dict(row) for row in results]

    print(f"Retrieved {len(results_list)} billing records")
    print()

    # Process the results into structured costs
    costs_by_date = process_costs(results_list, debug=args.debug)

    # Output results
    if args.json:
        # JSON output
        print(json.dumps(costs_by_date, indent=2, default=str))
    else:
        # Console output
        print_cost_summary(costs_by_date, args.days)

    # Export to CSV if requested
    if args.output:
        export_to_csv(costs_by_date, args.output, args.days)


if __name__ == "__main__":
    main()
