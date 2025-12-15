# OutputModels Parquet Data Extraction

## Overview

The MMM application automatically extracts compressed data from `OutputCollect.RDS` files into separate parquet files for easier querying and analysis. This feature extracts key data components that are commonly needed for downstream analysis without requiring users to load the full RDS file in R.

**Note:** The data is extracted from `OutputCollect.RDS` (the result of `robyn_outputs()`), not `OutputModels.RDS` (the result of `robyn_run()`). OutputCollect contains the processed model results including decomposition and performance metrics.

## What Gets Extracted

From each `OutputCollect.RDS` file, the following data is extracted into parquet format:

1. **`xDecompAgg.parquet`** - Aggregated decomposition data
2. **`resultHypParam.parquet`** - Model hyperparameters and performance metrics
3. **`mediaVecCollect.parquet`** - Media vector collection data
4. **`xDecompVecCollect.parquet`** - Decomposition vector collection data

## Storage Location

For each model run, parquet files are stored in an `output_models_data/` subdirectory:

```
gs://{bucket}/robyn/{revision}/{country}/{timestamp}/output_models_data/
  ├── xDecompAgg.parquet
  ├── resultHypParam.parquet
  ├── mediaVecCollect.parquet
  └── xDecompVecCollect.parquet
```

**Example:**
```
gs://mmm-app-output/robyn/v1/US/1234567890/output_models_data/
  ├── xDecompAgg.parquet
  ├── resultHypParam.parquet
  ├── mediaVecCollect.parquet
  └── xDecompVecCollect.parquet
```

## Automatic Extraction

### New Model Runs

Starting from this deployment, all new model training runs will automatically:

1. Generate `OutputCollect.RDS` (from `robyn_outputs()`)
2. Extract the four parquet files immediately after
3. Upload both the RDS and parquet files to GCS

This happens automatically in `run_all.R` with no manual intervention required.

### Existing Model Runs (Backfill)

For existing model runs that have `OutputCollect.RDS`, you can use the backfill script to extract parquet data.

## Backfilling Existing Models

### One-Time Backfill Script

To extract parquet data for existing models in GCS:

```bash
# Backfill all existing models
python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output

# Backfill for a specific country
python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output \
  --country US

# Backfill for a specific country and revision
python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output \
  --country US \
  --revision v1

# Dry run to see what would be processed
python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output \
  --dry-run

# Process only the first 10 runs (for testing)
python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output \
  --limit 10
```

### Using Docker Container (Recommended for Production)

Since the backfill script requires R to read RDS files, it's recommended to run it inside the training container:

```bash
# Build and run in Docker (if R is not installed locally)
docker build -f docker/Dockerfile.training -t mmm-backfill .

docker run --rm \
  -v ~/.config/gcloud:/root/.config/gcloud \
  -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
  mmm-backfill \
  python3 scripts/backfill_output_models_parquet.py \
  --bucket mmm-app-output
```

### Using Cloud Run Job

To run the backfill as a one-time Cloud Run Job:

```bash
# Execute as a Cloud Run Job
gcloud run jobs execute mmm-app-training \
  --region europe-west1 \
  --args="python3,scripts/backfill_output_models_parquet.py,--bucket,mmm-app-output" \
  --wait

# For a specific country
gcloud run jobs execute mmm-app-training \
  --region europe-west1 \
  --args="python3,scripts/backfill_output_models_parquet.py,--bucket,mmm-app-output,--country,US" \
  --wait
```

## Usage

### Reading Parquet Files

#### Python Example

```python
import pandas as pd
from google.cloud import storage

def read_output_models_parquet(bucket_name, run_path, component):
    """
    Read a parquet component from OutputModels extraction.
    
    Args:
        bucket_name: GCS bucket name
        run_path: Path to run (e.g., "robyn/v1/US/1234567890")
        component: Component name ("xDecompAgg", "resultHypParam", etc.)
    
    Returns:
        pandas.DataFrame
    """
    gcs_path = f"gs://{bucket_name}/{run_path}/output_models_data/{component}.parquet"
    return pd.read_parquet(gcs_path)

# Usage
df_hyp = read_output_models_parquet(
    "mmm-app-output",
    "robyn/v1/US/1234567890",
    "resultHypParam"
)

print(f"Model performance metrics:\n{df_hyp.head()}")
```

#### R Example

```r
library(arrow)
library(googleCloudStorageR)

# Authenticate
gcs_auth()
gcs_global_bucket("mmm-app-output")

# Download and read parquet file
run_path <- "robyn/v1/US/1234567890"
local_file <- tempfile(fileext = ".parquet")

gcs_get_object(
  paste0(run_path, "/output_models_data/resultHypParam.parquet"),
  saveToDisk = local_file
)

df_hyp <- arrow::read_parquet(local_file)
print(head(df_hyp))
```

#### Direct from GCS (Arrow)

```python
import pyarrow.parquet as pq
from pyarrow import fs

# Connect to GCS
gcs = fs.GcsFileSystem()

# Read directly from GCS
table = pq.read_table(
    "mmm-app-output/robyn/v1/US/1234567890/output_models_data/resultHypParam.parquet",
    filesystem=gcs
)

df = table.to_pandas()
print(df.head())
```

