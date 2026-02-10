#!/bin/bash
#
# ACTUAL Cloud Run Cost Tracking Script
# 
# This script retrieves ACTUAL costs from GCP billing, not estimates.
# It uses the Cloud Billing API to get real historical spending data.
#
# Usage:
#   ./scripts/get_actual_costs.sh [DAYS_BACK]
#
# Examples:
#   ./scripts/get_actual_costs.sh        # Last 30 days (default)
#   DAYS_BACK=7 ./scripts/get_actual_costs.sh  # Last 7 days
#
# Configuration (via environment variables):
#   PROJECT_ID - GCP project ID (default: datawarehouse-422511)
#   DAYS_BACK - Number of days to look back (default: 30)
#   BILLING_DATASET - BigQuery dataset for billing export (default: mmm_billing)
#   BILLING_ACCOUNT_NUM - Billing account number (default: 01B2F0_BCBFB7_2051C5)
#
# Requirements:
#   - gcloud CLI configured with appropriate permissions
#   - Billing Account Reader role
#   - BigQuery billing export enabled
#   - jq for JSON parsing
#

set -euo pipefail

# Configuration
PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
DAYS_BACK="${DAYS_BACK:-30}"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

echo "==========================================="
echo "ACTUAL Cloud Run Costs (from Billing API)"
echo "==========================================="
echo "Project: $PROJECT_ID"
echo "Period: Last $DAYS_BACK days"
echo ""

# Calculate date range
if date --version >/dev/null 2>&1; then
    # GNU date (Linux)
    START_DATE=$(date -u -d "$DAYS_BACK days ago" +"%Y-%m-%d")
    END_DATE=$(date -u +"%Y-%m-%d")
else
    # BSD date (macOS)
    START_DATE=$(date -u -v-${DAYS_BACK}d +"%Y-%m-%d")
    END_DATE=$(date -u +"%Y-%m-%d")
fi

echo "Date range: $START_DATE to $END_DATE"
echo ""

#############################################
# Get billing account ID
#############################################
echo -e "${BOLD}Step 1: Getting billing account...${NC}"
BILLING_ACCOUNT=$(gcloud billing projects describe "$PROJECT_ID" \
    --format="value(billingAccountName)" 2>/dev/null | sed 's|billingAccounts/||')

if [ -z "$BILLING_ACCOUNT" ]; then
    echo -e "${RED}ERROR: Could not find billing account for project${NC}"
    echo "Make sure you have billing.resourceAssociations.list permission"
    exit 1
fi

echo "Billing Account: $BILLING_ACCOUNT"
echo ""

#############################################
# Query actual costs using BigQuery export
#############################################
echo -e "${BOLD}Step 2: Querying actual costs...${NC}"
echo ""

# Check if BigQuery billing export is configured
# Actual billing export structure for this project:
# Dataset: mmm_billing
# Table: gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5
DATASET_ID="${BILLING_DATASET:-mmm_billing}"
BILLING_ACCOUNT_NUM="${BILLING_ACCOUNT_NUM:-01B2F0_BCBFB7_2051C5}"
TABLE_NAME="gcp_billing_export_resource_v1_${BILLING_ACCOUNT_NUM}"

echo "Attempting to query billing data from BigQuery export..."
echo "Dataset: $DATASET_ID"
echo "Table: $TABLE_NAME"
echo ""

# Build BigQuery query
# Note: BigQuery returns newline-delimited JSON (NDJSON), not a JSON array
read -r -d '' QUERY << 'EOF' || true
SELECT
  service.description as service,
  sku.description as sku,
  SUM(cost) as total_cost,
  SUM(usage.amount) as usage_amount,
  usage.unit as usage_unit
FROM `PROJECT_ID.DATASET_ID.TABLE_NAME`
WHERE
  DATE(_PARTITIONTIME) >= 'START_DATE'
  AND DATE(_PARTITIONTIME) <= 'END_DATE'
  AND project.id = 'PROJECT_ID'
  AND (
    service.description = 'Cloud Run'
    OR service.description = 'Artifact Registry'
    OR service.description = 'Cloud Storage'
    OR service.description = 'Cloud Scheduler'
  )
GROUP BY service, sku, usage_unit
ORDER BY total_cost DESC
EOF

