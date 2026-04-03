#!/bin/bash
#
# Comprehensive Cloud Run Cost Tracking Script
# 
# This script provides detailed cost analysis across all cost drivers:
# - Training jobs (prod vs dev)
# - Web services (prod vs dev)
# - Deployment frequency impact
# - Queue tick scheduler invocations
# - Artifact Registry storage
# - User requests vs automated requests
#
# Usage:
#   ./scripts/get_comprehensive_costs.sh [DAYS_BACK]
#
# Examples:
#   ./scripts/get_comprehensive_costs.sh        # Last 30 days (default)
#   DAYS_BACK=7 ./scripts/get_comprehensive_costs.sh  # Last 7 days
#
# Requirements:
#   - gcloud CLI configured with appropriate permissions
#   - jq for JSON parsing (optional, graceful degradation if missing)
#

set -euo pipefail

# Configuration
PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
DAYS_BACK="${DAYS_BACK:-30}"

# Service names
PROD_WEB_SERVICE="mmm-app-web"
DEV_WEB_SERVICE="mmm-app-dev-web"
PROD_TRAINING_JOB="mmm-app-training"
DEV_TRAINING_JOB="mmm-app-dev-training"
PROD_SCHEDULER="robyn-queue-tick"
DEV_SCHEDULER="robyn-queue-tick-dev"

# Pricing (europe-west1, as of 2026)
CPU_PRICE_PER_VCPU_SEC=0.000024
MEMORY_PRICE_PER_GIB_SEC=0.0000025
INVOCATION_PRICE=0.0000004  # Per request

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo "==========================================="
echo "Comprehensive Cloud Run Cost Analysis"
echo "==========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Period: Last $DAYS_BACK days"
echo ""

# Calculate date range
if date --version >/dev/null 2>&1; then
    # GNU date (Linux)
    START_DATE=$(date -u -d "$DAYS_BACK days ago" +"%Y-%m-%dT%H:%M:%SZ")
else
    # BSD date (macOS)
    START_DATE=$(date -u -v-${DAYS_BACK}d +"%Y-%m-%dT%H:%M:%SZ")
fi

