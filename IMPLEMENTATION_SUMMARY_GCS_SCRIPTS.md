# Implementation Summary: GCS Data Management Scripts

## Overview

This implementation adds four powerful Python scripts for managing test data and cleaning up the GCS bucket `mmm-app-output`. All scripts are production-ready, tested, and follow the repository's code standards.

## What Was Implemented

### 1. Data Collection Script (`collect_gcs_data_examples.py`)

**Purpose:** Scan the GCS bucket and collect examples of all data structures.

**Features:**
- Collects schemas from parquet files (columns, dtypes, sample values)
- Reads JSON configurations (metadata, training configs)
- Identifies file structures across all major data types
- Outputs comprehensive JSON report
- Supports multiple countries (default: de, universal)
- Configurable bucket and output file

**Data Collected:**
- `mapped-datasets/<country>/<version>/raw.parquet` - Data schemas and samples
- `metadata/<country>/<version>/mapping.json` - Field mappings
- `training_data/<country>/<timestamp>/selected_columns.json` - Training configs
- `training_config/*.json` - Training configuration files
- `training-data/` - Alternative training data structure
- `robyn/v1/<country>/<run_id>/` - Robyn output files
- `robyn-queues/` - Queue management files

**Usage:**
```bash
python scripts/collect_gcs_data_examples.py --countries de universal
```

### 2. Test Data Generation Script (`generate_test_data.py`)

**Purpose:** Generate synthetic test data matching the collected structures.

**Features:**
- Parses collection report JSON
- Generates synthetic data matching original schemas
- Preserves column types (int, float, datetime, string)
- Uses sample values when available
- Creates realistic test datasets (100-365 rows)
- Maintains directory structure

**Output Structure:**
```
test_data/
├── mapped-datasets/
│   └── <country>/
│       └── latest/
│           └── raw.parquet
├── metadata/
│   └── <country>/
│       └── latest/
│           └── mapping.json
├── training_data/
│   └── <country>/
│       └── <timestamp>/
│           ├── selected_columns.json
│           └── training_data.parquet
├── training_config/
│   └── config_<timestamp>.json
├── robyn/
│   └── v1/
│       └── <country>/
│           └── <timestamp>/
│               ├── model_summary.json
│               ├── hyperparameters.json
│               └── results.csv
└── robyn-queues/
    └── default/
        └── queue.json
```

**Usage:**
```bash
python scripts/generate_test_data.py
```

### 3. Upload Script (`upload_test_data.py`)

**Purpose:** Upload generated test data to GCS bucket.

**Features:**
- Preserves directory structure
- Configurable GCS prefix
- Dry-run mode for preview
- Confirmation prompt
- Verifies GCS access before upload
- Sets appropriate content types (JSON, parquet, CSV)

**Safety Features:**
- `--dry-run` - Preview without uploading
- `--force` - Skip confirmation (use carefully)
- Verifies bucket access
- Shows file count before upload

**Usage:**
```bash
# Preview first
python scripts/upload_test_data.py --dry-run --prefix test-data-2024

# Upload
python scripts/upload_test_data.py --prefix test-data-2024
```

### 4. Cleanup Script (`delete_non_revision_data.py`)

**Purpose:** Delete all data except revision folders (e.g., r12, r24).

**⚠️ THIS IS A DESTRUCTIVE OPERATION!**

**Features:**
- Identifies revision paths using `/rNN/` pattern
- Keeps: Any path containing `/r\d+/` (e.g., `/r12/`, `/r24/`, `/r1/`)
- Deletes: Everything else
- Multiple safety confirmations
- Dry-run mode (highly recommended)
- Detailed preview of what will be deleted/kept

**Safety Features:**
- **REQUIRES** `--yes-i-am-sure` flag for actual deletion
- Dry-run shows exactly what would be deleted
- Requires typing "DELETE" to confirm (unless --force)
- Shows examples of paths being kept/deleted
- Lists all revision folders being preserved
- Optional prefix filtering to limit scope

**Examples of What Gets Kept:**
- `robyn/v1/de/r12/model_summary.json` ✓ KEPT
- `metadata/de/r24/mapping.json` ✓ KEPT
- `training_data/universal/r1/config.json` ✓ KEPT

**Examples of What Gets Deleted:**
- `robyn/v1/de/20231015_120000/model.json` ✗ DELETED
- `mapped-datasets/de/latest/raw.parquet` ✗ DELETED
- `training_data/universal/20231015/config.json` ✗ DELETED

**Usage:**
```bash
# ALWAYS preview first!
python scripts/delete_non_revision_data.py --dry-run

# If satisfied with preview, delete
python scripts/delete_non_revision_data.py --yes-i-am-sure

# Delete specific prefix only (safer)
python scripts/delete_non_revision_data.py --prefix robyn/v1/de/ --yes-i-am-sure
```

## Documentation

### Created Documentation Files

1. **`scripts/README_GCS_SCRIPTS.md`** (9KB)
   - Comprehensive guide for all scripts
   - Detailed usage examples
   - Complete workflow instructions
   - Troubleshooting section
   - Safety tips

