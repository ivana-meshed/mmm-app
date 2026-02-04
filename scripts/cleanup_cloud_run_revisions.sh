#!/bin/bash

# Cleanup old Cloud Run revisions to reduce clutter and potential costs
# Keeps only the most recent N revisions

set -e

PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
REGION="${REGION:-europe-west1}"
KEEP_LAST_N="${KEEP_LAST_N:-10}"
DRY_RUN="${DRY_RUN:-true}"

echo "========================================"
echo "Cloud Run Revision Cleanup Script"
echo "========================================"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Keep last: $KEEP_LAST_N revisions per service"
echo "Dry run: $DRY_RUN"
echo ""

# Services to clean
SERVICES=("mmm-app-web" "mmm-app-dev-web")

for SERVICE in "${SERVICES[@]}"; do
  echo "Processing: $SERVICE"
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
  
  # Get all revisions sorted by creation time (newest first)
  revisions=$(gcloud run revisions list \
    --service=$SERVICE \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(metadata.name)" \
    --sort-by="~metadata.creationTimestamp" \
    --limit=1000)
  
  total_count=$(echo "$revisions" | wc -l | tr -d ' ')
  delete_count=$((total_count - KEEP_LAST_N))
  
  echo "  Total revisions: $total_count"
  
  if [ "$delete_count" -le 0 ]; then
    echo "  No revisions to delete (≤ $KEEP_LAST_N exist)"
    echo ""
    continue
  fi
  
  echo "  Revisions to delete: $delete_count"
  echo ""
  
  # Get revisions to delete (skip first N)
  revisions_to_delete=$(echo "$revisions" | tail -n +$((KEEP_LAST_N + 1)))
  
  # Delete old revisions
  deleted=0
  failed=0
  
  for revision in $revisions_to_delete; do
    if [ "$DRY_RUN" = "true" ]; then
      echo "  [DRY RUN] Would delete: $revision"
      deleted=$((deleted + 1))
    else
      echo "  Deleting: $revision"
      if gcloud run revisions delete $revision \
        --service=$SERVICE \
        --region=$REGION \
        --project=$PROJECT_ID \
        --quiet 2>/dev/null; then
        echo "  ✓ Deleted successfully"
        deleted=$((deleted + 1))
      else
        echo "  ✗ Failed to delete $revision"
        failed=$((failed + 1))
      fi
    fi
  done
  
  echo ""
  echo "  Summary:"
  echo "    Kept: $KEEP_LAST_N revisions"
  echo "    Deleted: $deleted revisions"
  if [ "$failed" -gt 0 ]; then
    echo "    Failed: $failed revisions"
  fi
  echo ""
done

echo "========================================"
echo "Cleanup Complete"
echo "========================================"

if [ "$DRY_RUN" = "true" ]; then
  echo ""
  echo "This was a DRY RUN. No revisions were actually deleted."
  echo "To perform actual cleanup, run:"
  echo "  DRY_RUN=false ./scripts/cleanup_cloud_run_revisions.sh"
fi

echo ""
echo "To adjust the number of revisions to keep:"
echo "  KEEP_LAST_N=5 DRY_RUN=false ./scripts/cleanup_cloud_run_revisions.sh"
