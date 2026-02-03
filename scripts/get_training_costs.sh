#!/bin/bash

# Training Cost Analysis Script
# Automatically collects and calculates training job costs from Cloud Run
#
# This script queries Cloud Run job executions and calculates actual costs
# based on CPU and memory usage.

set -e

PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
DAYS_BACK="${DAYS_BACK:-30}"

# Pricing (europe-west1)
CPU_RATE=0.000024      # $ per vCPU-second
MEMORY_RATE=0.0000025  # $ per GB-second

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=========================================="
echo "Training Job Cost Analysis"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Period: Last $DAYS_BACK days"
echo ""

# Jobs to analyze
JOBS=("mmm-app-training" "mmm-app-dev-training")

TOTAL_COST=0
TOTAL_JOBS=0

for JOB in "${JOBS[@]}"; do
  echo -e "${BLUE}Analyzing: $JOB${NC}"
  echo ""
  
  # Get executions from the last N days
  # Cross-platform date calculation (works on both macOS/BSD and Linux/GNU)
  if date -v-1d > /dev/null 2>&1; then
    # BSD date (macOS)
    START_DATE=$(date -u -v-${DAYS_BACK}d +"%Y-%m-%dT%H:%M:%SZ")
  else
    # GNU date (Linux)
    START_DATE=$(date -u -d "$DAYS_BACK days ago" +"%Y-%m-%dT%H:%M:%SZ")
  fi
  
  # Get job executions
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
  
  # Get job configuration to determine CPU and memory
  JOB_CONFIG=$(gcloud run jobs describe "$JOB" \
    --region="$REGION" \
    --format="json" 2>/dev/null)
  
  # Extract CPU and memory from job config
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
      # Calculate duration in seconds - cross-platform
      # Handle various ISO 8601 formats by stripping milliseconds and normalizing timezone
      START_TIME_CLEAN=$(echo "$START_TIME" | sed 's/\.[0-9]*Z$/Z/' | sed 's/+00:00$/Z/')
      COMPLETION_TIME_CLEAN=$(echo "$COMPLETION_TIME" | sed 's/\.[0-9]*Z$/Z/' | sed 's/+00:00$/Z/')
      
      if date -v-1d > /dev/null 2>&1; then
        # BSD date (macOS) - use -j to avoid setting system date
        START_SEC=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$START_TIME_CLEAN" +%s 2>/dev/null || echo "0")
        END_SEC=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$COMPLETION_TIME_CLEAN" +%s 2>/dev/null || echo "0")
      else
        # GNU date (Linux)
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
    
    # Calculate average duration
    AVG_DURATION=$((TOTAL_DURATION / JOB_COUNT))
    AVG_MINUTES=$((AVG_DURATION / 60))
    
    # Calculate cost per job
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
    
    TOTAL_COST=$(echo "$TOTAL_COST + $JOB_TOTAL_COST" | bc -l)
    TOTAL_JOBS=$((TOTAL_JOBS + JOB_COUNT))
  else
    echo "  Could not calculate duration (incomplete execution data)"
    echo ""
  fi
done

echo "=========================================="
echo "Summary (Last $DAYS_BACK days)"
echo "=========================================="
TOTAL_COST_FMT=$(printf "%.2f" "$TOTAL_COST")
echo "Total jobs executed: $TOTAL_JOBS"
echo "Total training cost: \$$TOTAL_COST_FMT"

if [ "$TOTAL_JOBS" -gt 0 ]; then
  AVG_COST=$(echo "$TOTAL_COST / $TOTAL_JOBS" | bc -l)
  AVG_COST_FMT=$(printf "%.2f" "$AVG_COST")
  echo "Average cost per job: \$$AVG_COST_FMT"
  
  # Extrapolate monthly cost
  DAYS_IN_MONTH=30
  MONTHLY_JOBS=$(echo "$TOTAL_JOBS * $DAYS_IN_MONTH / $DAYS_BACK" | bc -l)
  MONTHLY_COST=$(echo "$TOTAL_COST * $DAYS_IN_MONTH / $DAYS_BACK" | bc -l)
  MONTHLY_JOBS_FMT=$(printf "%.0f" "$MONTHLY_JOBS")
  MONTHLY_COST_FMT=$(printf "%.2f" "$MONTHLY_COST")
  
  echo ""
  echo "Projected monthly (30 days):"
  echo "  Jobs: ~$MONTHLY_JOBS_FMT"
  echo "  Cost: \$$MONTHLY_COST_FMT"
fi

echo ""
echo "=========================================="
echo "⚠️  IMPORTANT: Cost Calculation Scope"
echo "=========================================="
echo "This script calculates TRAINING JOB costs only."
echo ""
echo "Cloud Run has TWO types of resources:"
echo "  1. Training Jobs (mmm-app-training, mmm-app-dev-training)"
echo "     → Calculated above: \$$TOTAL_COST_FMT"
echo ""
echo "  2. Web Service (mmm-app, mmm-app-dev)"
echo "     → Streamlit web UI running continuously"
echo "     → NOT included in this calculation"
echo "     → This is typically the LARGEST cost component"
echo ""
echo "Your actual Cloud Run bill includes BOTH:"
echo "  - Training job compute (this script): ~\$$TOTAL_COST_FMT"
echo "  - Web service compute (NOT calculated): ~\$100-150/month"
echo "  - Container image pulls and overhead"
echo ""
echo "To see TOTAL Cloud Run costs:"
echo "  1. GCP Console → Billing → Reports"
echo "  2. Filter by 'Cloud Run' service"
echo "  3. Group by 'SKU' to see breakdown:"
echo "     - 'Cloud Run CPU' = compute time"
echo "     - 'Cloud Run Memory' = memory allocation"
echo "     - 'Cloud Run Requests' = HTTP requests"
echo ""
echo "Additional GCP costs (not Cloud Run):"
echo "  - GCS storage and operations (~\$2-3/month)"
echo "  - Artifact Registry storage (~\$11-12/month)"
echo "  - Cloud Logging (~\$1-2/month)"
echo "  - Secret Manager (~\$2/month)"
echo "=========================================="
echo ""