# Replace placeholders
QUERY="${QUERY//PROJECT_ID/$PROJECT_ID}"
QUERY="${QUERY//DATASET_ID/$DATASET_ID}"
QUERY="${QUERY//TABLE_NAME/$TABLE_NAME}"
QUERY="${QUERY//START_DATE/$START_DATE}"
QUERY="${QUERY//END_DATE/$END_DATE}"

# Try to run BigQuery query
if command -v bq >/dev/null 2>&1; then
    # BigQuery outputs newline-delimited JSON (NDJSON), not a JSON array
    # Use jq -s to slurp it into an array
    BILLING_DATA_RAW=$(bq query --format=json --use_legacy_sql=false "$QUERY" 2>/dev/null || echo "")
    
    # Check if we got any data
    if [ -n "$BILLING_DATA_RAW" ] && [ "$BILLING_DATA_RAW" != "[]" ]; then
        # Try to parse as NDJSON and convert to array
        if command -v jq >/dev/null 2>&1; then
            # Check if already a valid JSON array before using jq -s
            # BigQuery --format=json returns a JSON array, but jq -s wraps it again
            if echo "$BILLING_DATA_RAW" | jq -e 'type == "array"' >/dev/null 2>&1; then
                # Already an array, use as-is to avoid double-nesting
                BILLING_DATA="$BILLING_DATA_RAW"
            else
                # NDJSON or other format, convert to array with jq -s (slurp)
                BILLING_DATA=$(echo "$BILLING_DATA_RAW" | jq -s '.' 2>/dev/null || echo "[]")
            fi
            
            if [ "$BILLING_DATA" != "[]" ] && [ -n "$BILLING_DATA" ]; then
                echo -e "${GREEN}✓ Successfully retrieved billing data from BigQuery${NC}"
                echo ""
                
                # Show data count
                DATA_COUNT=$(echo "$BILLING_DATA" | jq 'length' 2>/dev/null || echo "0")
                echo "Retrieved $DATA_COUNT record(s)"
                echo ""
                
                # Debug mode - show raw data if DEBUG=1
                if [ "${DEBUG:-0}" = "1" ]; then
                    echo "=== DEBUG: Raw BigQuery Output ==="
                    echo "$BILLING_DATA_RAW"
                    echo "==================================="
                    echo ""
                    echo "=== DEBUG: Parsed BILLING_DATA ==="
                    echo "$BILLING_DATA"
                    echo "==================================="
                    echo ""
                fi
                
                # Always show first record structure for debugging
                echo "=== First Record Structure ==="
                echo "$BILLING_DATA" | jq '.[0]' 2>/dev/null || echo "Unable to parse first record"
                echo "=============================="
                echo ""
                
                # Debug: Check if array access works
                FIRST_CHECK=$(echo "$BILLING_DATA" | jq -e '.[0]' 2>/dev/null)
                if [ $? -eq 0 ]; then
                    echo "✓ Array access works, proceeding with parsing..."
                    echo ""
                else
                    echo "✗ Array access failed, data structure might be unexpected"
                    echo ""
                fi
                
                # Parse and display results
                echo "==================================="
                echo "ACTUAL COSTS BY SERVICE"
                echo "==================================="
                echo ""
                
                # Check if data is valid before processing
                if echo "$BILLING_DATA" | jq -e '.[0]' >/dev/null 2>&1; then
                    # Parse with safe field access - process iteratively for reliability
                    # Note: BigQuery returns numbers as strings, need to convert
                    echo "Parsing billing data..."
                    
                    # Get record count for progress
                    RECORD_COUNT=$(echo "$BILLING_DATA" | jq 'length' 2>/dev/null || echo "0")
                    echo "Processing $RECORD_COUNT records..."
                    echo ""
                    
                    # Process each record individually to avoid hanging on large datasets
                    RECORD_NUM=0
                    TOTAL_COST=0
                    
                    while true; do
                        # Get one record at a time (don't use -r here, we need JSON not string)
                        RECORD=$(echo "$BILLING_DATA" | jq ".[$RECORD_NUM] // empty" 2>/dev/null)
                        
                        # Break if no more records
                        if [ -z "$RECORD" ] || [ "$RECORD" = "null" ]; then
                            break
                        fi
                        
                        # Extract fields safely
                        SERVICE=$(echo "$RECORD" | jq -r '.service // "Unknown"' 2>/dev/null || echo "Unknown")
                        SKU=$(echo "$RECORD" | jq -r '.sku // "Unknown"' 2>/dev/null || echo "Unknown")
                        COST=$(echo "$RECORD" | jq -r '.total_cost // "0"' 2>/dev/null || echo "0")
                        USAGE=$(echo "$RECORD" | jq -r '.usage_amount // "0"' 2>/dev/null || echo "0")
                        UNIT=$(echo "$RECORD" | jq -r '.usage_unit // "units"' 2>/dev/null || echo "units")
                        
                        # Convert cost to number and format
                        COST_NUM=$(echo "$COST" | awk '{printf "%.2f", $1}' 2>/dev/null || echo "0.00")
                        
                        # Display the record
                        echo "$SERVICE - $SKU: \$$COST_NUM ($USAGE $UNIT)"
                        
                        # Add to total (use bc for floating point)
                        TOTAL_COST=$(echo "$TOTAL_COST + $COST" | bc -l 2>/dev/null || echo "$TOTAL_COST")
                        
                        RECORD_NUM=$((RECORD_NUM + 1))
                    done
                    
                    echo ""
                    echo "==================================="
                    echo "TOTAL COST"
                    echo "==================================="
                    # Format total
                    TOTAL_FORMATTED=$(echo "$TOTAL_COST" | awk '{printf "%.2f", $1}' 2>/dev/null || echo "0.00")
                    echo "Total actual cost: \$$TOTAL_FORMATTED"
                    echo ""
                    
                    # Add cost breakdown summary
                    echo "==================================="
                    echo "COST BREAKDOWN BY SERVICE"
                    echo "==================================="
                    echo ""
                    
                    # Calculate breakdown from BILLING_DATA
                    if command -v jq >/dev/null 2>&1; then
                        # Cloud Run costs
                        CLOUD_RUN_COST=$(echo "$BILLING_DATA" | jq -r '[.[] | select(.service == "Cloud Run") | .total_cost | tonumber] | add // 0' 2>/dev/null || echo "0")
                        CLOUD_RUN_FORMATTED=$(echo "$CLOUD_RUN_COST" | awk '{printf "%.2f", $1}')
                        CLOUD_RUN_PCT=$(echo "scale=1; ($CLOUD_RUN_COST / $TOTAL_COST) * 100" | bc -l 2>/dev/null || echo "0")
                        
                        # Cloud Storage costs
                        STORAGE_COST=$(echo "$BILLING_DATA" | jq -r '[.[] | select(.service == "Cloud Storage") | .total_cost | tonumber] | add // 0' 2>/dev/null || echo "0")
                        STORAGE_FORMATTED=$(echo "$STORAGE_COST" | awk '{printf "%.2f", $1}')
                        STORAGE_PCT=$(echo "scale=1; ($STORAGE_COST / $TOTAL_COST) * 100" | bc -l 2>/dev/null || echo "0")
                        
                        # Artifact Registry costs
                        REGISTRY_COST=$(echo "$BILLING_DATA" | jq -r '[.[] | select(.service == "Artifact Registry") | .total_cost | tonumber] | add // 0' 2>/dev/null || echo "0")
                        REGISTRY_FORMATTED=$(echo "$REGISTRY_COST" | awk '{printf "%.2f", $1}')
                        REGISTRY_PCT=$(echo "scale=1; ($REGISTRY_COST / $TOTAL_COST) * 100" | bc -l 2>/dev/null || echo "0")
                        
                        # Cloud Scheduler costs
                        SCHEDULER_COST=$(echo "$BILLING_DATA" | jq -r '[.[] | select(.service == "Cloud Scheduler") | .total_cost | tonumber] | add // 0' 2>/dev/null || echo "0")
                        SCHEDULER_FORMATTED=$(echo "$SCHEDULER_COST" | awk '{printf "%.2f", $1}')
                        
                        echo "Cloud Run:          \$$CLOUD_RUN_FORMATTED ($CLOUD_RUN_PCT%)"
                        echo "Cloud Storage:      \$$STORAGE_FORMATTED ($STORAGE_PCT%)"
                        echo "Artifact Registry:  \$$REGISTRY_FORMATTED ($REGISTRY_PCT%)"
                        echo "Cloud Scheduler:    \$$SCHEDULER_FORMATTED"
                        echo ""
                        
                        # Add optimization insights
                        echo "==================================="
                        echo "OPTIMIZATION INSIGHTS"
                        echo "==================================="
                        echo ""
                        
                        # Check if Cloud Run is the main cost driver
                        if [ "$(echo "$CLOUD_RUN_COST > ($TOTAL_COST * 0.5)" | bc -l 2>/dev/null)" = "1" ]; then
                            echo "💡 Cloud Run accounts for ${CLOUD_RUN_PCT}% of costs"
                            echo "   Consider: Scale-to-zero, reduce min instances, optimize job duration"
                            echo ""
                        fi
                        
                        # Check storage costs
                        if [ "$(echo "$STORAGE_COST > 2" | bc -l 2>/dev/null)" = "1" ]; then
                            echo "💡 Storage costs: \$$STORAGE_FORMATTED"
                            echo "   Consider: Lifecycle policies, delete unused data, use cheaper storage classes"
                            echo ""
                        fi
                        
                        # Check registry costs
                        if [ "$(echo "$REGISTRY_COST > 5" | bc -l 2>/dev/null)" = "1" ]; then
                            echo "💡 Artifact Registry costs: \$$REGISTRY_FORMATTED"
                            echo "   Consider: Clean up old images, keep only recent versions"
                            echo ""
                        fi
                        
                        # Monthly projection
                        MONTHLY_PROJECTION=$(echo "$TOTAL_COST * 30 / $DAYS_BACK" | bc -l 2>/dev/null || echo "0")
                        MONTHLY_FORMATTED=$(echo "$MONTHLY_PROJECTION" | awk '{printf "%.2f", $1}')
                        echo "📊 Monthly projection (based on last $DAYS_BACK days): \$$MONTHLY_FORMATTED"
                        echo ""
                    fi
                else
                    USE_ALTERNATIVE=true
                fi
            else
                echo -e "${YELLOW}Warning: BigQuery billing export not configured or no data available${NC}"
                echo ""
                USE_ALTERNATIVE=true
            fi
        else
            echo -e "${YELLOW}Warning: jq command not found${NC}"
            USE_ALTERNATIVE=true
        fi
    else
        echo -e "${YELLOW}Warning: BigQuery billing export not configured or no data available${NC}"
        echo ""
        USE_ALTERNATIVE=true
    fi
