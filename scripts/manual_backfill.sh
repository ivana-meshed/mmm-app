#!/bin/bash
# Manual script to backfill model summaries for existing runs
# This can be run from Cloud Shell or any environment with gcloud access

set -e

# Configuration
PROJECT_ID="${PROJECT_ID:-datawarehouse-422511}"
BUCKET="${BUCKET:-mmm-app-output}"
REGION="${REGION:-europe-west1}"
TRAINING_JOB="${TRAINING_JOB:-mmm-app-training}"

echo "=========================================="
echo "Model Summary Backfill Script"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Bucket: $BUCKET"
echo "Region: $REGION"
echo "Training Job: $TRAINING_JOB"
echo "=========================================="
echo ""

# Check if gcloud is available
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud command not found"
    echo "Please install Google Cloud SDK or run this from Cloud Shell"
    exit 1
fi

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" > /dev/null 2>&1; then
    echo "❌ Error: Not authenticated with gcloud"
    echo "Run: gcloud auth login"
    exit 1
fi

echo "🔄 Starting backfill process..."
echo "This will:"
echo "  1. Generate model_summary.json for all runs missing it"
echo "  2. Aggregate summaries by country into model_summary/{country}/"
echo ""
echo "Note: This may take a while depending on the number of runs"
echo "Estimated time: ~1-5 minutes per run without summary"
echo ""

# Execute the backfill
gcloud run jobs execute "$TRAINING_JOB" \
  --region "$REGION" \
  --task-timeout 3600 \
  --args="Rscript,/app/backfill_summaries.R,--bucket,$BUCKET,--project,$PROJECT_ID" \
  --wait

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ Backfill completed successfully!"
    echo ""
    echo "Summaries are now available at:"
    echo "  - Individual: gs://$BUCKET/robyn/{revision}/{country}/{timestamp}/model_summary.json"
    echo "  - Aggregated: gs://$BUCKET/model_summary/{country}/summary.json"
    echo ""
else
    echo ""
    echo "❌ Backfill failed with exit code: $EXIT_CODE"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check Cloud Run job logs:"
    echo "     gcloud logging read \"resource.type=cloud_run_job AND resource.labels.job_name=$TRAINING_JOB\" --limit 50"
    echo ""
    echo "  2. Verify the training job exists:"
    echo "     gcloud run jobs describe $TRAINING_JOB --region $REGION"
    echo ""
    echo "  3. Check GCS bucket permissions:"
    echo "     gsutil ls gs://$BUCKET/robyn/"
    echo ""
    exit $EXIT_CODE
fi
