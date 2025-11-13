# Model Summary Documentation

## Overview

The MMM application now automatically generates summary files for each model training run. These summaries capture key information about candidate models, Pareto models, and performance metrics extracted from Robyn's `OutputCollect.RDS` file.

## Storage Location

### Individual Run Summaries

Each training run generates a `model_summary.json` file stored alongside other run artifacts:

```
gs://{bucket}/robyn/{revision}/{country}/{timestamp}/model_summary.json
```

**Example:**
```
gs://mmm-app-output/robyn/v1/US/1234567890/model_summary.json
```

### Aggregated Country Summaries

Aggregated summaries combining all runs for a country are stored at:

```
gs://{bucket}/robyn-summaries/{country}/summary.json
```

Or with revision filtering:

```
gs://{bucket}/robyn-summaries/{country}/{revision}_summary.json
```

**Examples:**
```
gs://mmm-app-output/robyn-summaries/US/summary.json
gs://mmm-app-output/robyn-summaries/UK/v1_summary.json
```

## Summary File Schema

### Individual Run Summary (`model_summary.json`)

```json
{
  "country": "US",
  "revision": "v1",
  "timestamp": "1234567890",
  "created_at": "2025-11-13T12:00:00",
  "training_time_mins": 45.5,
  "has_pareto_models": true,
  "pareto_model_count": 5,
  "candidate_model_count": 100,
  
  "best_model": {
    "model_id": "1_234_5",
    "nrmse": 0.0523,
    "decomp_rssd": 0.0234,
    "rsq_train": 0.9456,
    "nrmse_train": 0.0512,
    "rsq_val": 0.9234,
    "nrmse_val": 0.0567,
    "rsq_test": 0.9123,
    "nrmse_test": 0.0589,
    "mape": 5.23
  },
  
  "pareto_models": [
    {
      "model_id": "1_234_5",
      "nrmse": 0.0523,
      "decomp_rssd": 0.0234,
      "rsq_train": 0.9456,
      "nrmse_train": 0.0512,
      "rsq_val": 0.9234,
      "nrmse_val": 0.0567,
      "rsq_test": 0.9123,
      "nrmse_test": 0.0589,
      "mape": 5.23,
      "robyn_pareto_front": 1
    }
    // ... more Pareto models (up to 10)
  ],
  
  "candidate_models": [
    {
      "model_id": "1_234_5",
      "nrmse": 0.0523,
      "decomp_rssd": 0.0234,
      "rsq_train": 0.9456,
      "nrmse_train": 0.0512,
      "rsq_val": 0.9234,
      "nrmse_val": 0.0567,
      "rsq_test": 0.9123,
      "nrmse_test": 0.0589,
      "mape": 5.23,
      "is_pareto": true
    }
    // ... more candidate models (up to 100)
  ],
  
  "input_metadata": {
    "dep_var": "UPLOAD_VALUE",
    "dep_var_type": "revenue",
    "adstock": "geometric",
    "window_start": "2024-01-01",
    "window_end": "2024-12-31",
    "paid_media_vars": ["GA_PAID_COST", "FB_COST"],
    "organic_vars": ["ORGANIC_TRAFFIC"],
    "context_vars": ["SEASONALITY", "TREND"],
    "factor_vars": []
  }
}
```

### Aggregated Country Summary (`summary.json`)

```json
{
  "country": "US",
  "revision": "v1",
  "aggregated_at": "2025-11-13T12:00:00",
  "total_runs": 25,
  "runs_with_pareto_models": 23,
  
  "best_model_overall": {
    "model_id": "1_234_5",
    "nrmse": 0.0423,
    "decomp_rssd": 0.0198,
    "rsq_train": 0.9567,
    "nrmse_train": 0.0412,
    "rsq_val": 0.9345,
    "nrmse_val": 0.0456,
    "rsq_test": 0.9234,
    "nrmse_test": 0.0478,
    "mape": 4.56
  },
  
  "runs": [
    // Array of individual run summaries (full schema from above)
  ]
}
```

## Fields Captured

### Metadata Fields

| Field | Type | Description |
|-------|------|-------------|
| `country` | string | Country code for the model run |
| `revision` | string | Revision/version identifier |
| `timestamp` | string | Run timestamp |
| `created_at` | string | ISO timestamp when summary was created |
| `training_time_mins` | number | Training duration in minutes |

### Model Information

| Field | Type | Description |
|-------|------|-------------|
| `has_pareto_models` | boolean | Whether the run produced Pareto optimal models |
| `pareto_model_count` | integer | Number of Pareto models identified |
| `candidate_model_count` | integer | Total number of candidate models |

### Performance Metrics

All models (best, Pareto, and candidates) include these metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `model_id` | string | Unique model identifier from Robyn |
| `nrmse` | number | Normalized Root Mean Square Error (overall) |
| `decomp_rssd` | number | Decomposition Root Sum of Squared Deviations |
| `rsq_train` | number | R-squared on training set |
| `nrmse_train` | number | NRMSE on training set |
| `rsq_val` | number | R-squared on validation set |
| `nrmse_val` | number | NRMSE on validation set |
| `rsq_test` | number | R-squared on test set |
| `nrmse_test` | number | NRMSE on test set |
| `mape` | number | Mean Absolute Percentage Error |
| `robyn_pareto_front` | integer | Pareto front number (for Pareto models) |
| `is_pareto` | boolean | Whether model is on Pareto front (for candidates) |

