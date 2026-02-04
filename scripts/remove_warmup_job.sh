#!/bin/bash

# Script to remove the warmup scheduler job
# This job keeps Cloud Run services warm but adds cost
#
# Savings: ~$0-60/year (depending on what it invokes)
# Trade-off: 2-3 second cold starts on first request after idle period

set -e

PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
WARMUP_JOB_NAME="mmm-warmup-job"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo "=========================================="
echo "Warmup Job Removal Script"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Job: $WARMUP_JOB_NAME"
echo ""

# Check if job exists
echo "Checking if warmup job exists..."
JOB_EXISTS=$(gcloud scheduler jobs list \
  --location="$REGION" \
  --filter="name:$WARMUP_JOB_NAME" \
  --format="value(name)" 2>/dev/null || echo "")

if [ -z "$JOB_EXISTS" ]; then
  echo -e "${GREEN}✓ Warmup job does not exist or already removed${NC}"
  echo ""
  echo "Nothing to do!"
  exit 0
fi

echo -e "${BLUE}Found warmup job: $WARMUP_JOB_NAME${NC}"
echo ""

# Get job details
JOB_INFO=$(gcloud scheduler jobs describe "$WARMUP_JOB_NAME" \
  --location="$REGION" \
  --format="json" 2>/dev/null)

SCHEDULE=$(echo "$JOB_INFO" | jq -r '.schedule')
STATE=$(echo "$JOB_INFO" | jq -r '.state')
TARGET=$(echo "$JOB_INFO" | jq -r '.httpTarget.uri // "Unknown"')

echo "Job Details:"
echo "  Schedule: $SCHEDULE"
echo "  State: $STATE"
echo "  Target: $TARGET"
echo ""

# Explain impact
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Impact of Removing Warmup Job${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "PROS:"
echo "  ✓ Reduces Cloud Run invocations"
echo "  ✓ Potential cost savings: ~\$0-60/year"
echo "  ✓ Allows services to scale to zero when idle"
echo "  ✓ More efficient resource usage"
echo ""
echo "CONS:"
echo "  ✗ First request after idle will have 2-3s cold start"
echo "  ✗ Users may notice slight delay on first access"
echo ""
echo "RECOMMENDATION:"
echo "  Remove if:"
echo "    • Application is used intermittently (not 24/7)"
echo "    • 2-3s cold start is acceptable"
echo "    • Cost optimization is priority"
echo ""
echo "  Keep if:"
echo "    • Application needs instant response at all times"
echo "    • Cold starts are unacceptable"
echo "    • Users expect <1s response always"
echo ""

# Ask for confirmation
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${RED}WARNING: This will remove the warmup job${NC}"
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
read -p "Are you sure you want to remove the warmup job? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
  echo ""
  echo "Operation cancelled. Warmup job not removed."
  exit 0
fi

echo ""
echo "Removing warmup job..."

# Delete the job
gcloud scheduler jobs delete "$WARMUP_JOB_NAME" \
  --location="$REGION" \
  --quiet

if [ $? -eq 0 ]; then
  echo ""
  echo -e "${GREEN}✓ Warmup job successfully removed${NC}"
  echo ""
  echo "Next steps:"
  echo "  1. Monitor application performance"
  echo "  2. Check if cold starts are acceptable"
  echo "  3. Verify cost reduction in billing"
  echo ""
  echo "To re-create the warmup job (if needed):"
  echo "  gcloud scheduler jobs create http $WARMUP_JOB_NAME \\"
  echo "    --location=$REGION \\"
  echo "    --schedule='*/5 * * * *' \\"
  echo "    --uri='[YOUR_SERVICE_URL]' \\"
  echo "    --http-method=GET"
  echo ""
else
  echo ""
  echo -e "${RED}✗ Failed to remove warmup job${NC}"
  echo "Check GCP permissions and try again"
  exit 1
fi