else
    echo -e "${YELLOW}Warning: bq command not found${NC}"
    USE_ALTERNATIVE=true
fi

#############################################
# Alternative: Use gcloud billing API directly
#############################################
if [ "${USE_ALTERNATIVE:-false}" = "true" ]; then
    echo -e "${BOLD}Step 3: Using alternative method (Cloud Billing API)...${NC}"
    echo ""
    
    # Note: This requires the billing.budgets.list permission
    # The actual cost retrieval via gcloud is limited - BigQuery export is preferred
    
    echo -e "${YELLOW}LIMITATION: gcloud billing commands don't provide detailed cost breakdowns${NC}"
    echo "To get actual costs, you need to:"
    echo "1. Enable BigQuery billing export in GCP Console"
    echo "2. Wait 24 hours for data to populate"
    echo "3. Run this script again"
    echo ""
    echo "For now, showing estimated costs based on actual usage..."
    echo ""
fi

#############################################
# Fallback: Show actual usage with pricing
#############################################
echo -e "${BOLD}Step 4: Training Job Activity${NC}"
echo ""

# Get actual Cloud Run execution statistics
echo "Cloud Run Training Jobs (Period: $START_DATE to $END_DATE):"
echo "-----------------------------------------------------------"

for job_name in "mmm-app-training" "mmm-app-dev-training"; do
    echo ""
    echo "Job: $job_name"
    
    executions=$(gcloud run jobs executions list \
        --job="$job_name" \
        --project="$PROJECT_ID" \
        --filter="metadata.createTime>=\\\"$START_DATE\\\"" \
        --format="json" 2>/dev/null || echo "[]")
    
    if [ "$executions" != "[]" ] && [ -n "$executions" ]; then
        count=$(echo "$executions" | jq 'length')
        echo "  Executions: $count"
        
        # Calculate actual duration from logs
        if command -v jq >/dev/null 2>&1; then
            total_duration=0
            for exec in $(echo "$executions" | jq -r '.[].name'); do
                start_time=$(echo "$executions" | jq -r ".[] | select(.name==\"$exec\") | .metadata.createTime")
                end_time=$(echo "$executions" | jq -r ".[] | select(.name==\"$exec\") | .status.completionTime // empty")
                
                if [ -n "$start_time" ] && [ -n "$end_time" ]; then
                    # Calculate duration
                    if date --version >/dev/null 2>&1; then
                        start_sec=$(date -d "${start_time}" +%s 2>/dev/null || echo 0)
                        end_sec=$(date -d "${end_time}" +%s 2>/dev/null || echo 0)
                    else
                        start_sec=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${start_time%.*}" +%s 2>/dev/null || echo 0)
                        end_sec=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${end_time%.*}" +%s 2>/dev/null || echo 0)
                    fi
                    
                    if [ "$start_sec" -gt 0 ] && [ "$end_sec" -gt 0 ]; then
                        duration=$((end_sec - start_sec))
                        total_duration=$((total_duration + duration))
                    fi
                fi
            done
            
            if [ "$total_duration" -gt 0 ]; then
                avg_duration=$((total_duration / count))
                echo "  Total compute time: $((total_duration / 60)) minutes"
                echo "  Average per job: $((avg_duration / 60)) minutes"
                
                # Get resource config
                cpu_config=$(echo "$executions" | jq -r '.[0].spec.template.spec.containers[0].resources.limits.cpu // "8.0"' | grep -o '[0-9.]*')
                memory_config=$(echo "$executions" | jq -r '.[0].spec.template.spec.containers[0].resources.limits.memory // "32Gi"' | grep -o '[0-9]*')
                
                # Calculate actual cost
                cpu_cost=$(echo "scale=2; $total_duration * $cpu_config * 0.000024" | bc -l 2>/dev/null || echo "0")
                memory_cost=$(echo "scale=2; $total_duration * $memory_config * 0.0000025" | bc -l 2>/dev/null || echo "0")
                total_cost=$(echo "scale=2; $cpu_cost + $memory_cost" | bc -l 2>/dev/null || echo "0")
                
                echo "  Actual cost estimate:"
                printf "    CPU: \$%.2f\n" "$cpu_cost"
                printf "    Memory: \$%.2f\n" "$memory_cost"
                printf "    Total: \$%.2f\n" "$total_cost"
            fi
        fi
    else
        echo "  No training runs in the last $DAYS_BACK days"
        echo "  (This is normal if no MMM experiments were conducted)"
    fi
