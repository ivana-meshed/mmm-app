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
- Scheduler requests costs
- Registry costs
- Storage costs
- Other relevant costs

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
          service.description IN ('Cloud Run', 'Artifact Registry', 'Cloud Storage', 'Cloud Scheduler')
          OR sku.description LIKE '%Cloud Run%'
        )
      GROUP BY 
        usage_date, 
        service_name, 
        sku_description, 
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
      resource_name,
      label_key,
      label_value
    ORDER BY usage_date DESC, total_cost DESC
    """
    return query


def categorize_cost(sku_description: str, resource_name: Optional[str]) -> str:
    """Categorize costs by type (user requests, scheduler, registry, etc)."""
    sku_lower = sku_description.lower()

    # Artifact Registry costs
    if "artifact" in sku_lower or "registry" in sku_lower:
        return "registry"

    # Cloud Storage costs
    if "storage" in sku_lower or "gcs" in sku_lower:
        return "storage"

    # Cloud Scheduler costs
    if "scheduler" in sku_lower or "cron" in sku_lower:
        return "scheduler_service"

    # Cloud Run request costs
    if "request" in sku_lower or "invocation" in sku_lower:
        # Try to determine if scheduler or user based on patterns
        if resource_name and "queue" in resource_name.lower():
            return "scheduler_requests"
        return "user_requests"

    # Cloud Run CPU/Memory costs
    if "cpu" in sku_lower or "vcpu" in sku_lower:
        return "compute_cpu"

    if "memory" in sku_lower or "ram" in sku_lower:
        return "compute_memory"

    # Default category
    return "other"


def identify_service(
    resource_name: Optional[str], sku_description: str
) -> Optional[str]:
    """Identify which MMM service this cost belongs to."""
    if not resource_name:
        # For registry and storage, check SKU description
        if "artifact" in sku_description.lower():
            return "registry"
        if "storage" in sku_description.lower():
            return "storage"
        return None

    resource_lower = resource_name.lower()

    # Check for exact matches first
    if "mmm-app-dev-web" in resource_lower:
        return "mmm-app-dev-web"
    if "mmm-app-web" in resource_lower:
        return "mmm-app-web"
    if "mmm-app-dev-training" in resource_lower:
        return "mmm-app-dev-training"
    if "mmm-app-training" in resource_lower:
        return "mmm-app-training"

    # Check for scheduler names
    if "robyn-queue-tick-dev" in resource_lower:
        return "mmm-app-dev-web"  # Dev scheduler costs go to dev web
    if "robyn-queue-tick" in resource_lower:
        return "mmm-app-web"  # Prod scheduler costs go to prod web

    return None


def process_costs(
    query_results: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Process query results into structured cost breakdown."""
    # Structure: {date: {service: {category: cost}}}
    costs_by_date: Dict[str, Dict[str, Dict[str, float]]] = {}

    for row in query_results:
        usage_date = str(row.get("usage_date", ""))
        service_name = row.get("service_name", "")
        sku_description = row.get("sku_description", "")
        resource_name = row.get("resource_name")
        total_cost = float(row.get("total_cost", 0))

        # Skip zero or negative costs
        if total_cost <= 0:
            continue

        # Identify which MMM service this belongs to
        mmm_service = identify_service(resource_name, sku_description)

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

    return costs_by_date


def format_currency(amount: float) -> str:
    """Format amount as currency."""
    return f"${amount:.2f}"


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

    for service, categories in sorted(service_totals.items()):
        service_total = sum(categories.values())
        print(f"{service}: {format_currency(service_total)}")

        for category, cost in sorted(
            categories.items(), key=lambda x: x[1], reverse=True
        ):
            pct = (cost / service_total * 100) if service_total > 0 else 0
            print(f"  - {category}: {format_currency(cost)} ({pct:.1f}%)")
        print()

    print("=" * 80)
    print(f"Grand Total: {format_currency(grand_total)}")
    print(f"Daily Average: {format_currency(grand_total / max(days_back, 1))}")
    print(
        f"Monthly Projection: {format_currency(grand_total * 30 / days_back)}"
    )
    print("=" * 80)


def export_to_csv(
    costs_by_date: Dict[str, Dict[str, Dict[str, float]]],
    output_file: str,
) -> None:
    """Export cost data to CSV file."""
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
                    }
                )

    if not rows:
        print(f"No data to export to {output_file}")
        return

    with open(output_file, "w", newline="") as csvfile:
        fieldnames = ["date", "service", "category", "cost"]
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
    args = parser.parse_args()

    print(f"Fetching cost data for project: {args.project}")
    print(f"Date range: Last {args.days} days")
    print()

    # Check for GOOGLE_APPLICATION_CREDENTIALS environment variable
    gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac_path:
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
        print("1. Unset this variable:")
        print("   unset GOOGLE_APPLICATION_CREDENTIALS")
        print()
        print("2. Then run the script again")
        print()
        print("Or verify the service account has these permissions:")
        print("  - roles/bigquery.user (project level)")
        print("  - roles/bigquery.dataViewer (dataset level)")
        print("=" * 70)
        print()

    # Get date range
    start_date, end_date = get_date_range(args.days)

    # Initialize BigQuery client
    try:
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
            print("SOLUTION:")
            print("1. Unset the environment variable:")
            print("   unset GOOGLE_APPLICATION_CREDENTIALS")
            print()
            print("2. Run the script again:")
            print(f"   python scripts/track_daily_costs.py --days {args.days}")
            print()
            print("The script will then use your user credentials which have")
            print("the correct permissions.")
            print()
            print("Alternative: If you want to use the service account,")
            print("grant it these roles:")
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

    # Process costs
    costs_by_date = process_costs(results_list)

    # Output results
    if args.json:
        # JSON output
        print(json.dumps(costs_by_date, indent=2, default=str))
    else:
        # Console output
        print_cost_summary(costs_by_date, args.days)

    # Export to CSV if requested
    if args.output:
        export_to_csv(costs_by_date, args.output)


if __name__ == "__main__":
    main()
