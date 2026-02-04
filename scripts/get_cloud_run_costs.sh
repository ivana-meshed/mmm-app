#!/bin/bash

# Complete Cloud Run Cost Analysis Script
# Calculates costs for BOTH training jobs AND web services
#
# This script queries Cloud Run and calculates actual costs
# based on CPU and memory usage for all Cloud Run resources.

set -e

PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
DAYS_BACK="${DAYS_BACK:-30}"

# Pricing (europe-west1)
CPU_RATE=0.000024      # $ per vCPU-second
MEMORY_RATE=0.0000025  # $ per GB-second
REQUEST_RATE=0.0000004 # $ per request

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo "=========================================="
echo "Complete Cloud Run Cost Analysis"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Period: Last $DAYS_BACK days"
echo ""

# Calculate date range
if date -v-1d > /dev/null 2>&1; then
  # BSD date (macOS)
  START_DATE=$(date -u -v-${DAYS_BACK}d +"%Y-%m-%dT%H:%M:%SZ")
else
  # GNU date (Linux)
  START_DATE=$(date -u -d "$DAYS_BACK days ago" +"%Y-%m-%dT%H:%M:%SZ")
fi

TOTAL_TRAINING_COST=0
TOTAL_WEB_COST=0
TOTAL_JOBS=0

