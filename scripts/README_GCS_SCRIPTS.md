# GCS Data Management Scripts

This directory contains scripts for managing test data and cleaning up the GCS bucket.

## Scripts Overview

### 1. collect_gcs_data_examples.py

Collects data examples from the `mmm-app-output` GCS bucket to understand data structures.

**Usage:**
```bash
# Collect examples from default countries (de, universal)
python scripts/collect_gcs_data_examples.py

# Collect from specific countries
python scripts/collect_gcs_data_examples.py --countries de universal us

# Specify output file
python scripts/collect_gcs_data_examples.py --output my_report.json

# Use different bucket
python scripts/collect_gcs_data_examples.py --bucket my-bucket
```

**Output:**
- JSON report file (default: `gcs_data_examples.json`) containing:
  - Mapped datasets schemas and structures
  - Metadata (mapping.json) examples
  - Training data configurations
  - Training config files
  - Robyn output structures
  - Queue data

**What it collects:**
- `mapped-datasets/<country>/<version>/raw.parquet` - Column schemas and sample data
- `metadata/<country>/<version>/mapping.json` - Field mapping configurations
- `training_data/<country>/<timestamp>/selected_columns.json` - Training data configs
- `training_config/*.json` - Training configuration files
- `training-data/` - Alternative training data structure
- `robyn/v1/<country>/<run_id>/` - Robyn output files
- `robyn-queues/` - Queue management files

### 2. generate_test_data.py

Generates synthetic test data based on collected examples.

**Usage:**
```bash
# Generate test data from collected examples
python scripts/generate_test_data.py

# Specify input file
python scripts/generate_test_data.py --input my_report.json

# Specify output directory
python scripts/generate_test_data.py --output-dir my_test_data

# Generate for specific countries
python scripts/generate_test_data.py --countries de universal
```

**Output:**
- Directory structure with test data (default: `test_data/`)
  - `mapped-datasets/<country>/latest/raw.parquet`
  - `metadata/<country>/latest/mapping.json`
  - `training_data/<country>/<timestamp>/selected_columns.json`
  - `training_config/config_<timestamp>.json`
  - `robyn/v1/<country>/<timestamp>/` (placeholder files)
  - `robyn-queues/default/queue.json`

**Features:**
- Generates synthetic data matching the original schema
- Creates realistic column names and data types
- Preserves structure and relationships
- Creates 100-365 rows of synthetic data

### 3. upload_test_data.py

Uploads generated test data to GCS bucket.

**Usage:**
```bash
# Dry run (preview what would be uploaded)
python scripts/upload_test_data.py --dry-run

# Upload to GCS with default settings
python scripts/upload_test_data.py

# Upload to specific bucket and prefix
python scripts/upload_test_data.py --bucket my-bucket --prefix test-data

# Upload from custom directory
python scripts/upload_test_data.py --source-dir my_test_data

# Skip confirmation prompt
python scripts/upload_test_data.py --force
```

**Options:**
- `--bucket` - Target GCS bucket (default: `mmm-app-output`)
- `--source-dir` - Source directory (default: `test_data`)
- `--prefix` - GCS prefix for uploaded data (default: `test-data`)
- `--dry-run` - Preview without uploading
- `--force` - Skip confirmation prompt

**Safety:**
- Verifies GCS access before uploading
- Shows file count before upload
- Requires confirmation (unless --force)
- Preserves directory structure

### 4. delete_non_revision_data.py

Deletes all GCS data except revision folders (e.g., r12, r24).

**⚠️ DANGER: This permanently deletes data!**

**Usage:**
```bash
# ALWAYS start with dry run to preview
python scripts/delete_non_revision_data.py --dry-run

# Delete with safety flags
python scripts/delete_non_revision_data.py --yes-i-am-sure

# Delete specific prefix only
python scripts/delete_non_revision_data.py --prefix robyn/v1/de/ --yes-i-am-sure

# Skip confirmation prompt (use with extreme caution!)
python scripts/delete_non_revision_data.py --yes-i-am-sure --force
```

**Safety Features:**
- **REQUIRES** `--yes-i-am-sure` flag for actual deletion
- Dry run mode by default if flag omitted
- Requires typing "DELETE" to confirm (unless --force)
- Shows detailed preview of what will be deleted
- Lists revision folders that will be kept