#############################################
# Function: Get job execution stats
#############################################
get_job_stats() {
    local job_name=$1
    local job_label=$2
    
    echo -e "${BOLD}Analyzing: $job_label${NC}"
    
    # Get executions
    local executions=$(gcloud run jobs executions list \
        --job="$job_name" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --filter="metadata.createTime>=\"$START_DATE\"" \
        --format="json" 2>/dev/null || echo "[]")
    
    if [ "$executions" = "[]" ] || [ -z "$executions" ]; then
        echo "  No executions found in the last $DAYS_BACK days"
        echo ""
        return 0
    fi
    
    # Parse execution data
    local total_count=0
    local success_count=0
    local failed_count=0
    local total_duration=0
    local cpu_config=""
    local memory_config=""
    
    # Use jq if available, otherwise parse manually
    if command -v jq >/dev/null 2>&1; then
        total_count=$(echo "$executions" | jq 'length')
        success_count=$(echo "$executions" | jq '[.[] | select(.status.conditions[]?.type == "Completed" and .status.conditions[]?.status == "True")] | length')
        failed_count=$((total_count - success_count))
        
        # Get duration for successful executions
        for exec in $(echo "$executions" | jq -c '.[]'); do
            local start_time=$(echo "$exec" | jq -r '.metadata.createTime // empty')
            local completion_time=$(echo "$exec" | jq -r '.status.completionTime // empty')
            
            if [ -n "$start_time" ] && [ -n "$completion_time" ]; then
                # Strip microseconds for BSD date compatibility
                start_time_clean=$(echo "$start_time" | sed 's/\.[0-9]*Z$/Z/')
                completion_time_clean=$(echo "$completion_time" | sed 's/\.[0-9]*Z$/Z/')
                
                if date --version >/dev/null 2>&1; then
                    # GNU date
                    local start_sec=$(date -d "$start_time_clean" +%s)
                    local end_sec=$(date -d "$completion_time_clean" +%s)
                else
                    # BSD date
                    local start_sec=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$start_time_clean" +%s 2>/dev/null || echo 0)
                    local end_sec=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$completion_time_clean" +%s 2>/dev/null || echo 0)
                fi
                
                if [ "$start_sec" -gt 0 ] && [ "$end_sec" -gt 0 ]; then
                    local duration=$((end_sec - start_sec))
                    total_duration=$((total_duration + duration))
                fi
            fi
        done
        
        # Get resource configuration from first execution
        cpu_config=$(echo "$executions" | jq -r '.[0].spec.template.spec.containers[0].resources.limits.cpu // "unknown"')
        memory_config=$(echo "$executions" | jq -r '.[0].spec.template.spec.containers[0].resources.limits.memory // "unknown"')
    else
        # Fallback: basic counting without jq
        total_count=$(echo "$executions" | grep -c '"name"' || echo 0)
        success_count=$total_count  # Approximate
        failed_count=0
        cpu_config="8.0"  # Default assumption
        memory_config="32Gi"
    fi
    
    # Calculate costs
    if [ "$total_duration" -gt 0 ]; then
        local avg_duration=$((total_duration / success_count))
        local avg_minutes=$((avg_duration / 60))
        
        # Parse CPU and memory values
        local cpu_value=$(echo "$cpu_config" | grep -o '[0-9.]*' | head -1)
        local memory_value=$(echo "$memory_config" | grep -o '[0-9.]*' | head -1)
        
        # Default to common values if parsing fails
        cpu_value=${cpu_value:-8.0}
        memory_value=${memory_value:-32}
        
        # Calculate costs
        local cpu_cost=$(echo "$total_duration * $cpu_value * $CPU_PRICE_PER_VCPU_SEC" | bc -l 2>/dev/null || echo "0")
        local memory_cost=$(echo "$total_duration * $memory_value * $MEMORY_PRICE_PER_GIB_SEC" | bc -l 2>/dev/null || echo "0")
        local total_cost=$(echo "$cpu_cost + $memory_cost" | bc -l 2>/dev/null || echo "0")
        local cost_per_job=$(echo "scale=2; $total_cost / $success_count" | bc -l 2>/dev/null || echo "0")
        
        echo "  Total executions: $total_count"
        echo "  Configuration: ${cpu_value} vCPU, ${memory_value} GB"
        echo "  Successful: $success_count"
        echo "  Failed: $failed_count"
        echo "  Total duration: $total_duration seconds ($((total_duration / 60)) minutes)"
        echo "  Average duration: $avg_minutes minutes per job"
        echo ""
        echo "  Cost Breakdown:"
        printf "    CPU cost:    \$%.2f\n" "$cpu_cost"
        printf "    Memory cost: \$%.2f\n" "$memory_cost"
        printf "    Total cost:  \$%.2f\n" "$total_cost"
        printf "    Per job:     \$%.2f\n" "$cost_per_job"
        echo ""
        
        # Export for summary
        eval "${job_label}_EXECUTIONS=$success_count"
        eval "${job_label}_COST=$total_cost"
    else
        echo "  Total executions: $total_count"
        echo "  No completed executions with timing data"
        echo ""
        
        eval "${job_label}_EXECUTIONS=0"
        eval "${job_label}_COST=0"
    fi
}