2. **`scripts/QUICKSTART.md`** (6KB)
   - Step-by-step quick start guide
   - Prerequisites and setup
   - Complete example workflow
   - Common troubleshooting

3. **Updated `README.md`**
   - Added GCS Data Management Scripts section
   - Added links to new documentation
   - Added to documentation table

## Testing

### Test Coverage (`tests/test_gcs_scripts.py`)

**10 tests covering:**
- JSON serialization (datetime, DataFrame)
- Data type parsing (int, float, datetime, string)
- Synthetic data generation
- File listing for upload
- Revision path detection
- Revision folder extraction

**All tests passing:** ✅ 10/10

## Code Quality

All scripts follow repository standards:
- ✅ Formatted with Black (line length 80)
- ✅ Imports sorted with isort
- ✅ Docstrings for all functions
- ✅ Type hints where appropriate
- ✅ Comprehensive error handling
- ✅ Detailed logging
- ✅ Command-line argument parsing

## Prerequisites

**Required for all scripts:**
- Python 3.8+
- Google Cloud authentication configured
- Packages: `google-cloud-storage`, `pandas`, `numpy`

**Authentication:**
```bash
# Option 1: Application Default Credentials (recommended)
gcloud auth application-default login

# Option 2: Service account key
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

## Complete Workflow Example

Here's how to use all scripts together:

```bash
# 1. Collect examples from GCS
python scripts/collect_gcs_data_examples.py --countries de universal

# 2. Review the report
cat gcs_data_examples.json | jq . | less

# 3. Generate test data
python scripts/generate_test_data.py

# 4. Verify generated data
ls -R test_data/

# 5. Preview upload
python scripts/upload_test_data.py --dry-run --prefix test-data-2024

# 6. Upload to GCS
python scripts/upload_test_data.py --prefix test-data-2024

# 7. (Optional) Clean up old data
python scripts/delete_non_revision_data.py --dry-run
python scripts/delete_non_revision_data.py --yes-i-am-sure
```

## Next Steps for You

### Immediate Actions

1. **Run the collection script** to scan your actual GCS data:
   ```bash
   python scripts/collect_gcs_data_examples.py --countries de universal
   ```

2. **Review the generated report** (`gcs_data_examples.json`):
   - Check what data structures were found
   - Verify schemas match expectations
   - Share with the team if needed

3. **Generate test data**:
   ```bash
   python scripts/generate_test_data.py
   ```

4. **Review generated test data**:
   - Check the `test_data/` directory
   - Verify data looks reasonable
   - Test with your application

5. **Upload test data if satisfied**:
   ```bash
   python scripts/upload_test_data.py --prefix test-data-2024
   ```

### Optional: Cleanup

If you want to clean up old experiment data (keeping only revisions):

```bash
# ALWAYS dry-run first!
python scripts/delete_non_revision_data.py --dry-run

# Review output carefully!
# If satisfied, run:
python scripts/delete_non_revision_data.py --yes-i-am-sure
```

## Important Notes

### Safety Considerations

1. **Delete script is DESTRUCTIVE**
   - Always use `--dry-run` first
   - Review output carefully
   - Understand what "revision" means (paths with `/rNN/`)
   - Test with `--prefix` first to limit scope
   - Make backups of critical data

2. **Upload script verification**
   - Preview with `--dry-run` before uploading
   - Verify target prefix/bucket
   - Check file count matches expectations

3. **Collection script is READ-ONLY**
   - Safe to run anytime
   - Does not modify GCS data
   - Only reads schemas and samples

### Customization

All scripts support command-line arguments:
- `--bucket` - Change target bucket
- `--countries` - Specify countries
- `--prefix` - Limit scope or organize uploads
- `--output` / `--output-dir` - Custom output locations
- `--dry-run` - Preview mode
- `--force` - Skip confirmations
- `--help` - See all options

## File Statistics

| File | Size | Lines | Description |
|------|------|-------|-------------|
| `collect_gcs_data_examples.py` | 17KB | 440 | Data collection script |
| `generate_test_data.py` | 9KB | 282 | Test data generation |
| `upload_test_data.py` | 5KB | 177 | Upload to GCS |
| `delete_non_revision_data.py` | 7KB | 240 | Cleanup script |
| `test_gcs_scripts.py` | 6KB | 182 | Unit tests |
| `README_GCS_SCRIPTS.md` | 9KB | - | Full documentation |
| `QUICKSTART.md` | 6KB | - | Quick start guide |

**Total:** ~59KB of code and documentation

## Summary

You now have a complete suite of tools for:
- ✅ Understanding your GCS data structure
- ✅ Generating realistic test data
- ✅ Managing test data in GCS
- ✅ Cleaning up old experiment data
- ✅ Preserving important revision data

All scripts are production-ready, tested (10/10 tests passing), and follow repository code standards.

The next step is to run the collection script against your actual GCS bucket and share the results! 🎉