done

echo ""
echo "Note: Training costs are included in the Cloud Run costs above."
echo ""
echo "==========================================="
echo "SUMMARY & NEXT STEPS"
echo "==========================================="
echo ""

# Check if BigQuery data was successfully retrieved
if [ "${USE_ALTERNATIVE:-false}" != "true" ] && [ -n "$BILLING_DATA" ] && [ "$BILLING_DATA" != "[]" ]; then
    echo "✅ Successfully retrieved actual billing data from BigQuery"
    echo ""
    echo "Period analyzed: $START_DATE to $END_DATE ($DAYS_BACK days)"
    echo "Total cost: \$$TOTAL_FORMATTED"
    echo ""
    echo "💰 Cost Optimization Opportunities:"
    echo ""
    echo "1. Review the cost breakdown above to identify top drivers"
    echo "2. Check COST_OPTIMIZATION.md for detailed optimization strategies"
    echo "3. Compare actual costs with projected costs from infrastructure changes"
    echo "4. Run this script monthly to track cost trends"
    echo ""
    echo "📊 To analyze different time periods:"
    echo "  DAYS_BACK=7 ./scripts/get_actual_costs.sh   # Last 7 days"
    echo "  DAYS_BACK=90 ./scripts/get_actual_costs.sh  # Last 90 days"
    echo ""
    echo "📈 View detailed billing reports in GCP Console:"
    echo "  https://console.cloud.google.com/billing/reports"
    echo "  Filter by Project: $PROJECT_ID"
else
    echo "⚠️  BigQuery billing export needs to be configured"
    echo ""
    echo "Current configuration:"
    echo "  Dataset: $DATASET_ID"
    echo "  Table: $TABLE_NAME"
    echo "  Project: $PROJECT_ID"
    echo ""
    echo "To enable BigQuery billing export:"
    echo ""
    echo "1. Go to: https://console.cloud.google.com/billing"
    echo "2. Select your billing account"
    echo "3. Go to 'Billing export' → 'BigQuery export'"
    echo "4. Verify dataset and table are configured correctly"
    echo "5. Wait 24 hours for data to populate"
    echo ""
    echo "You can override defaults with environment variables:"
    echo "  BILLING_DATASET=your_dataset ./scripts/get_actual_costs.sh"
    echo "  BILLING_ACCOUNT_NUM=your_account_id ./scripts/get_actual_costs.sh"
    echo ""
    echo "View costs in GCP Console → Billing → Reports"
fi
echo ""