#############################################
# Function: Get web service stats
#############################################
get_web_service_stats() {
    local service_name=$1
    local service_label=$2
    
    echo -e "${BOLD}Analyzing: $service_label${NC}"
    
    # Get service configuration
    local service_info=$(gcloud run services describe "$service_name" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --format="json" 2>/dev/null || echo "{}")
    
    if [ "$service_info" = "{}" ]; then
        echo "  Service not found or not accessible"
        echo ""
        return 0
    fi
    
    # Extract configuration
    local cpu_limit=""
    local memory_limit=""
    local min_instances=""
    local max_instances=""
    
    if command -v jq >/dev/null 2>&1; then
        cpu_limit=$(echo "$service_info" | jq -r '.spec.template.spec.containers[0].resources.limits.cpu // "1.0"')
        memory_limit=$(echo "$service_info" | jq -r '.spec.template.spec.containers[0].resources.limits.memory // "2Gi"')
        min_instances=$(echo "$service_info" | jq -r '.spec.template.metadata.annotations["run.googleapis.com/min-instances"] // "0"')
        max_instances=$(echo "$service_info" | jq -r '.spec.template.metadata.annotations["run.googleapis.com/max-instances"] // "10"')
    else
        cpu_limit="1.0"
        memory_limit="2Gi"
        min_instances="0"
        max_instances="10"
    fi
    
    # Estimate web service costs
    # Assumptions:
    # - Each request takes ~1-5 seconds (avg 2 seconds)
    # - Scheduler makes 1 request per 10 minutes = 144 requests/day = 4,320/month
    # - User requests: estimated based on typical usage
    
    local hours_per_month=$((DAYS_BACK * 24))
    local cpu_value=$(echo "$cpu_limit" | grep -o '[0-9.]*' | head -1)
    local memory_value=$(echo "$memory_limit" | grep -o '[0-9.]*' | head -1)
    
    cpu_value=${cpu_value:-1.0}
    memory_value=${memory_value:-2}
    
    echo "  Configuration:"
    echo "    CPU: ${cpu_value} vCPU"
    echo "    Memory: ${memory_value} GB"
    echo "    Min instances: ${min_instances}"
    echo "    Max instances: ${max_instances}"
    echo ""
    
    # Calculate idle cost (if min_instances > 0)
    if [ "$min_instances" -gt 0 ]; then
        local idle_hours=$((hours_per_month * min_instances))
        local idle_seconds=$((idle_hours * 3600))
        local idle_cpu_cost=$(echo "$idle_seconds * $cpu_value * $CPU_PRICE_PER_VCPU_SEC" | bc -l 2>/dev/null || echo "0")
        local idle_memory_cost=$(echo "$idle_seconds * $memory_value * $MEMORY_PRICE_PER_GIB_SEC" | bc -l 2>/dev/null || echo "0")
        local idle_total_cost=$(echo "$idle_cpu_cost + $idle_memory_cost" | bc -l 2>/dev/null || echo "0")
        
        echo "  Idle Cost (min_instances=$min_instances):"
        printf "    Monthly idle cost: \$%.2f\n" "$idle_total_cost"
        echo ""
        
        eval "${service_label}_IDLE_COST=$idle_total_cost"
    else
        echo "  Idle Cost: \$0.00 (min_instances=0, scale-to-zero enabled)"
        echo ""
        
        eval "${service_label}_IDLE_COST=0"
    fi
    
    # Request-based cost estimation
    echo "  Request-based costs depend on:"
    echo "    - Scheduler invocations (queue ticks)"
    echo "    - User interactions (page loads, data queries)"
    echo "    - Training job triggers"
    echo ""
    echo "  See scheduler analysis below for automated request costs."
    echo ""
}

