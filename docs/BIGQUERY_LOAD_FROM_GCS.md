# Loading GCS Parquet Data into BigQuery

This guide explains how to create a BigQuery table from parquet files stored in Google Cloud Storage (GCS), specifically from the `mmm-app-output/datasets/de/` bucket.

## Prerequisites

- Access to Google Cloud Console
- Access to the `mmm-app-output` GCS bucket
- BigQuery API enabled in your GCP project
- Appropriate IAM permissions:
  - `bigquery.datasets.create` (to create datasets)
  - `bigquery.tables.create` (to create tables)
  - `storage.objects.get` (to read from GCS)

## Method 1: Using BigQuery Console (Recommended for Testing)

### Step 1: Navigate to BigQuery Console

1. Go to [BigQuery Console](https://console.cloud.google.com/bigquery)
2. Select your project from the dropdown

### Step 2: Create a Dataset (if needed)

1. Click on your project name in the Explorer panel
2. Click the three dots (⋮) and select **"Create dataset"**
3. Configure:
   - **Dataset ID**: `mmm_data` (or your preferred name)
   - **Data location**: Choose the same region as your GCS bucket (e.g., `europe-west1`)
   - **Default table expiration**: Optional (e.g., 90 days for test data)
4. Click **"Create dataset"**

### Step 3: Create a Table from GCS

#### Option A: Load from Latest Parquet File

1. Click on the dataset you created (`mmm_data`)
2. Click **"Create Table"**
3. Configure the source:
   - **Create table from**: `Google Cloud Storage`
   - **Select file from GCS bucket**: 
     ```
     gs://mmm-app-output/datasets/de/latest/raw.parquet
     ```
   - **File format**: `Parquet`

4. Configure the destination:
   - **Project**: Your project
   - **Dataset**: `mmm_data`
   - **Table**: `de_mmm_raw` (or your preferred name)
   - **Table type**: `Native table`

5. Schema:
   - **Auto detect**: ✅ (checked)
   - BigQuery will automatically detect schema from parquet file

6. Advanced options (optional):
   - **Write preference**: `Write if empty` or `Overwrite table`
   
7. Click **"Create Table"**

#### Option B: Load from Specific Timestamped Version

If you want to load a specific version instead of "latest":

1. First, list available versions in GCS:
   - Go to [Cloud Storage Browser](https://console.cloud.google.com/storage/browser)
   - Navigate to `mmm-app-output/datasets/de/`
   - You'll see folders like `20250115_143022/`, `20250114_091533/`, etc.

2. Use the specific path:
   ```
   gs://mmm-app-output/datasets/de/20250115_143022/raw.parquet
   ```

3. Follow the same steps as Option A with this specific path

### Step 4: Verify the Table

1. Click on your new table in the Explorer panel
2. Go to the **Preview** tab to see sample data
3. Check the **Schema** tab to verify columns
4. Go to the **Details** tab to see row count and size

### Step 5: Query Your Data

Click **"Query"** button and run a test query:

```sql
SELECT *
FROM `your-project-id.mmm_data.de_mmm_raw`
LIMIT 100
```

## Method 2: Using bq Command-Line Tool

### Step 1: Install and Authenticate

```bash
# Install Google Cloud SDK if not already installed
# https://cloud.google.com/sdk/docs/install

# Authenticate
gcloud auth login

# Set project
gcloud config set project YOUR_PROJECT_ID
```

### Step 2: Create Dataset

```bash
bq mk \
  --dataset \
  --location=europe-west1 \
  --description="MMM data from GCS" \
  YOUR_PROJECT_ID:mmm_data
```

### Step 3: Load Table from GCS

```bash
bq load \
  --source_format=PARQUET \
  --autodetect \
  --replace \
  mmm_data.de_mmm_raw \
  gs://mmm-app-output/datasets/de/latest/raw.parquet
```

**Flags explanation:**
- `--source_format=PARQUET`: Specifies parquet format
- `--autodetect`: Automatically detect schema from file
- `--replace`: Overwrite table if it exists (use `--noreplace` to fail if exists)

### Step 4: Query the Table

```bash
bq query \
  --use_legacy_sql=false \
  'SELECT COUNT(*) as row_count FROM `mmm_data.de_mmm_raw`'
```

## Method 3: Using Python Script

Create a Python script to automate the process:

```python
from google.cloud import bigquery

# Initialize client
client = bigquery.Client(project='YOUR_PROJECT_ID')

# Create dataset
dataset_id = f"{client.project}.mmm_data"
dataset = bigquery.Dataset(dataset_id)
dataset.location = "europe-west1"
dataset = client.create_dataset(dataset, exists_ok=True)
print(f"Created dataset {dataset.project}.{dataset.dataset_id}")

# Configure load job
table_id = f"{dataset_id}.de_mmm_raw"
job_config = bigquery.LoadJobConfig(
    source_format=bigquery.SourceFormat.PARQUET,
    autodetect=True,
    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # Overwrite
)

# Load from GCS
uri = "gs://mmm-app-output/datasets/de/latest/raw.parquet"
load_job = client.load_table_from_uri(
    uri, table_id, job_config=job_config
)

# Wait for completion
load_job.result()

# Get table info
table = client.get_table(table_id)
print(f"Loaded {table.num_rows} rows into {table_id}")
```

Run the script:
```bash
python load_mmm_data.py
```

## Method 4: Using Terraform

Create a `bigquery.tf` file:

```hcl
resource "google_bigquery_dataset" "mmm_data" {
  dataset_id                  = "mmm_data"
  friendly_name               = "MMM Data"
  description                 = "Marketing Mix Modeling data from GCS"
  location                    = "europe-west1"
  default_table_expiration_ms = 7776000000  # 90 days
}

resource "google_bigquery_table" "de_mmm_raw" {
  dataset_id = google_bigquery_dataset.mmm_data.dataset_id
  table_id   = "de_mmm_raw"

  external_data_configuration {
    autodetect    = true
    source_format = "PARQUET"
    source_uris = [
      "gs://mmm-app-output/datasets/de/latest/raw.parquet"
    ]
  }

  schema = <<EOF
[
  {
    "name": "date",
    "type": "DATE",
    "mode": "NULLABLE"
  }
]
EOF
}
```

Apply with:
```bash
terraform init
terraform apply
```

## Loading Multiple Countries

To load data for multiple countries (e.g., de, fr, uk):

### Option A: Separate Tables

```bash
for country in de fr uk; do
  bq load \
    --source_format=PARQUET \
    --autodetect \
    --replace \
    mmm_data.${country}_mmm_raw \
    gs://mmm-app-output/datasets/${country}/latest/raw.parquet
done
```

### Option B: Single Table with Country Column

1. Load each country's data with a country identifier
2. Use a query to combine them:

```sql
CREATE OR REPLACE TABLE `mmm_data.all_countries_mmm_raw` AS
SELECT *, 'de' as country FROM `mmm_data.de_mmm_raw`
UNION ALL
SELECT *, 'fr' as country FROM `mmm_data.fr_mmm_raw`
UNION ALL
SELECT *, 'uk' as country FROM `mmm_data.uk_mmm_raw`
```

## Creating an External Table (Alternative)

Instead of loading data into BigQuery, you can create an external table that queries GCS directly:

```sql
CREATE OR REPLACE EXTERNAL TABLE `mmm_data.de_mmm_raw_external`
OPTIONS (
  format = 'PARQUET',
  uris = ['gs://mmm-app-output/datasets/de/latest/raw.parquet']
);
```

**Benefits:**
- No data duplication (reads directly from GCS)
- Always uses the latest data
- No storage costs in BigQuery

**Drawbacks:**
- Slower query performance
- Costs for GCS API calls
- No table-level optimizations

## Using the Table in MMM App

Once your table is created, use it in the MMM app:

1. Go to **Connect Data** page
2. Select **BigQuery** as data source
3. Connect with your service account credentials
4. Navigate to **Map Data** page
5. Select **BigQuery** from the "Alternatively: connect and load new dataset" dropdown
6. Enter your table ID:
   ```
   your-project-id.mmm_data.de_mmm_raw
   ```
   Or use custom SQL:
   ```sql
   SELECT * FROM `your-project-id.mmm_data.de_mmm_raw`
   WHERE date >= '2024-01-01'
   ```
7. Click **Load**

## Troubleshooting

### Issue: "Access Denied"

**Solution:**
- Ensure your service account has `roles/bigquery.dataEditor` and `roles/storage.objectViewer`
- Grant permissions:
  ```bash
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:YOUR_SA@YOUR_PROJECT.iam.gserviceaccount.com" \
    --role="roles/bigquery.dataEditor"
  
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:YOUR_SA@YOUR_PROJECT.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
  ```

### Issue: "File not found"

**Solution:**
- Verify the GCS path exists:
  ```bash
  gsutil ls gs://mmm-app-output/datasets/de/
  ```
- Check if you're using the correct bucket name and path

### Issue: "Schema mismatch"

**Solution:**
- Use `--autodetect` flag to let BigQuery infer schema
- Or explicitly define schema if autodetect fails
- Ensure all parquet files have consistent schema

### Issue: "Location mismatch"

**Solution:**
- Ensure dataset location matches GCS bucket location
- Both should be in the same region (e.g., `europe-west1`)

## Best Practices

1. **Use Latest Symlink**: Always use `/latest/raw.parquet` for the most recent data
2. **Partition Tables**: For large datasets, consider partitioning by date:
   ```sql
   CREATE OR REPLACE TABLE `mmm_data.de_mmm_raw`
   PARTITION BY date
   AS SELECT * FROM EXTERNAL_QUERY(...)
   ```
3. **Set Expiration**: Set table expiration for test data to avoid costs
4. **Monitor Costs**: Use BigQuery's cost controls and quotas
5. **Document Schema**: Keep track of column names and types for reference

## Example: Complete Workflow

```bash
# 1. Set variables
PROJECT_ID="your-project-id"
DATASET_ID="mmm_data"
TABLE_ID="de_mmm_raw"
GCS_PATH="gs://mmm-app-output/datasets/de/latest/raw.parquet"

# 2. Create dataset
bq mk --dataset --location=europe-west1 ${PROJECT_ID}:${DATASET_ID}

# 3. Load table
bq load \
  --source_format=PARQUET \
  --autodetect \
  --replace \
  ${DATASET_ID}.${TABLE_ID} \
  ${GCS_PATH}

# 4. Verify
bq show ${DATASET_ID}.${TABLE_ID}
bq head -n 10 ${DATASET_ID}.${TABLE_ID}

# 5. Query
bq query --use_legacy_sql=false \
  "SELECT COUNT(*) as total_rows, 
          MIN(date) as min_date, 
          MAX(date) as max_date 
   FROM \`${PROJECT_ID}.${DATASET_ID}.${TABLE_ID}\`"
```

## Summary

You now have multiple methods to create BigQuery tables from your GCS parquet files:
- **BigQuery Console**: Best for one-time setup and testing
- **bq CLI**: Best for automation and scripts
- **Python**: Best for integration with other code
- **Terraform**: Best for infrastructure as code
- **External Tables**: Best for real-time access without data duplication

Choose the method that best fits your workflow and requirements!