##############################################################
# PART 1: Training Jobs (existing logic)
##############################################################
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}PART 1: Training Job Costs${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

JOBS=("mmm-app-training" "mmm-app-dev-training")

for JOB in "${JOBS[@]}"; do
  echo -e "${BLUE}Analyzing: $JOB${NC}"
  echo ""
  
  # Get executions from the last N days
  EXECUTIONS=$(gcloud run jobs executions list \
    --job="$JOB" \
    --region="$REGION" \
    --format="json" \
    --filter="metadata.creationTimestamp>=$START_DATE" 2>/dev/null || echo "[]")
  
  if [ "$EXECUTIONS" = "[]" ] || [ -z "$EXECUTIONS" ]; then
    echo "  No executions found in the last $DAYS_BACK days"
    echo ""
    continue
  fi
  
  # Count executions
  JOB_COUNT=$(echo "$EXECUTIONS" | jq 'length')
  echo "  Total executions: $JOB_COUNT"
  
  if [ "$JOB_COUNT" -eq 0 ]; then
    echo ""
    continue
  fi
  
  # Get job configuration
  JOB_CONFIG=$(gcloud run jobs describe "$JOB" \
    --region="$REGION" \
    --format="json" 2>/dev/null)
  
  # Extract CPU and memory
  CPU=$(echo "$JOB_CONFIG" | jq -r '.spec.template.spec.template.spec.containers[0].resources.limits.cpu // "8"' | sed 's/[^0-9.]//g')
  MEMORY=$(echo "$JOB_CONFIG" | jq -r '.spec.template.spec.template.spec.containers[0].resources.limits.memory // "32Gi"' | sed 's/Gi//g')
  
  echo "  Configuration: ${CPU} vCPU, ${MEMORY} GB"
  
  # Calculate total duration and cost
  TOTAL_DURATION=0
  SUCCESS_COUNT=0
  FAILED_COUNT=0
  
  # Parse each execution
  for i in $(seq 0 $((JOB_COUNT - 1))); do
    EXECUTION=$(echo "$EXECUTIONS" | jq -r ".[$i]")
    
    # Get start and completion times
    START_TIME=$(echo "$EXECUTION" | jq -r '.status.startTime // empty')
    COMPLETION_TIME=$(echo "$EXECUTION" | jq -r '.status.completionTime // empty')
    STATUS=$(echo "$EXECUTION" | jq -r '.status.conditions[0].type // "Unknown"')
    
    if [ -n "$START_TIME" ] && [ -n "$COMPLETION_TIME" ]; then
      # Calculate duration - handle microseconds
      START_TIME_CLEAN=$(echo "$START_TIME" | sed 's/\.[0-9]*Z$/Z/' | sed 's/+00:00$/Z/')
      COMPLETION_TIME_CLEAN=$(echo "$COMPLETION_TIME" | sed 's/\.[0-9]*Z$/Z/' | sed 's/+00:00$/Z/')
      
      if date -v-1d > /dev/null 2>&1; then
        # BSD date
        START_SEC=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$START_TIME_CLEAN" +%s 2>/dev/null || echo "0")
        END_SEC=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$COMPLETION_TIME_CLEAN" +%s 2>/dev/null || echo "0")
      else
        # GNU date
        START_SEC=$(date -d "$START_TIME_CLEAN" +%s 2>/dev/null || echo "0")
        END_SEC=$(date -d "$COMPLETION_TIME_CLEAN" +%s 2>/dev/null || echo "0")
      fi
      
      if [ "$START_SEC" -gt 0 ] && [ "$END_SEC" -gt 0 ]; then
        DURATION=$((END_SEC - START_SEC))
        TOTAL_DURATION=$((TOTAL_DURATION + DURATION))
        
        if [ "$STATUS" = "Completed" ]; then
          SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
          FAILED_COUNT=$((FAILED_COUNT + 1))
        fi
      fi
    fi
  done
  
  if [ "$TOTAL_DURATION" -gt 0 ]; then
    # Calculate costs
    CPU_COST=$(echo "$TOTAL_DURATION * $CPU * $CPU_RATE" | bc -l)
    MEMORY_COST=$(echo "$TOTAL_DURATION * $MEMORY * $MEMORY_RATE" | bc -l)
    JOB_TOTAL_COST=$(echo "$CPU_COST + $MEMORY_COST" | bc -l)
    
    # Format numbers
    CPU_COST_FMT=$(printf "%.2f" "$CPU_COST")
    MEMORY_COST_FMT=$(printf "%.2f" "$MEMORY_COST")
    JOB_TOTAL_COST_FMT=$(printf "%.2f" "$JOB_TOTAL_COST")
    
    # Calculate averages
    AVG_DURATION=$((TOTAL_DURATION / JOB_COUNT))
    AVG_MINUTES=$((AVG_DURATION / 60))
    COST_PER_JOB=$(echo "$JOB_TOTAL_COST / $JOB_COUNT" | bc -l)
    COST_PER_JOB_FMT=$(printf "%.2f" "$COST_PER_JOB")
    
    echo "  Successful: $SUCCESS_COUNT"
    echo "  Failed: $FAILED_COUNT"
    echo "  Total duration: $TOTAL_DURATION seconds ($((TOTAL_DURATION / 60)) minutes)"
    echo "  Average duration: $AVG_MINUTES minutes per job"
    echo ""
    echo -e "${GREEN}  Cost Breakdown:${NC}"
    echo "    CPU cost:    \$$CPU_COST_FMT"
    echo "    Memory cost: \$$MEMORY_COST_FMT"
    echo "    Total cost:  \$$JOB_TOTAL_COST_FMT"
    echo "    Per job:     \$$COST_PER_JOB_FMT"
    echo ""
    
    TOTAL_TRAINING_COST=$(echo "$TOTAL_TRAINING_COST + $JOB_TOTAL_COST" | bc -l)
    TOTAL_JOBS=$((TOTAL_JOBS + JOB_COUNT))
  else
    echo "  Could not calculate duration (incomplete execution data)"
    echo ""
  fi
done

##############################################################
# PART 2: Web Services (new logic)
##############################################################
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}PART 2: Web Service Costs${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

SERVICES=("mmm-app-web" "mmm-app-dev-web")

for SERVICE in "${SERVICES[@]}"; do
  echo -e "${BLUE}Analyzing: $SERVICE${NC}"
  echo ""
  
  # Get service details
  SERVICE_INFO=$(gcloud run services describe "$SERVICE" \
    --region="$REGION" \
    --format="json" 2>/dev/null || echo "{}")
  
  if [ "$SERVICE_INFO" = "{}" ] || [ -z "$SERVICE_INFO" ]; then
    echo "  Service not found or inaccessible"
    echo ""
    continue
  fi
  
  # Extract configuration
  CPU=$(echo "$SERVICE_INFO" | jq -r '.spec.template.spec.containers[0].resources.limits.cpu // "2"' | sed 's/[^0-9.]//g')
  MEMORY=$(echo "$SERVICE_INFO" | jq -r '.spec.template.spec.containers[0].resources.limits.memory // "4Gi"' | sed 's/Gi//g' | sed 's/G//g')
  MIN_INSTANCES=$(echo "$SERVICE_INFO" | jq -r '.spec.template.metadata.annotations["run.googleapis.com/min-instances"] // "0"')
  MAX_INSTANCES=$(echo "$SERVICE_INFO" | jq -r '.spec.template.metadata.annotations["run.googleapis.com/max-instances"] // "10"')
  
  echo "  Configuration: ${CPU} vCPU, ${MEMORY} GB"
  echo "  Scaling: min=$MIN_INSTANCES, max=$MAX_INSTANCES"
  
  # Estimate web service costs
  # Since we can't easily get exact instance-hours from gcloud, we'll estimate based on:
  # 1. If min_instances > 0: always-on cost
  # 2. Request-based usage (estimate)
  
  # Calculate always-on cost (if min_instances > 0)
  ALWAYS_ON_COST=0
  if [ "$MIN_INSTANCES" -gt 0 ]; then
    # Always-on: min_instances * seconds_in_period * rates
    SECONDS_IN_PERIOD=$((DAYS_BACK * 24 * 3600))
    ALWAYS_ON_CPU_COST=$(echo "$MIN_INSTANCES * $SECONDS_IN_PERIOD * $CPU * $CPU_RATE" | bc -l)
    ALWAYS_ON_MEMORY_COST=$(echo "$MIN_INSTANCES * $SECONDS_IN_PERIOD * $MEMORY * $MEMORY_RATE" | bc -l)
    ALWAYS_ON_COST=$(echo "$ALWAYS_ON_CPU_COST + $ALWAYS_ON_MEMORY_COST" | bc -l)
    ALWAYS_ON_COST_FMT=$(printf "%.2f" "$ALWAYS_ON_COST")
    echo "  Always-on cost (min_instances=$MIN_INSTANCES): \$$ALWAYS_ON_COST_FMT"
  fi
  
  # Estimate request-based usage
  # This is approximate since gcloud doesn't expose per-service request metrics easily
  # We'll use a conservative estimate based on typical Streamlit usage
  
  echo ""
  echo -e "${YELLOW}  ⚠️  Note: Web service costs are estimated${NC}"
  echo "  Actual costs depend on:"
  echo "    - Number of user requests"
  echo "    - Request duration (processing time)"
  echo "    - Container instance-hours"
  echo ""
  
  # Conservative estimate: assume moderate usage
  # For a Streamlit app with moderate traffic:
  # - ~50-200 requests/day (1,500-6,000 per 30 days)
  # - Average 30-60 seconds per request
  # - With container pooling, ~2-4 hours of active time per day
  
  if [ "$MIN_INSTANCES" -eq 0 ]; then
    # Scale-to-zero: estimate based on typical usage
    # Assume 3 hours/day active for moderate usage
    ESTIMATED_HOURS_PER_DAY=3
    ESTIMATED_TOTAL_HOURS=$(echo "$ESTIMATED_HOURS_PER_DAY * $DAYS_BACK" | bc -l)
    ESTIMATED_SECONDS=$(echo "$ESTIMATED_TOTAL_HOURS * 3600" | bc -l)
    
    USAGE_CPU_COST=$(echo "$ESTIMATED_SECONDS * $CPU * $CPU_RATE" | bc -l)
    USAGE_MEMORY_COST=$(echo "$ESTIMATED_SECONDS * $MEMORY * $MEMORY_RATE" | bc -l)
    USAGE_COST=$(echo "$USAGE_CPU_COST + $USAGE_MEMORY_COST" | bc -l)
    
    USAGE_COST_FMT=$(printf "%.2f" "$USAGE_COST")
    ESTIMATED_TOTAL_HOURS_FMT=$(printf "%.1f" "$ESTIMATED_TOTAL_HOURS")
    
    echo -e "${GREEN}  Estimated Cost (moderate usage):${NC}"
    echo "    Container hours: ~${ESTIMATED_TOTAL_HOURS_FMT}h"
    echo "    CPU cost:    \$$USAGE_CPU_COST"
    echo "    Memory cost: \$$USAGE_MEMORY_COST"
    echo "    Total cost:  \$$USAGE_COST_FMT"
    
    SERVICE_TOTAL_COST=$USAGE_COST
  else
    # Has min_instances: cost is primarily always-on
    SERVICE_TOTAL_COST=$ALWAYS_ON_COST
    echo -e "${GREEN}  Total Cost:${NC}"
    echo "    \$$ALWAYS_ON_COST_FMT (always-on with min_instances=$MIN_INSTANCES)"
  fi
  
  echo ""
  
  TOTAL_WEB_COST=$(echo "$TOTAL_WEB_COST + $SERVICE_TOTAL_COST" | bc -l)
done

# Disclaimer about web service estimation
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Web Service Cost Estimation Method${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Web service costs are ESTIMATED because:"
echo "  • gcloud doesn't expose per-service instance-hours"
echo "  • Actual costs depend on request patterns"
echo "  • Container pooling and cold starts affect runtime"
echo ""
echo "Estimation assumptions (moderate usage):"
echo "  • ~3 hours/day of active container time"
echo "  • Scale-to-zero when idle (if min_instances=0)"
echo "  • Typical Streamlit app usage pattern"
echo ""
echo "For EXACT costs:"
echo "  1. GCP Console → Billing → Reports"
echo "  2. Filter by 'Cloud Run' service"
echo "  3. Group by 'SKU' to see actual breakdown"
echo ""

##############################################################
# PART 3: Cloud Scheduler Costs (new logic)
##############################################################
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}PART 3: Cloud Scheduler Costs${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

TOTAL_SCHEDULER_COST=0

# Get all scheduler jobs
SCHEDULER_JOBS=$(gcloud scheduler jobs list \
  --location="$REGION" \
  --format="json" 2>/dev/null || echo "[]")

if [ "$SCHEDULER_JOBS" = "[]" ] || [ -z "$SCHEDULER_JOBS" ]; then
  echo "  No scheduler jobs found"
  echo ""
else
  JOB_COUNT=$(echo "$SCHEDULER_JOBS" | jq 'length')
  echo "  Total scheduler jobs: $JOB_COUNT"
  echo ""
  
  # Cloud Scheduler pricing:
  # - First 3 jobs: Free
  # - Additional jobs: $0.10/job/month
  
  if [ "$JOB_COUNT" -le 3 ]; then
    SCHEDULER_JOB_COST=0
    echo -e "${GREEN}  All jobs within free tier (≤3 jobs)${NC}"
  else
    PAID_JOBS=$((JOB_COUNT - 3))
    SCHEDULER_JOB_COST=$(echo "$PAID_JOBS * 0.10" | bc -l)
    SCHEDULER_JOB_COST_FMT=$(printf "%.2f" "$SCHEDULER_JOB_COST")
    echo -e "${YELLOW}  Paid jobs: $PAID_JOBS (beyond free tier)${NC}"
    echo "  Job cost: \$$SCHEDULER_JOB_COST_FMT/month"
  fi
  
  echo ""
  echo "  Job Details:"
  
  # List each job with schedule
  for i in $(seq 0 $((JOB_COUNT - 1))); do
    JOB=$(echo "$SCHEDULER_JOBS" | jq -r ".[$i]")
    JOB_NAME=$(echo "$JOB" | jq -r '.name' | sed 's|.*/||')
    SCHEDULE=$(echo "$JOB" | jq -r '.schedule')
    STATE=$(echo "$JOB" | jq -r '.state')
    
    # Calculate invocations per month based on schedule
    # This is a rough estimate
    case "$SCHEDULE" in
      "*/1 * * * *")  # Every 1 minute
        INVOCATIONS_PER_MONTH=43200  # 60 * 24 * 30
        ;;
      "*/5 * * * *")  # Every 5 minutes
        INVOCATIONS_PER_MONTH=8640   # 12 * 24 * 30
        ;;
      *)
        INVOCATIONS_PER_MONTH=0
        ;;
    esac
    
    echo "    • $JOB_NAME"
    echo "      Schedule: $SCHEDULE"
    echo "      State: $STATE"
    if [ "$INVOCATIONS_PER_MONTH" -gt 0 ]; then
      echo "      Est. invocations/month: ~$INVOCATIONS_PER_MONTH"
    fi
  done
  
  echo ""
  
  # Estimate request costs (when scheduler invokes Cloud Run)
  # Each scheduler invocation is also a Cloud Run request
  # But request costs are typically negligible ($0.0000004 per request)
  
  echo "  Note: Scheduler invokes Cloud Run services"
  echo "  Request costs are negligible (~\$0.02/month total)"
  echo ""
  
  TOTAL_SCHEDULER_COST=$SCHEDULER_JOB_COST