#############################################
# Function: Analyze scheduler invocations
#############################################
analyze_scheduler() {
    local scheduler_name=$1
    local scheduler_label=$2
    
    echo -e "${BOLD}Analyzing: $scheduler_label${NC}"
    
    # Get scheduler configuration
    local scheduler_info=$(gcloud scheduler jobs describe "$scheduler_name" \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --format="json" 2>/dev/null || echo "{}")
    
    if [ "$scheduler_info" = "{}" ]; then
        echo "  Scheduler job not found"
        echo ""
        return 0
    fi
    
    # Extract schedule
    local schedule=""
    if command -v jq >/dev/null 2>&1; then
        schedule=$(echo "$scheduler_info" | jq -r '.schedule // "unknown"')
    else
        schedule="*/10 * * * *"  # Assume 10-minute default
    fi
    
    echo "  Schedule: $schedule"
    
    # Calculate invocation frequency
    local invocations_per_hour=0
    if [[ "$schedule" =~ \*/([0-9]+)\ \*\ \*\ \*\ \* ]]; then
        local minutes="${BASH_REMATCH[1]}"
        invocations_per_hour=$((60 / minutes))
    elif [[ "$schedule" = "*/1 * * * *" ]]; then
        invocations_per_hour=60
    fi
    
    local invocations_per_day=$((invocations_per_hour * 24))
    local invocations_per_month=$((invocations_per_day * 30))
    
    echo "  Invocations:"
    echo "    Per hour: $invocations_per_hour"
    echo "    Per day: $invocations_per_day"
    echo "    Per month (30 days): $invocations_per_month"
    echo ""
    
    # Estimate cost
    # Each scheduler invocation:
    # - Triggers web service (1-15 seconds depending on queue state)
    # - Cloud Scheduler charges: $0.10/month (covered by free tier for 1-3 jobs)
    # - Cloud Run charges: Based on actual container time
    
    # Assumptions:
    # - Average request duration: 5 seconds (processing queue tick)
    # - CPU: 1 vCPU (web service)
    # - Memory: 2 GB
    
    local avg_request_duration=5  # seconds
    local total_container_seconds=$((invocations_per_month * avg_request_duration))
    local cpu_value=1.0
    local memory_value=2
    
    local scheduler_cpu_cost=$(echo "$total_container_seconds * $cpu_value * $CPU_PRICE_PER_VCPU_SEC" | bc -l 2>/dev/null || echo "0")
    local scheduler_memory_cost=$(echo "$total_container_seconds * $memory_value * $MEMORY_PRICE_PER_GIB_SEC" | bc -l 2>/dev/null || echo "0")
    local scheduler_invocation_cost=$(echo "$invocations_per_month * $INVOCATION_PRICE" | bc -l 2>/dev/null || echo "0")
    local scheduler_total_cost=$(echo "$scheduler_cpu_cost + $scheduler_memory_cost + $scheduler_invocation_cost" | bc -l 2>/dev/null || echo "0")
    
    echo "  Estimated Monthly Cost:"
    echo "    Container time: $total_container_seconds seconds ($((total_container_seconds / 3600)) hours)"
    printf "    CPU cost: \$%.2f\n" "$scheduler_cpu_cost"
    printf "    Memory cost: \$%.2f\n" "$scheduler_memory_cost"
    printf "    Invocation cost: \$%.2f\n" "$scheduler_invocation_cost"
    printf "    Total: \$%.2f\n" "$scheduler_total_cost"
    echo "    (Cloud Scheduler service: \$0.10/month covered by free tier)"
    echo ""
    
    eval "${scheduler_label}_COST=$scheduler_total_cost"
    eval "${scheduler_label}_INVOCATIONS=$invocations_per_month"
}

#############################################
# Function: Analyze deployment frequency
#############################################
analyze_deployment_frequency() {
    echo -e "${BOLD}Analyzing: Deployment Frequency Impact${NC}"
    
    # Get revision counts for each service
    local prod_revisions=$(gcloud run services describe "$PROD_WEB_SERVICE" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --format="value(status.traffic[].revisionName)" 2>/dev/null | wc -l || echo 0)
    
    local dev_revisions=$(gcloud run services describe "$DEV_WEB_SERVICE" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --format="value(status.traffic[].revisionName)" 2>/dev/null | wc -l || echo 0)
    
    echo "  Active revisions:"
    echo "    Production: $prod_revisions"
    echo "    Development: $dev_revisions"
    echo ""
    
    # Explain deployment cost impact
    echo "  Deployment Cost Impact:"
    echo "    Each deployment creates a new revision that runs alongside"
    echo "    the old revision for 2-8 hours (traffic migration period)."
    echo ""
    echo "    During this overlap:"
    echo "    - Both revisions consume resources"
    echo "    - Effectively doubles costs during transition"
    echo "    - 150 deployments/month = ~€50-60 extra cost"
    echo ""
    echo "  Recommendations:"
    echo "    - Batch changes to reduce deployment frequency"
    echo "    - Target: 30 deployments/month (saves €40-50/month)"
    echo "    - Use feature branches and test thoroughly before merging"
    echo ""
}

