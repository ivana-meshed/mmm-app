#!/usr/bin/env python3
"""
Deep-dive analysis of Cloud Run idle costs.

This script investigates why Cloud Run services incur costs even when idle
(no user requests). It analyzes:
1. Instance hours vs request patterns
2. CPU throttling impact
3. Scheduler-triggered wake-ups
4. Cost breakdown by service configuration
5. Recommendations for cost optimization

Includes analysis of:
- Scheduler costs (service fees and invocations)
- GitHub Actions costs (CI/CD and weekly cleanup)
- Artifact Registry cleanup automation

Usage:
    python scripts/analyze_idle_costs.py --days 7
    python scripts/analyze_idle_costs.py --days 30 --service mmm-app-web
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery

# Configuration
PROJECT_ID = os.getenv("PROJECT_ID", "datawarehouse-422511")
BILLING_DATASET = os.environ.get("BILLING_DATASET", "mmm_billing")
BILLING_ACCOUNT_NUM = os.environ.get(
    "BILLING_ACCOUNT_NUM", "01B2F0_BCBFB7_2051C5"
)
TABLE_NAME = f"gcp_billing_export_resource_v1_{BILLING_ACCOUNT_NUM}"

# Cloud Run pricing (europe-west1, as of 2026)
# Source: https://cloud.google.com/run/pricing
CPU_PRICE_PER_VCPU_SECOND = 0.00002400  # $0.024 / vCPU-hour
MEMORY_PRICE_PER_GB_SECOND = 0.00000250  # $0.0025 / GB-hour
REQUEST_PRICE = 0.00000040  # $0.40 per million requests

# Service configurations (from terraform)
# NOTE: These should match the actual Terraform configuration in infra/terraform/main.tf
SERVICE_CONFIGS = {
    "mmm-app-web": {
        "cpu": 1.0,  # vCPU allocated
        "memory": 2.0,  # GB allocated
        "throttling": True,  # CPU throttling enabled (as of Feb 2026)
        "min_instances": 0,
        "scheduler_interval": None,  # Scheduler DISABLED for prod (cost optimization)
    },
    "mmm-app-dev-web": {
        "cpu": 1.0,
        "memory": 2.0,
        "throttling": True,  # CPU throttling enabled (as of Feb 2026)
        "min_instances": 0,
        "scheduler_interval": 30,  # Scheduler runs every 30 minutes in dev
    },
    "mmm-app-training": {
        "cpu": 8.0,
        "memory": 32.0,
        "throttling": True,  # Jobs always have CPU allocated during execution
        "min_instances": 0,
        "scheduler_interval": None,  # No scheduler for jobs
    },
    "mmm-app-dev-training": {
        "cpu": 8.0,
        "memory": 32.0,
        "throttling": True,
        "min_instances": 0,
        "scheduler_interval": None,
    },
}


def get_credentials():
    """Get BigQuery client with appropriate credentials."""
    # Check if GOOGLE_APPLICATION_CREDENTIALS is set
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac:
        print(
            f"\n⚠️  WARNING: GOOGLE_APPLICATION_CREDENTIALS is set to: {gac}"
        )
        print(
            "The script will use this service account. "
            "Use --use-user-credentials to override.\n"
        )

    client = bigquery.Client(project=PROJECT_ID)
    return client


def build_analysis_query(
    start_date: str, end_date: str, service_filter: Optional[str] = None
) -> str:
    """Build BigQuery query for detailed cost analysis."""
    service_condition = ""
    if service_filter:
        service_condition = (
            f"AND ARRAY_TO_STRING(all_labels, ',') LIKE '%{service_filter}%'"
        )

    query = f"""
    SELECT
      DATE(_PARTITIONTIME) as usage_date,
      TIMESTAMP_TRUNC(_PARTITIONTIME, HOUR) as usage_hour,
      service.description as service_name,
      sku.description as sku_description,
      resource.name as resource_full_name,
      ARRAY_AGG(DISTINCT CONCAT(labels.key, ':', labels.value) IGNORE NULLS) as all_labels,
      SUM(cost) as cost,
      SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) as credits,
      SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) as total_cost,
      SUM(usage.amount) as usage_amount,
      ANY_VALUE(usage.unit) as usage_unit
    FROM `{PROJECT_ID}.{BILLING_DATASET}.{TABLE_NAME}`
    LEFT JOIN UNNEST(labels) as labels
    WHERE
      DATE(_PARTITIONTIME) >= '{start_date}'
      AND DATE(_PARTITIONTIME) <= '{end_date}'
      AND project.id = '{PROJECT_ID}'
      AND (
        service.description LIKE '%Cloud Run%'
        OR sku.description LIKE '%Cloud Run%'
        OR service.description LIKE '%Artifact Registry%'
        OR service.description LIKE '%Cloud Storage%'
        OR service.description LIKE '%Scheduler%'
        OR service.description LIKE '%Secret Manager%'
        OR service.description LIKE '%Cloud Build%'
        OR sku.description LIKE '%Scheduler%'
        OR sku.description LIKE '%Secret Manager%'
        OR sku.description LIKE '%Cloud Build%'
        OR resource.name LIKE '%mmm-app%'
        OR resource.name LIKE '%robyn-queue%'
        OR resource.name LIKE '%sf-private-key%'
        OR resource.name LIKE '%github%'
      )
      {service_condition}
    GROUP BY 
      usage_date,
      usage_hour,
      service_name,
      sku_description,
      resource_full_name
    ORDER BY usage_date DESC, usage_hour DESC, total_cost DESC
    """
    return query


def identify_service_from_labels(
    all_labels: List[str], resource_full_name: Optional[str] = None
) -> Optional[str]:
    """Identify MMM service from labels and resource name."""
    # Check all labels for service names
    labels_str = " ".join(all_labels).lower() if all_labels else ""
    
    # Order matters - check dev services before prod to avoid matching "mmm-app" in "mmm-app-dev"
    if "mmm-app-dev-web" in labels_str:
        return "mmm-app-dev-web"
    if "mmm-app-dev-training" in labels_str:
        return "mmm-app-dev-training"
    if "mmm-app-web" in labels_str:
        return "mmm-app-web"
    if "mmm-app-training" in labels_str:
        return "mmm-app-training"
    
    # Fallback to resource name if labels don't match
    if resource_full_name:
        resource_lower = resource_full_name.lower()
        if "mmm-app-dev-web" in resource_lower or "mmm_app_dev_web" in resource_lower:
            return "mmm-app-dev-web"
        if "mmm-app-dev-training" in resource_lower or "mmm_app_dev_training" in resource_lower:
            return "mmm-app-dev-training"
        if "mmm-app-web" in resource_lower or "mmm_app_web" in resource_lower:
            return "mmm-app-web"
        if "mmm-app-training" in resource_lower or "mmm_app_training" in resource_lower:
            return "mmm-app-training"
    
    # For shared resources (storage, registry), distribute equally
    if labels_str or resource_full_name:
        # Check if it's a shared cost (artifact registry, storage)
        return "shared"
    
    return None


def categorize_sku(sku_description: str, resource_name: Optional[str] = None) -> str:
    """Categorize SKU into cost type."""
    sku_lower = sku_description.lower()

    # Check for CPU
    if "cpu" in sku_lower and ("instance" in sku_lower or "job" in sku_lower):
        return "compute_cpu"

    # Check for Memory
    if "memory" in sku_lower and (
        "instance" in sku_lower or "job" in sku_lower
    ):
        return "compute_memory"

    # Cloud Scheduler service costs (the base service fee, not invocations)
    # Note: Cloud Scheduler base service fee is ~$0.10/month per job
    # This is separate from invocation costs which are categorized below
    if "scheduler" in sku_lower and "job" in sku_lower:
        return "scheduler_service"
    if "cron" in sku_lower:
        return "scheduler_service"

    # Check for requests - distinguish between scheduler and user requests
    if "request" in sku_lower or "invocation" in sku_lower:
        # Try to determine if scheduler or user based on resource name patterns
        if resource_name:
            resource_lower = resource_name.lower()
            if (
                "queue" in resource_lower
                or "scheduler" in resource_lower
                or "tick" in resource_lower
                or "robyn-queue-tick" in resource_lower
            ):
                return "scheduler_requests"
        return "user_requests"

    # Check for networking
    if (
        "network" in sku_lower
        or "egress" in sku_lower
        or "ingress" in sku_lower
    ):
        return "networking"

    return "other"


def analyze_costs(
    query_results: List[Dict[str, Any]], days: int
) -> Dict[str, Any]:
    """Analyze billing data to understand idle costs."""
    analysis = {
        "by_service": {},
        "by_date": {},
        "by_hour": {},
        "total_days": days,
        "shared_costs": 0,  # Track shared costs for distribution
    }

    for row in query_results:
        usage_date = str(row.get("usage_date", ""))
        usage_hour = row.get("usage_hour")
        service_name = row.get("service_name", "")
        sku_description = row.get("sku_description", "")
        all_labels = row.get("all_labels", [])
        resource_full_name = row.get("resource_full_name")
        total_cost = float(row.get("total_cost", 0))
        usage_amount = float(row.get("usage_amount", 0))
        usage_unit = row.get("usage_unit", "")

        if total_cost <= 0:
            continue

        # Identify service
        mmm_service = identify_service_from_labels(all_labels, resource_full_name)
        if not mmm_service:
            continue
        
        # Handle shared costs
        if mmm_service == "shared":
            analysis["shared_costs"] += total_cost
            continue

        # Categorize cost
        category = categorize_sku(sku_description, resource_full_name)

        # Aggregate by service
        if mmm_service not in analysis["by_service"]:
            analysis["by_service"][mmm_service] = {
                "total": 0,
                "by_category": {},
                "by_date": {},
                "hours_with_activity": set(),
                "usage_details": [],
            }

        analysis["by_service"][mmm_service]["total"] += total_cost

        if category not in analysis["by_service"][mmm_service]["by_category"]:
            analysis["by_service"][mmm_service]["by_category"][category] = 0

        analysis["by_service"][mmm_service]["by_category"][
            category
        ] += total_cost

        # Track hours with activity
        if usage_hour:
            analysis["by_service"][mmm_service]["hours_with_activity"].add(
                usage_hour
            )

        # Track usage details
        if category in ["compute_cpu", "compute_memory"] and usage_amount > 0:
            analysis["by_service"][mmm_service]["usage_details"].append(
                {
                    "date": usage_date,
                    "hour": str(usage_hour) if usage_hour else "unknown",
                    "category": category,
                    "sku": sku_description,
                    "cost": total_cost,
                    "usage_amount": usage_amount,
                    "usage_unit": usage_unit,
                }
            )

        # Aggregate by date
        if usage_date not in analysis["by_date"]:
            analysis["by_date"][usage_date] = {}

        if mmm_service not in analysis["by_date"][usage_date]:
            analysis["by_date"][usage_date][mmm_service] = 0

        analysis["by_date"][usage_date][mmm_service] += total_cost

    # Distribute shared costs equally across services
    if analysis["shared_costs"] > 0 and analysis["by_service"]:
        num_services = len(analysis["by_service"])
        shared_per_service = analysis["shared_costs"] / num_services
        for service in analysis["by_service"]:
            analysis["by_service"][service]["total"] += shared_per_service
            if "shared" not in analysis["by_service"][service]["by_category"]:
                analysis["by_service"][service]["by_category"]["shared"] = 0
            analysis["by_service"][service]["by_category"]["shared"] += shared_per_service

    # Convert sets to counts
    for service, data in analysis["by_service"].items():
        data["unique_hours_active"] = len(data["hours_with_activity"])
        del data["hours_with_activity"]

    return analysis


def calculate_theoretical_costs(
    service: str, hours_active: int
) -> Dict[str, float]:
    """Calculate theoretical costs based on service configuration."""
    if service not in SERVICE_CONFIGS:
        return {}

    config = SERVICE_CONFIGS[service]
    cpu = config["cpu"]
    memory = config["memory"]

    # Calculate per-hour costs
    cpu_cost_per_hour = cpu * CPU_PRICE_PER_VCPU_SECOND * 3600
    memory_cost_per_hour = memory * MEMORY_PRICE_PER_GB_SECOND * 3600

    # Calculate total for hours active
    total_cpu_cost = cpu_cost_per_hour * hours_active
    total_memory_cost = memory_cost_per_hour * hours_active

    return {
        "cpu_cost_per_hour": cpu_cost_per_hour,
        "memory_cost_per_hour": memory_cost_per_hour,
        "total_cpu_cost": total_cpu_cost,
        "total_memory_cost": total_memory_cost,
        "total_cost": total_cpu_cost + total_memory_cost,
        "hours_active": hours_active,
    }


def print_analysis(analysis: Dict[str, Any], args: argparse.Namespace):
    """Print detailed cost analysis."""
    print("\n" + "=" * 80)
    print("IDLE COST DEEP-DIVE ANALYSIS")
    print("=" * 80)
    print(f"\nAnalysis Period: {args.days} days")
    print(f"Date Range: {args.start_date} to {args.end_date}")

    # Summary by service
    print("\n" + "=" * 80)
    print("COST BREAKDOWN BY SERVICE")
    print("=" * 80)

    for service, data in sorted(analysis["by_service"].items()):
        print(f"\n{service}:")
        print(f"  Total Cost: ${data['total']:.2f}")
        print(f"  Daily Average: ${data['total'] / args.days:.2f}")
        print(
            f"  Monthly Projection: ${data['total'] / args.days * 30:.2f}"
        )
        print(f"  Unique Hours Active: {data['unique_hours_active']}")

        print(f"\n  Cost by Category:")
        for category, cost in sorted(
            data["by_category"].items(), key=lambda x: x[1], reverse=True
        ):
            pct = (cost / data["total"] * 100) if data["total"] > 0 else 0
            print(f"    - {category}: ${cost:.2f} ({pct:.1f}%)")

        # Theoretical cost calculation
        if service in SERVICE_CONFIGS:
            config = SERVICE_CONFIGS[service]
            theoretical = calculate_theoretical_costs(
                service, data["unique_hours_active"]
            )

            print(f"\n  Configuration:")
            print(f"    - CPU: {config['cpu']} vCPU")
            print(f"    - Memory: {config['memory']} GB")
            print(f"    - CPU Throttling: {config['throttling']}")
            print(f"    - Min Instances: {config['min_instances']}")
            if config["scheduler_interval"]:
                print(
                    f"    - Scheduler: Every {config['scheduler_interval']} minutes"
                )

            print(f"\n  Theoretical Costs (based on configuration):")
            print(
                f"    - CPU Cost/Hour: ${theoretical['cpu_cost_per_hour']:.4f}"
            )
            print(
                f"    - Memory Cost/Hour: ${theoretical['memory_cost_per_hour']:.4f}"
            )
            print(
                f"    - Total Cost/Hour: ${theoretical['cpu_cost_per_hour'] + theoretical['memory_cost_per_hour']:.4f}"
            )
            print(
                f"    - Theoretical Total ({data['unique_hours_active']} hours): ${theoretical['total_cost']:.2f}"
            )

            actual_cpu = data["by_category"].get("compute_cpu", 0)
            actual_memory = data["by_category"].get("compute_memory", 0)
            print(f"\n  Actual vs Theoretical:")
            print(
                f"    - CPU: ${actual_cpu:.2f} actual vs ${theoretical['total_cpu_cost']:.2f} theoretical"
            )
            print(
                f"    - Memory: ${actual_memory:.2f} actual vs ${theoretical['total_memory_cost']:.2f} theoretical"
            )

    # Show sample usage details for first service
    print("\n" + "=" * 80)
    print("DETAILED USAGE PATTERNS (Sample)")
    print("=" * 80)

    for service, data in list(analysis["by_service"].items())[:2]:
        if data["usage_details"]:
            print(f"\n{service} - First 10 billing records:")
            for detail in data["usage_details"][:10]:
                print(
                    f"  {detail['date']} {detail['hour']}: {detail['category']} - ${detail['cost']:.4f}"
                )
                print(
                    f"    Usage: {detail['usage_amount']:.2f} {detail['usage_unit']}"
                )

    # Root cause analysis
    print("\n" + "=" * 80)
    print("ROOT CAUSE ANALYSIS")
    print("=" * 80)

    # Check actual configuration from SERVICE_CONFIGS
    web_services = [s for s in analysis["by_service"].keys() if "web" in s]
    has_throttling_enabled = all(
        SERVICE_CONFIGS.get(s, {}).get("throttling", False) for s in web_services
    )
    scheduler_intervals = [
        SERVICE_CONFIGS.get(s, {}).get("scheduler_interval")
        for s in web_services
        if SERVICE_CONFIGS.get(s, {}).get("scheduler_interval")
    ]
    has_scheduler = len(scheduler_intervals) > 0
    scheduler_interval = scheduler_intervals[0] if scheduler_intervals else None

    print("\nCurrent Configuration:")
    print(f"  - CPU throttling: {'ENABLED' if has_throttling_enabled else 'DISABLED'}")
    print(f"  - Scheduler: {'ENABLED' if has_scheduler else 'DISABLED'}")
    if has_scheduler:
        print(f"  - Scheduler interval: {scheduler_interval} minutes")
    print(f"  - Min instances: 0 (scale-to-zero)")

    print("\nWhy are there costs despite min_instances=0?")
    
    if not has_throttling_enabled:
        print("\n1. CPU THROTTLING DISABLED:")
        print("   - Current setting: cpu-throttling = false")
        print(
            "   - Impact: CPU remains allocated even when container is idle"
        )
        print("   - Consequence: You pay for CPU time, not just active request time")
    else:
        print("\n1. CPU THROTTLING:")
        print("   - Current setting: cpu-throttling = true ✓")
        print("   - Impact: CPU only allocated during active requests")
        print("   - This is already optimized")

    if has_scheduler:
        print(f"\n2. SCHEDULER WAKE-UPS:")
        print(f"   - Scheduler pings every {scheduler_interval} minutes")
        print("   - Each ping wakes up an instance")
        print("   - Instance stays warm for several minutes after request")
        wake_ups_per_day = 24 * 60 // scheduler_interval
        print(
            f"   - Result: ~{wake_ups_per_day} wake-ups/day = higher costs even with no user traffic"
        )
    else:
        print("\n2. SCHEDULER:")
        print("   - Scheduler is currently DISABLED")
        print("   - No automated wake-ups from scheduler")

    print("\n3. INSTANCE LIFECYCLE:")
    print("   - Instance starts on first request")
    print("   - Processes request quickly")
    if has_throttling_enabled:
        print("   - With cpu-throttling=true, CPU released when idle")
    else:
        print("   - With cpu-throttling=false, keeps using CPU")
    print(
        "   - Stays warm for ~15 minutes after last request (Cloud Run default)"
    )
    if has_scheduler and scheduler_interval and scheduler_interval <= 15:
        print(
            f"   - With {scheduler_interval}-min scheduler interval, nearly always has a warm instance"
        )

    # Recommendations
    print("\n" + "=" * 80)
    print("COST OPTIMIZATION RECOMMENDATIONS")
    print("=" * 80)

    total_monthly = sum(
        data["total"] / args.days * 30
        for data in analysis["by_service"].values()
    )

    print(f"\nCurrent Monthly Projection: ${total_monthly:.2f}")
    
    # Generate dynamic recommendations based on actual configuration
    recommendations = []
    recommendation_num = 1
    
    # Check CPU throttling
    if not has_throttling_enabled:
        recommendations.append({
            'priority': 'Highest',
            'savings': 80,
            'reduction': 0.7,
            'title': 'ENABLE CPU THROTTLING',
            'change': 'In main.tf: Set "run.googleapis.com/cpu-throttling" = "true"',
            'impact': [
                'Reduces CPU costs by ~80% during idle time',
                'Estimated monthly savings: ~$80-100',
                'Minimal impact on performance (CPU allocated during active requests)'
            ],
            'tradeoffs': [
                'CPU only allocated when actively processing requests',
                'May see slight latency increase for long-running operations',
                'For short web requests (< 1 second), no noticeable difference'
            ]
        })
    
    # Check scheduler interval
    if has_scheduler and scheduler_interval and scheduler_interval < 30:
        current_wakeups = 24 * 60 // scheduler_interval
        new_wakeups = 24 * 60 // 30
        recommendations.append({
            'priority': 'Medium',
            'savings': 30,
            'reduction': 0.3,
            'title': 'INCREASE SCHEDULER INTERVAL',
            'change': f'In main.tf: Change schedule from "*/{"10" if scheduler_interval == 10 else scheduler_interval} * * * *" to "*/{30} * * * *"',
            'impact': [
                f'Reduces wake-ups from {current_wakeups}/day to {new_wakeups}/day',
                'Reduces "always warm" behavior, allowing better scale-to-zero',
                'Estimated monthly savings: ~$20-30'
            ],
            'tradeoffs': [
                f'Training jobs in queue wait up to 30 min instead of {scheduler_interval} min',
                'Acceptable if training is not time-critical'
            ]
        })
    elif has_scheduler and scheduler_interval and scheduler_interval >= 30:
        recommendations.append({
            'priority': 'Low',
            'savings': 50,
            'reduction': 0.5,
            'title': 'DISABLE SCHEDULER (if not needed)',
            'change': 'In prod.tfvars: Set scheduler_enabled = false',
            'impact': [
                'Eliminates all scheduler-triggered wake-ups',
                'Significantly reduces idle costs',
                'Estimated monthly savings: ~$40-60'
            ],
            'tradeoffs': [
                'Training jobs must be triggered manually or via API',
                'No automatic queue processing',
                'May increase operational overhead'
            ]
        })
    
    # Check timeout configuration
    recommendations.append({
        'priority': 'Low',
        'savings': 5,
        'reduction': 0.05,
        'title': 'OPTIMIZE REQUEST TIMEOUT',
        'change': 'In main.tf: Consider reducing timeout from 300s to 120-180s',
        'impact': [
            'Prevents instances from staying allocated for failed/hung requests',
            'Small cost savings: ~$5-10/month',
            'Faster failure detection'
        ],
        'tradeoffs': [
            'Requests taking longer than timeout will be terminated',
            'May need testing to ensure legitimate requests complete',
            'Current 300s (5 min) is reasonable for most operations'
        ]
    })
    
    # Alternative architecture (always shown as informational)
    recommendations.append({
        'priority': 'Informational',
        'savings': 60,
        'reduction': 0.6,
        'title': 'CONSIDER ALTERNATIVE ARCHITECTURE',
        'change': 'Use Cloud Tasks or Pub/Sub + Cloud Functions instead of scheduler',
        'impact': [
            'Only triggers when jobs are actually in queue',
            'No wake-ups when queue is empty',
            'Could reduce idle costs to near-zero',
            'Estimated monthly savings: ~$40-60'
        ],
        'tradeoffs': [
            'Requires architectural changes',
            'More complex to implement and test',
            'Significant development effort'
        ]
    })
    
    if recommendations:
        print("\nRecommendations (in priority order):")
        
        for i, rec in enumerate(recommendations, 1):
            if rec['priority'] == 'Informational':
                continue  # Show informational items last
            print(f"\n{i}. {rec['title']} ({rec['priority']} Priority)")
            print(f"   Change:")
            print(f"   - {rec['change']}")
            print("\n   Expected Impact:")
            for impact in rec['impact']:
                print(f"     - {impact}")
            print("\n   Trade-offs:")
            for tradeoff in rec['tradeoffs']:
                print(f"     - {tradeoff}")
        
        # Show informational items at the end
        info_recs = [r for r in recommendations if r['priority'] == 'Informational']
        if info_recs:
            for rec in info_recs:
                print(f"\n{len(recommendations)}. {rec['title']} ({rec['priority']})")
                print(f"   Options:")
                for impact in rec['impact']:
                    print(f"     - {impact}")
                print("\n   Trade-offs:")
                for tradeoff in rec['tradeoffs']:
                    print(f"     - {tradeoff}")
    else:
        print("\n✓ All major cost optimizations are already implemented!")
        print("  - CPU throttling: ENABLED")
        if has_scheduler:
            print(f"  - Scheduler interval: {scheduler_interval} minutes (reasonable)")
        else:
            print("  - Scheduler: DISABLED (maximum cost savings)")
        print("  - Min instances: 0 (scale-to-zero)")
        print("\nCurrent configuration is well-optimized for cost.")

    # Quick win summary
    actionable_recs = [r for r in recommendations if r['priority'] in ['Highest', 'Medium']]
    if actionable_recs:
        print("\n" + "=" * 80)
        print("QUICK WIN: Implement Top Recommendations")
        print("=" * 80)
        print("\nExpected Results:")
        print(f"  Current monthly cost: ${total_monthly:.2f}")
        
        cumulative_reduction = 1.0
        for rec in actionable_recs:
            cumulative_reduction *= (1 - rec['reduction'])
            print(f"  After {rec['title'].lower()}: ${total_monthly * cumulative_reduction:.2f} ({int((1-cumulative_reduction)*100)}% reduction)")
        
        total_savings = total_monthly * (1 - cumulative_reduction)
        print(f"\n  Total monthly savings: ~${total_savings:.2f} (~${total_savings * 12:.0f}/year)")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Deep-dive analysis of Cloud Run idle costs"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to analyze (default: 7)",
    )
    parser.add_argument(
        "--service",
        type=str,
        help="Filter by specific service (e.g., mmm-app-web)",
    )
    parser.add_argument(
        "--use-user-credentials",
        action="store_true",
        help="Use user credentials instead of GOOGLE_APPLICATION_CREDENTIALS",
    )

    args = parser.parse_args()

    # Calculate date range
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=args.days - 1)
    args.start_date = start_date.strftime("%Y-%m-%d")
    args.end_date = end_date.strftime("%Y-%m-%d")

    print(f"Fetching cost data for project: {PROJECT_ID}")
    print(f"Date range: Last {args.days} days")
    if args.service:
        print(f"Filtering by service: {args.service}")

    # Handle credentials
    saved_gac = None
    if args.use_user_credentials and "GOOGLE_APPLICATION_CREDENTIALS" in os.environ:
        print(
            "\nUsing user credentials (ignoring GOOGLE_APPLICATION_CREDENTIALS)"
        )
        saved_gac = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS")

    try:
        # Create BigQuery client
        client = get_credentials()

        # Build and execute query
        print("\nQuerying BigQuery billing export...")
        query = build_analysis_query(
            args.start_date, args.end_date, args.service
        )

        query_job = client.query(query)
        results = list(query_job.result())

        print(f"Retrieved {len(results)} billing records\n")

        if not results:
            print("No billing data found for the specified period.")
            return

        # Analyze costs
        analysis = analyze_costs(results, args.days)

        # Print analysis
        print_analysis(analysis, args)

    except gcp_exceptions.Forbidden as e:
        print("\nError: Permission denied when accessing BigQuery")
        print(str(e))
    except gcp_exceptions.PermissionDenied as e:
        print("\nError: Permission denied")
        print(str(e))
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Restore environment variable
        if saved_gac:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved_gac


if __name__ == "__main__":
    main()
