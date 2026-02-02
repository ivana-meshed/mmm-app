#!/bin/bash

# Artifact Registry Cleanup Script
# This script cleans up old Docker images from Google Artifact Registry
# to reduce storage costs
#
# CRITICAL: Artifact Registry currently has 9,228 images (122.58 GB)
# costing $12.26/month. This cleanup can reduce it to ~$0.50-1.00/month.

set -e

PROJECT_ID="datawarehouse-422511"
REPO="mmm-repo"
LOCATION="europe-west1"
KEEP_LAST_N="${KEEP_LAST_N:-10}"  # Keep last N images per image type (default: 10)
DRY_RUN="${DRY_RUN:-true}"  # Set to 'false' to actually delete images

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "Artifact Registry Cleanup Script"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Repository: $REPO"
echo "Location: $LOCATION"
echo "Keep last: $KEEP_LAST_N images per type"
echo "Dry run: $DRY_RUN"
echo ""

if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}⚠️  DRY RUN MODE - No images will be deleted${NC}"
    echo "Set DRY_RUN=false to actually delete images"
    echo ""
fi

# Image types
IMAGES=("mmm-app" "mmm-web" "mmm-training" "mmm-training-base")

TOTAL_IMAGES_TO_DELETE=0
TOTAL_SIZE_TO_FREE=0

for IMAGE in "${IMAGES[@]}"; do
  echo -e "${GREEN}Processing: $IMAGE${NC}"
  
  # Get total count of images
  TOTAL_COUNT=$(gcloud artifacts docker images list \
    $LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE \
    --include-tags \
    --format="value(version)" 2>/dev/null | wc -l)
  
  echo "  Total images: $TOTAL_COUNT"
  
  if [ "$TOTAL_COUNT" -le "$KEEP_LAST_N" ]; then
    echo "  ✓ Keeping all images (below threshold)"
    echo ""
    continue
  fi
  
  IMAGES_TO_DELETE=$((TOTAL_COUNT - KEEP_LAST_N))
  echo "  Images to delete: $IMAGES_TO_DELETE"
  
  # Get oldest images (excluding last N)
  IMAGES_LIST=$(gcloud artifacts docker images list \
    $LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE \
    --include-tags \
    --sort-by=~CREATE_TIME \
    --format="value(version)" 2>/dev/null | \
    tail -n +$((KEEP_LAST_N + 1)))
  
  if [ -z "$IMAGES_LIST" ]; then
    echo "  ✓ No images to delete"
    echo ""
    continue
  fi
  
  COUNT=0
  while IFS= read -r IMAGE_VERSION; do
    COUNT=$((COUNT + 1))
    FULL_IMAGE="$LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE@$IMAGE_VERSION"
    
    if [ "$DRY_RUN" = "true" ]; then
      echo "  [DRY RUN] Would delete: $IMAGE@$IMAGE_VERSION"
    else
      echo "  Deleting: $IMAGE@$IMAGE_VERSION"
      gcloud artifacts docker images delete "$FULL_IMAGE" --quiet 2>/dev/null || {
        echo -e "  ${RED}Failed to delete $IMAGE@$IMAGE_VERSION${NC}"
      }
    fi
  done <<< "$IMAGES_LIST"
  
  TOTAL_IMAGES_TO_DELETE=$((TOTAL_IMAGES_TO_DELETE + COUNT))
  echo ""
done

echo "=========================================="
echo "Summary"
echo "=========================================="
echo "Total images to delete: $TOTAL_IMAGES_TO_DELETE"

if [ "$DRY_RUN" = "true" ]; then
  echo ""
  echo -e "${YELLOW}This was a DRY RUN. No images were deleted.${NC}"
  echo ""
  echo "To actually delete images, run:"
  echo "  DRY_RUN=false $0"
  echo ""
  echo "Or to keep more/fewer versions:"
  echo "  DRY_RUN=false KEEP_LAST_N=5 $0"
else
  echo ""
  echo -e "${GREEN}✓ Cleanup complete!${NC}"
  echo ""
  echo "Verify the cleanup:"
  echo "  gcloud artifacts docker images list $LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO"
  echo ""
  echo "Check repository size:"
  echo "  gcloud artifacts repositories describe $REPO --location=$LOCATION"
fi

echo ""
echo "Expected monthly savings: \$11-12 (reducing from \$12.26 to \$0.50-1.00)"
echo ""