fi

##############################################################
# SUMMARY
##############################################################
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}TOTAL CLOUD RUN COSTS (Last $DAYS_BACK days)${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

TOTAL_TRAINING_COST_FMT=$(printf "%.2f" "$TOTAL_TRAINING_COST")
TOTAL_WEB_COST_FMT=$(printf "%.2f" "$TOTAL_WEB_COST")
TOTAL_SCHEDULER_COST_FMT=$(printf "%.2f" "$TOTAL_SCHEDULER_COST")
GRAND_TOTAL=$(echo "$TOTAL_TRAINING_COST + $TOTAL_WEB_COST + $TOTAL_SCHEDULER_COST" | bc -l)
GRAND_TOTAL_FMT=$(printf "%.2f" "$GRAND_TOTAL")

echo "Training Jobs:"
echo "  Jobs executed: $TOTAL_JOBS"
echo "  Total cost:    \$$TOTAL_TRAINING_COST_FMT"
echo ""

echo "Web Services:"
echo "  Total cost:    \$$TOTAL_WEB_COST_FMT (estimated)"
echo ""

echo "Cloud Scheduler:"
echo "  Total cost:    \$$TOTAL_SCHEDULER_COST_FMT"
echo ""

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Grand Total:   \$$GRAND_TOTAL_FMT${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Cost breakdown by percentage
if [ "$(echo "$GRAND_TOTAL > 0" | bc -l)" -eq 1 ]; then
  TRAINING_PCT=$(echo "scale=1; $TOTAL_TRAINING_COST * 100 / $GRAND_TOTAL" | bc -l)
  WEB_PCT=$(echo "scale=1; $TOTAL_WEB_COST * 100 / $GRAND_TOTAL" | bc -l)
  SCHEDULER_PCT=$(echo "scale=1; $TOTAL_SCHEDULER_COST * 100 / $GRAND_TOTAL" | bc -l)
  
  echo "Cost Breakdown:"
  echo "  Training jobs: ${TRAINING_PCT}%"
  echo "  Web services:  ${WEB_PCT}%"
  echo "  Scheduler:     ${SCHEDULER_PCT}%"
  echo ""
fi

# Monthly projection
if [ "$TOTAL_JOBS" -gt 0 ] || [ "$(echo "$GRAND_TOTAL > 0" | bc -l)" -eq 1 ]; then
  DAYS_IN_MONTH=30
  MONTHLY_TOTAL=$(echo "$GRAND_TOTAL * $DAYS_IN_MONTH / $DAYS_BACK" | bc -l)
  MONTHLY_TRAINING=$(echo "$TOTAL_TRAINING_COST * $DAYS_IN_MONTH / $DAYS_BACK" | bc -l)
  MONTHLY_WEB=$(echo "$TOTAL_WEB_COST * $DAYS_IN_MONTH / $DAYS_BACK" | bc -l)
  # Scheduler cost is already monthly, no need to extrapolate
  MONTHLY_SCHEDULER=$TOTAL_SCHEDULER_COST
  
  MONTHLY_TOTAL_FMT=$(printf "%.2f" "$MONTHLY_TOTAL")
  MONTHLY_TRAINING_FMT=$(printf "%.2f" "$MONTHLY_TRAINING")
  MONTHLY_WEB_FMT=$(printf "%.2f" "$MONTHLY_WEB")
  MONTHLY_SCHEDULER_FMT=$(printf "%.2f" "$MONTHLY_SCHEDULER")
  
  echo "Projected Monthly (30 days):"
  echo "  Training:  \$$MONTHLY_TRAINING_FMT"
  echo "  Web:       \$$MONTHLY_WEB_FMT"
  echo "  Scheduler: \$$MONTHLY_SCHEDULER_FMT"
  echo "  Total:     \$$MONTHLY_TOTAL_FMT"
  echo ""
fi

echo "=========================================="
echo "Additional GCP costs (not included above):"
echo "  - GCS storage: ~\$2-3/month"
echo "  - Artifact Registry: ~\$1-12/month (varies with cleanup)"
echo "  - Cloud Logging: ~\$1-2/month"
echo "  - Secret Manager: ~\$0.50/month"
echo "=========================================="
echo ""

echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Cost Optimization Opportunities${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "To further reduce Cloud Run costs:"
echo ""
echo "1. Remove warmup scheduler job (if exists):"
echo "   • Savings: ~\$0-60/year"
echo "   • Trade-off: 2-3s cold start on first request"
echo "   • Command: ./scripts/remove_warmup_job.sh"
echo ""
echo "2. Optimize web service resources (already done if using Terraform):"
echo "   • CPU: 2 vCPU → 1 vCPU"
echo "   • Memory: 4GB → 2GB"
echo "   • Savings: ~\$60-80/month"
echo ""
echo "3. Clean Artifact Registry regularly:"
echo "   • Command: ./scripts/cleanup_artifact_registry.sh"
echo "   • Savings: ~\$10-12/month"
echo ""
echo "For detailed cost optimization guide:"
echo "  See: COST_REDUCTION_IMPLEMENTATION.md"
echo ""