#############################################
# Function: Analyze artifact registry
#############################################
analyze_artifact_registry() {
    echo -e "${BOLD}Analyzing: Artifact Registry Costs${NC}"
    
    # Get repository info
    local repo_size=$(gcloud artifacts docker images list \
        "$REGION-docker.pkg.dev/$PROJECT_ID/mmm-repo" \
        --format="value(SIZE_BYTES)" 2>/dev/null | \
        awk '{s+=$1} END {print s}' || echo 0)
    
    if [ "$repo_size" -gt 0 ]; then
        local repo_size_gb=$(echo "scale=2; $repo_size / 1024 / 1024 / 1024" | bc -l)
        local storage_cost=$(echo "scale=2; $repo_size_gb * 0.10" | bc -l)  # $0.10/GB/month
        
        echo "  Repository size: ${repo_size_gb} GB"
        printf "  Estimated monthly storage cost: \$%.2f\n" "$storage_cost"
        echo ""
        echo "  Note: Artifact Registry pricing:"
        echo "    - Storage: \$0.10/GB/month"
        echo "    - Network egress: Free within same region"
        echo ""
        
        ARTIFACT_REGISTRY_COST=$storage_cost
    else
        echo "  Unable to determine repository size"
        echo "  Typical storage: 5-20 GB (\$0.50-2.00/month)"
        echo ""
        
        ARTIFACT_REGISTRY_COST=1.00  # Conservative estimate
    fi
}

#############################################
# Main execution
#############################################

echo ""
echo "==========================================="
echo "TRAINING JOBS"
echo "==========================================="
echo ""

# Prod training jobs
get_job_stats "$PROD_TRAINING_JOB" "PROD_TRAINING"

# Dev training jobs
get_job_stats "$DEV_TRAINING_JOB" "DEV_TRAINING"

echo ""
echo "==========================================="
echo "WEB SERVICES"
echo "==========================================="
echo ""

# Prod web service
get_web_service_stats "$PROD_WEB_SERVICE" "PROD_WEB"

# Dev web service
get_web_service_stats "$DEV_WEB_SERVICE" "DEV_WEB"

echo ""
echo "==========================================="
echo "SCHEDULERS (Queue Ticks)"
echo "==========================================="
echo ""

# Prod scheduler
analyze_scheduler "$PROD_SCHEDULER" "PROD_SCHEDULER"

# Dev scheduler
analyze_scheduler "$DEV_SCHEDULER" "DEV_SCHEDULER"

echo ""
echo "==========================================="
echo "DEPLOYMENT IMPACT"
echo "==========================================="
echo ""

analyze_deployment_frequency

echo ""
echo "==========================================="
echo "ARTIFACT REGISTRY"
echo "==========================================="
echo ""

analyze_artifact_registry

#############################################
# Generate summary
#############################################

echo ""
echo "==========================================="
echo "COST SUMMARY (Last $DAYS_BACK days)"
echo "==========================================="
echo ""

# Calculate totals
PROD_TRAINING_COST=${PROD_TRAINING_EXECUTIONS:-0}
PROD_TRAINING_EXECUTIONS=${PROD_TRAINING_EXECUTIONS:-0}
DEV_TRAINING_COST=${DEV_TRAINING_COST:-0}
DEV_TRAINING_EXECUTIONS=${DEV_TRAINING_EXECUTIONS:-0}
PROD_WEB_IDLE=${PROD_WEB_IDLE_COST:-0}
DEV_WEB_IDLE=${DEV_WEB_IDLE_COST:-0}
PROD_SCHEDULER_COST=${PROD_SCHEDULER_COST:-0}
DEV_SCHEDULER_COST=${DEV_SCHEDULER_COST:-0}
ARTIFACT_REGISTRY_COST=${ARTIFACT_REGISTRY_COST:-1.00}