## Benefits

1. **Easier Querying**: Parquet files can be queried with SQL engines (BigQuery, DuckDB, etc.)
2. **Faster Loading**: No need to load the entire RDS file in R to access specific data
3. **Language Agnostic**: Parquet can be read in Python, R, SQL, and many other languages
4. **Smaller File Size**: Compressed columnar format is more efficient than RDS for large datasets
5. **Selective Reading**: Read only the columns you need from each file
6. **Integration Ready**: Easy to integrate with data pipelines, dashboards, and BI tools

## Data Components

### xDecompAgg

Aggregated decomposition data showing the contribution of each media channel and factor.

**Typical columns:**
- `solID` - Model solution ID
- `channel` - Media channel or factor name
- `contribution` - Contribution value
- `spend` - Associated spend (for media channels)

### resultHypParam

Model hyperparameters and performance metrics for all candidate models.

**Typical columns:**
- `solID` - Model solution ID
- `rsq_train`, `rsq_val`, `rsq_test` - R-squared metrics
- `nrmse_train`, `nrmse_val`, `nrmse_test` - NRMSE metrics
- `decomp.rssd` - Decomposition RSSD
- `mape` - Mean Absolute Percentage Error
- `{channel}_alphas`, `{channel}_gammas`, `{channel}_thetas` - Hyperparameter values

### mediaVecCollect

Media response curves and saturation data.

**Typical columns:**
- `solID` - Model solution ID
- `channel` - Media channel name
- `spend` - Spend value
- `response` - Predicted response
- `carryover` - Carryover effect

### xDecompVecCollect

Time series decomposition vectors for each model component.

**Typical columns:**
- `solID` - Model solution ID
- `date` - Date
- `channel` - Channel or component name
- `value` - Decomposed value

## Troubleshooting

### Parquet files not created for new runs

Check the run logs for errors in the "EXTRACT PARQUET DATA FROM OUTPUTCOLLECT" section. Common issues:

1. **Missing R script**: Ensure `extract_output_models_data.R` is in the Docker container
   - Check Dockerfile includes: `COPY r/extract_output_models_data.R /app/extract_output_models_data.R`

2. **Missing arrow library**: Ensure R `arrow` package is installed in the training container

3. **OutputCollect.RDS is NULL**: Check if model training and `robyn_outputs()` completed successfully

### Backfill script fails

Common issues:

1. **R not installed**: Run the backfill script inside the training Docker container or Cloud Run Job

2. **GCS permissions**: Ensure the service account has `storage.objects.create` permission

3. **Corrupted RDS file**: If a specific RDS file fails, you may need to re-train that model

### Parquet files are empty

This usually means the corresponding component was NULL in OutputModels. Check the original model training logs to see if that component was generated.

## File Size Considerations

- Parquet files are typically 10-90% smaller than equivalent RDS files
- `resultHypParam.parquet`: ~50-500 KB (depends on number of models)
- `xDecompAgg.parquet`: ~10-100 KB
- `mediaVecCollect.parquet`: ~100 KB - 5 MB (depends on number of models and channels)
- `xDecompVecCollect.parquet`: ~500 KB - 50 MB (depends on time series length and channels)

## Integration Examples

### BigQuery Integration

Load parquet files from GCS to BigQuery for SQL analysis:

```sql
-- Create external table
CREATE EXTERNAL TABLE `project.dataset.model_hyperparameters`
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://mmm-app-output/robyn/v1/US/*/output_models_data/resultHypParam.parquet']
);

-- Query across all runs
SELECT 
  solID,
  rsq_train,
  nrmse_train,
  decomp_rssd
FROM `project.dataset.model_hyperparameters`
WHERE rsq_train > 0.9
ORDER BY nrmse_train ASC
LIMIT 10;
```

### DuckDB Analysis

Analyze parquet files with DuckDB:

```python
import duckdb

# Query parquet files directly from GCS
con = duckdb.connect()

query = """
SELECT 
  solID,
  rsq_train,
  nrmse_train
FROM read_parquet('gs://mmm-app-output/robyn/v1/US/*/output_models_data/resultHypParam.parquet')
WHERE rsq_train > 0.9
ORDER BY nrmse_train ASC
LIMIT 10
"""

result = con.execute(query).fetchdf()
print(result)
```

## Related Documentation

- [Model Summary Documentation](MODEL_SUMMARY.md) - JSON summaries of model runs
- [Architecture Documentation](../ARCHITECTURE.md) - Overall system architecture
- [Development Guide](../DEVELOPMENT.md) - Local development setup

## Future Enhancements

Potential future improvements:

1. **Incremental extraction**: Only extract new data components as they're added
2. **Compression optimization**: Tune parquet compression for optimal size/speed
3. **Metadata catalog**: Track all extracted parquet files in a central catalog
4. **Automated alerts**: Notify when extraction fails for a run
5. **Data validation**: Validate extracted data against source RDS checksums
