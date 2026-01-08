# Quick Start Guide: GCS Data Scripts

This guide shows you how to quickly collect data from your GCS bucket and generate test data.

## Prerequisites

1. **Google Cloud Authentication**
   ```bash
   gcloud auth application-default login
   ```

2. **Python Dependencies**
   ```bash
   pip install google-cloud-storage pandas numpy
   ```

## Step-by-Step Instructions

### Step 1: Collect Data Examples from GCS

Run the collection script to scan your GCS bucket and create a report:

```bash
python scripts/collect_gcs_data_examples.py --countries de universal
```

This will:
- Scan `mmm-app-output` bucket (or use `--bucket` to specify another)
- Look for data in "de" and "universal" folders
- Create a file `gcs_data_examples.json` with all the collected information

**Expected output:**
```
2024-01-08 12:00:00 - INFO - Scanning GCS bucket: mmm-app-output
2024-01-08 12:00:01 - INFO - Target countries: ['de', 'universal']
2024-01-08 12:00:02 - INFO - Collecting mapped-datasets examples...
...
✅ Report written to: gcs_data_examples.json

Summary:
  - Mapped datasets: 2 countries
  - Metadata: 2 countries
  - Training data: 2 countries
  - Training configs: 5 files
  - Training-data alt: 10 files
  - Robyn outputs: 2 countries
  - Queue files: 3 files
```

### Step 2: Review the Report

Open and review the generated report:

```bash
# Pretty print JSON
cat gcs_data_examples.json | jq . | less

# Or use any JSON viewer/editor
code gcs_data_examples.json
```

The report contains:
- Column schemas from parquet files
- Sample data values
- Metadata mapping structures
- Training configurations
- File paths and sizes

### Step 3: Generate Synthetic Test Data

Generate test data based on the collected examples:

```bash
python scripts/generate_test_data.py
```

This will:
- Read `gcs_data_examples.json`
- Generate synthetic data matching the schemas
- Create files in `test_data/` directory

**Expected output:**
```
2024-01-08 12:05:00 - INFO - Reading examples from: gcs_data_examples.json
2024-01-08 12:05:00 - INFO - Generating test data in: test_data
2024-01-08 12:05:01 - INFO - Generating mapped dataset for de...
2024-01-08 12:05:01 - INFO -   Created: test_data/mapped-datasets/de/latest/raw.parquet
...
✅ Test data generation complete!
Output directory: test_data
```

### Step 4: Verify Generated Test Data

Check what was created:

```bash
# List all generated files
find test_data -type f

# Check parquet file
python -c "import pandas as pd; df = pd.read_parquet('test_data/mapped-datasets/de/latest/raw.parquet'); print(df.head())"

# Check JSON files
cat test_data/metadata/de/latest/mapping.json | jq .
```

### Step 5: Upload Test Data to GCS (Optional)

**Preview what will be uploaded (dry run):**

```bash
python scripts/upload_test_data.py --dry-run --prefix test-data-2024
```

**Upload to GCS:**

```bash
python scripts/upload_test_data.py --prefix test-data-2024
```

You'll be prompted to confirm before uploading.

## Advanced Usage

### Collect from Specific Countries

```bash
python scripts/collect_gcs_data_examples.py --countries us uk de fr
```

### Generate Test Data for Specific Countries

```bash
python scripts/generate_test_data.py --countries us uk
```

### Upload to Different Bucket

```bash
python scripts/upload_test_data.py --bucket my-test-bucket --prefix test-data
```

### Clean Up Non-Revision Data

**⚠️ DANGER: This deletes data! Always dry-run first!**

```bash
# Preview what will be deleted
python scripts/delete_non_revision_data.py --dry-run

# Review the output carefully, then delete
python scripts/delete_non_revision_data.py --yes-i-am-sure
```

This keeps only revision folders (e.g., `/r12/`, `/r24/`) and deletes everything else.

## Troubleshooting

### "Cannot access GCS bucket"

**Problem:** Authentication not configured

**Solution:**
```bash
gcloud auth application-default login
# Or set service account key
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

### "No examples found"

**Problem:** Bucket is empty or country names don't match

**Solution:**
```bash
# List what's in the bucket
gsutil ls gs://mmm-app-output/mapped-datasets/

# Try different countries
python scripts/collect_gcs_data_examples.py --countries $(gsutil ls gs://mmm-app-output/mapped-datasets/ | sed 's|.*/||' | sed 's|/||')
```

### "Module not found"

**Problem:** Missing Python dependencies

**Solution:**
```bash
pip install google-cloud-storage pandas numpy
```

## Example Complete Workflow

Here's a complete example from start to finish:

```bash
# 1. Authenticate
gcloud auth application-default login

# 2. Collect examples from GCS
python scripts/collect_gcs_data_examples.py --countries de universal

# 3. Review the report
cat gcs_data_examples.json | jq '.mapped_datasets | keys'

# 4. Generate test data
python scripts/generate_test_data.py

# 5. Verify generated data
find test_data -type f

# 6. Preview upload
python scripts/upload_test_data.py --dry-run --prefix test-data-$(date +%Y%m%d)

# 7. Upload to GCS
python scripts/upload_test_data.py --prefix test-data-$(date +%Y%m%d)

# 8. Verify uploaded data
gsutil ls -lh gs://mmm-app-output/test-data-$(date +%Y%m%d)/
```

## Next Steps

After collecting the data report:
1. Share `gcs_data_examples.json` with your team
2. Review the schemas and structures
3. Customize test data generation if needed
4. Use test data for development and testing
5. Clean up old experiment data using the delete script

## Need Help?

- See full documentation: `scripts/README_GCS_SCRIPTS.md`
- Check test examples: `tests/test_gcs_scripts.py`
- All scripts support `--help` flag for options

## Safety Reminders

- ✅ Always use `--dry-run` first for upload/delete operations
- ✅ Review output carefully before confirming
- ✅ Test with `--prefix` to limit scope
- ✅ Keep backups of important data
- ⚠️ The delete script is destructive - use extreme caution!
