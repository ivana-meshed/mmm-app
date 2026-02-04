#!/bin/bash

# Track deployment frequency for Cloud Run services
# This helps identify deployment churn costs

set -e

PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
DAYS_BACK="${DAYS_BACK:-30}"

echo "========================================"
echo "Cloud Run Deployment Frequency Tracker"
echo "========================================"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Period: Last $DAYS_BACK days"
echo ""

# Calculate start date
if date -v-1d > /dev/null 2>&1; then
  # BSD date (macOS)
  START_DATE=$(date -u -v-${DAYS_BACK}d +"%Y-%m-%dT%H:%M:%SZ")
else
  # GNU date (Linux)
  START_DATE=$(date -u -d "$DAYS_BACK days ago" +"%Y-%m-%dT%H:%M:%SZ")
fi

# Services to track
SERVICES=("mmm-app-web" "mmm-app-dev-web")

total_deployments=0

for SERVICE in "${SERVICES[@]}"; do
  echo "Analyzing: $SERVICE"
  echo "----------------------------------------"
  
  # Check if service exists
  if ! gcloud run services describe $SERVICE \
    --region=$REGION \
    --project=$PROJECT_ID \
    > /dev/null 2>&1; then
    echo "  Service not found, skipping"
    echo ""
    continue
  fi
  
  # Get all revisions in period
  revisions=$(gcloud run revisions list \
    --service=$SERVICE \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(metadata.name,metadata.creationTimestamp)" \
    --filter="metadata.creationTimestamp>=$START_DATE" \
    --limit=1000 2>/dev/null || echo "")
  
  if [ -z "$revisions" ]; then
    echo "  No revisions found in period"
    echo ""
    continue
  fi
  
  # Count revisions
  count=$(echo "$revisions" | wc -l | tr -d ' ')
  total_deployments=$((total_deployments + count))
  
  # Calculate per-day average
  per_day=$(echo "scale=1; $count / $DAYS_BACK" | bc)
  per_month=$(echo "scale=0; $count * 30 / $DAYS_BACK" | bc)
  
  echo "  Deployments in last $DAYS_BACK days: $count"
  echo "  Average per day: $per_day"
  echo "  Projected per month: $per_month"
  
  # Show recent revisions
  echo ""
  echo "  Recent deployments:"
  echo "$revisions" | head -n 5 | while IFS=$'\t' read -r name timestamp; do
    echo "    • $timestamp - $name"
  done
  
  echo ""
done

echo "========================================"
echo "Summary"
echo "========================================"
echo "Total deployments: $total_deployments"

avg_per_day=$(echo "scale=1; $total_deployments / $DAYS_BACK" | bc)
proj_per_month=$(echo "scale=0; $total_deployments * 30 / $DAYS_BACK" | bc)

echo "Average per day: $avg_per_day"
echo "Projected per month: $proj_per_month"
echo ""

# Cost estimation
echo "========================================"
echo "Cost Impact Estimation"
echo "========================================"
echo "Deployment overlap assumptions:"
echo "  • Average overlap: 2-4 hours per deployment"
echo "  • Both old and new revisions running"
echo "  • Extra cost per deployment: €0.75-1.50"
echo ""

cost_low=$(echo "scale=2; $proj_per_month * 0.75" | bc)
cost_high=$(echo "scale=2; $proj_per_month * 1.50" | bc)

echo "Estimated deployment overhead:"
echo "  Conservative: €$cost_low/month"
echo "  Moderate: €$cost_high/month"
echo ""

# Recommendations
if [ "$proj_per_month" -gt 50 ]; then
  echo "⚠️  WARNING: High deployment frequency detected!"
  echo ""
  echo "Recommendations:"
  echo "  1. Review CI/CD triggers - deploy only on PR merge"
  echo "  2. Use local development for testing"
  echo "  3. Implement deployment approval workflow"
  echo "  4. Target: 20-30 deployments/month"
  echo ""
  echo "Potential savings: €$(echo "scale=0; ($proj_per_month - 30) * 1.00" | bc)/month"
elif [ "$proj_per_month" -gt 30 ]; then
  echo "ℹ️  Moderate deployment frequency"
  echo "  Consider optimizing to 20-30/month for cost efficiency"
else
  echo "✓ Deployment frequency is optimal"
fi

echo ""
echo "For detailed analysis, see:"
echo "  • DEPLOYMENT_COST_ANALYSIS.md"
echo "  • DEPLOYMENT_OPTIMIZATION_GUIDE.md"