**What it keeps:**
- Any path containing `/rNN/` pattern (e.g., `/r12/`, `/r24/`)
- Examples:
  - `robyn/v1/de/r12/model_summary.json` ✓ KEPT
  - `metadata/de/r24/mapping.json` ✓ KEPT

**What it deletes:**
- Everything else not matching revision pattern
- Examples:
  - `robyn/v1/de/20231015_120000/` ✗ DELETED
  - `mapped-datasets/de/latest/` ✗ DELETED
  - `training_data/universal/20231015/` ✗ DELETED

## Complete Workflow

### Creating Test Data

1. **Collect examples from GCS:**
   ```bash
   python scripts/collect_gcs_data_examples.py --countries de universal
   ```

2. **Review the generated report:**
   ```bash
   cat gcs_data_examples.json | jq . | less
   # Or use any JSON viewer
   ```

3. **Generate synthetic test data:**
   ```bash
   python scripts/generate_test_data.py --countries de universal
   ```

4. **Review generated test data:**
   ```bash
   ls -R test_data/
   ```

5. **Preview upload (dry run):**
   ```bash
   python scripts/upload_test_data.py --dry-run --prefix test-data-2024
   ```

6. **Upload to GCS:**
   ```bash
   python scripts/upload_test_data.py --prefix test-data-2024
   ```

### Cleaning Up Non-Revision Data

1. **ALWAYS preview first:**
   ```bash
   python scripts/delete_non_revision_data.py --dry-run
   ```

2. **Review what will be deleted** (read the output carefully!)

3. **If satisfied, perform deletion:**
   ```bash
   python scripts/delete_non_revision_data.py --yes-i-am-sure
   ```

4. **Type "DELETE" when prompted** (or use --force to skip prompt)

## Requirements

All scripts require:
- Python 3.8+
- Google Cloud authentication configured
- Required packages:
  ```bash
  pip install google-cloud-storage pandas numpy
  ```

## Authentication

Set up Google Cloud authentication:

```bash
# Option 1: Application Default Credentials (recommended for local dev)
gcloud auth application-default login

# Option 2: Service account key
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

## Environment Variables

- `GCS_BUCKET` - Default GCS bucket name (default: `mmm-app-output`)

## Safety Tips

1. **Always use --dry-run first** before any destructive operation
2. **Review the output carefully** before confirming
3. **Test with --prefix first** to limit scope
4. **Make backups** of critical data before cleanup
5. **Double-check revision patterns** match your expectations

## Examples

### Generate test data for development:
```bash
# Full workflow
python scripts/collect_gcs_data_examples.py --countries de universal
python scripts/generate_test_data.py --output-dir test_data_dev
python scripts/upload_test_data.py --source-dir test_data_dev --prefix test-data-dev
```

### Clean up old experiment data:
```bash
# Preview what will be deleted
python scripts/delete_non_revision_data.py --prefix robyn/v1/de/ --dry-run

# If looks good, delete
python scripts/delete_non_revision_data.py --prefix robyn/v1/de/ --yes-i-am-sure
```

### Clean up everything except revisions:
```bash
# DANGER: Preview first!
python scripts/delete_non_revision_data.py --dry-run

# Review output, then if satisfied:
python scripts/delete_non_revision_data.py --yes-i-am-sure
# Type "DELETE" when prompted
```

## Troubleshooting

### "Cannot access GCS bucket"
- Check authentication: `gcloud auth application-default login`
- Verify bucket exists: `gsutil ls gs://mmm-app-output`
- Check IAM permissions (need Storage Object Admin or equivalent)

### "No examples found"
- Verify bucket has data: `gsutil ls gs://mmm-app-output/mapped-datasets/`
- Check country names match exactly
- Try different countries or use --all-countries

### "Failed to read parquet"
- May be corrupted or different format
- Script will skip and continue with other files
- Check logs for specific error messages

## Notes

- All scripts include detailed logging
- Use `--help` on any script for full options
- Scripts are idempotent where possible
- Generated test data is synthetic and safe to share

## File Structure

```
scripts/
├── collect_gcs_data_examples.py  # Step 1: Collect examples
├── generate_test_data.py         # Step 2: Generate test data
├── upload_test_data.py           # Step 3: Upload to GCS
├── delete_non_revision_data.py   # Cleanup utility
└── README_GCS_SCRIPTS.md         # This file
```