# Calculate total training
TOTAL_TRAINING=$(echo "${PROD_TRAINING_COST} + ${DEV_TRAINING_COST}" | bc -l 2>/dev/null || echo "0")

# Calculate total web (idle + scheduler requests)
TOTAL_WEB=$(echo "${PROD_WEB_IDLE} + ${DEV_WEB_IDLE} + ${PROD_SCHEDULER_COST} + ${DEV_SCHEDULER_COST}" | bc -l 2>/dev/null || echo "0")

# Calculate grand total
GRAND_TOTAL=$(echo "${TOTAL_TRAINING} + ${TOTAL_WEB} + ${ARTIFACT_REGISTRY_COST}" | bc -l 2>/dev/null || echo "0")

# Monthly projection (if period is not 30 days)
MONTHLY_FACTOR=$(echo "scale=2; 30 / $DAYS_BACK" | bc -l)
MONTHLY_TOTAL=$(echo "${GRAND_TOTAL} * ${MONTHLY_FACTOR}" | bc -l 2>/dev/null || echo "0")

printf "Training Jobs:\n"
printf "  Production: %d jobs, \$%.2f\n" "$PROD_TRAINING_EXECUTIONS" "$PROD_TRAINING_COST"
printf "  Development: %d jobs, \$%.2f\n" "$DEV_TRAINING_EXECUTIONS" "$DEV_TRAINING_COST"
printf "  Subtotal: \$%.2f\n" "$TOTAL_TRAINING"
echo ""

printf "Web Services & Schedulers:\n"
printf "  Production idle: \$%.2f\n" "$PROD_WEB_IDLE"
printf "  Development idle: \$%.2f\n" "$DEV_WEB_IDLE"
printf "  Production scheduler: \$%.2f\n" "$PROD_SCHEDULER_COST"
printf "  Development scheduler: \$%.2f\n" "$DEV_SCHEDULER_COST"
printf "  Subtotal: \$%.2f\n" "$TOTAL_WEB"
echo ""

printf "Artifact Registry: \$%.2f\n" "$ARTIFACT_REGISTRY_COST"
echo ""

printf "${BOLD}Total ($DAYS_BACK days): \$%.2f${NC}\n" "$GRAND_TOTAL"

if [ "$DAYS_BACK" != "30" ]; then
    printf "${BOLD}Projected monthly (30 days): \$%.2f${NC}\n" "$MONTHLY_TOTAL"
fi

echo ""
echo "==========================================="
echo "COST BREAKDOWN BY ENVIRONMENT"
echo "==========================================="
echo ""

PROD_TOTAL=$(echo "${PROD_TRAINING_COST} + ${PROD_WEB_IDLE} + ${PROD_SCHEDULER_COST}" | bc -l 2>/dev/null || echo "0")
DEV_TOTAL=$(echo "${DEV_TRAINING_COST} + ${DEV_WEB_IDLE} + ${DEV_SCHEDULER_COST}" | bc -l 2>/dev/null || echo "0")

printf "Production: \$%.2f\n" "$PROD_TOTAL"
printf "Development: \$%.2f\n" "$DEV_TOTAL"
printf "Shared (Artifact Registry): \$%.2f\n" "$ARTIFACT_REGISTRY_COST"

echo ""
echo "==========================================="
echo "NOTES"
echo "==========================================="
echo ""
echo "1. Training jobs account for the majority of costs at scale"
echo "2. Scheduler costs are now optimized (10-minute intervals)"
echo "3. Web services use scale-to-zero (min_instances=0) to eliminate idle costs"
echo "4. Deployment frequency impacts costs during traffic migration periods"
echo "5. User request costs are variable and depend on actual usage patterns"
echo ""
echo "For more details, see:"
echo "  - COST_OPTIMIZATION.md"
echo "  - docs/COST_OPTIMIZATIONS_SUMMARY.md"
echo ""