### Input Metadata

Configuration used for the model run:

| Field | Type | Description |
|-------|------|-------------|
| `dep_var` | string | Dependent variable name |
| `dep_var_type` | string | Type of dependent variable (revenue, conversion, etc.) |
| `adstock` | string | Adstock type (geometric, weibull_cdf, weibull_pdf) |
| `window_start` | string | Training window start date |
| `window_end` | string | Training window end date |
| `paid_media_vars` | array | List of paid media variables |
| `organic_vars` | array | List of organic variables |
| `context_vars` | array | List of context variables |
| `factor_vars` | array | List of factor variables |

## Usage

### Automatic Generation

Summaries are automatically generated for every new model run. No manual intervention is required.

### Generating Summaries for Existing Models

To generate summaries for models that were run before this feature was added, you need an environment with R installed (to read RDS files).

**Important:** Backfilling requires R/Rscript to be available, as it needs to read the `OutputCollect.RDS` files. This cannot be done from the web container or CI/CD pipeline as they don't have R installed.

#### From Local Environment with R

```bash
# Ensure R is installed and Rscript is in PATH
# Install required R packages: jsonlite, optparse

# Generate summaries for all existing runs
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --generate-missing

# Generate summaries for a specific country
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --country US \
  --generate-missing
```

#### From a VM/Server with R Installed

If you need to backfill from a cloud environment:

```bash
# SSH into a VM with R installed, or use Cloud Shell with R
# Authenticate to GCP
gcloud auth application-default login

# Run the backfill script
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --project your-project-id \
  --generate-missing
```

### Creating Aggregated Country Summaries

```bash
# Aggregate all runs for all countries
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --aggregate

# Aggregate for a specific country
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --country US \
  --aggregate

# Aggregate for a specific country and revision
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --country US \
  --revision v1 \
  --aggregate
```

### Reading Summaries Programmatically

#### Python Example

```python
from google.cloud import storage
import json

def read_model_summary(bucket_name, run_path):
    """
    Read a model summary from GCS
    
    Args:
        bucket_name: GCS bucket name
        run_path: Path to run (e.g., "robyn/v1/US/1234567890")
    
    Returns:
        dict: Model summary
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"{run_path}/model_summary.json")
    
    content = blob.download_as_text()
    return json.loads(content)

# Usage
summary = read_model_summary("mmm-app-output", "robyn/v1/US/1234567890")
print(f"Best model: {summary['best_model']['model_id']}")
print(f"Has Pareto models: {summary['has_pareto_models']}")
print(f"Training time: {summary['training_time_mins']} minutes")
```

#### R Example

```r
library(googleCloudStorageR)
library(jsonlite)

# Authenticate
gcs_auth()
gcs_global_bucket("mmm-app-output")

# Read summary
run_path <- "robyn/v1/US/1234567890"
summary_json <- gcs_get_object(
  paste0(run_path, "/model_summary.json"),
  parseFunction = jsonlite::fromJSON
)

# Access data
print(paste("Best model:", summary_json$best_model$model_id))
print(paste("Has Pareto models:", summary_json$has_pareto_models))
print(paste("Pareto model count:", summary_json$pareto_model_count))
```

## Benefits

1. **Historical Tracking**: Track model performance over time
2. **Easy Comparison**: Compare models across runs without loading full RDS files
3. **Quick Access**: Get key metrics without processing large files
4. **Pareto Identification**: Quickly identify which runs produced Pareto optimal models
5. **Country Aggregation**: View all models for a country in a single file
6. **Lightweight**: JSON format is easy to parse and query

## Integration Points

### Streamlit UI

The summaries can be used in the Streamlit UI to:
- Display model performance history
- Compare models across runs
- Filter and search models by metrics
- Visualize performance trends

### Monitoring and Alerts

Summaries enable:
- Automated quality checks on new models
- Alerts when model performance degrades
- Tracking of Pareto model production rate
- Historical performance dashboards

## File Size Considerations

- Individual run summaries: ~10-50 KB (depends on candidate count)
- Aggregated country summaries: ~100 KB - 5 MB (depends on number of runs)
- Candidate models are limited to 100 per run to keep file size reasonable
- Pareto models are limited to 10 per run

## Maintenance

Summary files are immutable once created. To refresh:
1. Delete old summaries from GCS
2. Re-run the generation script
3. The system will create new summaries from the RDS files

## Troubleshooting

### Summary not generated

Check the run logs for errors in the "GENERATE MODEL SUMMARY" section. Common issues:
- OutputCollect.RDS corrupted or incomplete
- Insufficient permissions to write to GCS
- R script errors (check console.log)
- Missing `extract_model_summary.R` file in Docker container (check Dockerfile)

If the log shows "Could not find extract_model_summary.R", ensure that:
1. The training Docker image includes `extract_model_summary.R`
2. The Dockerfile has: `COPY r/extract_model_summary.R /app/extract_model_summary.R`
3. The image was rebuilt and redeployed after adding the helper file

### Missing summaries for old runs

Run the backfill script:
```bash
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --generate-missing
```

### Inconsistent data in aggregated summary

Re-generate the aggregated summary:
```bash
python3 scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --country US \
  --aggregate
```
