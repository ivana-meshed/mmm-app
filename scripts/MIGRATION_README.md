# Training Data Structure Migration

This directory contains a script to migrate training data on GCS from the old structure to the new structure.

## Overview

**Old structure:**
```
training_data/{country}/{timestamp}/selected_columns.json
```

**New structure:**
```
training_data/{country}/{goal}/{timestamp}/selected_columns.json
```

## Migration Script

The migration script (`migrate_training_data_structure.py`) performs the following:

1. Lists all files in the old format
2. Reads each JSON file to extract the `selected_goal` field
3. Copies the file to the new location with goal in the path
4. Optionally deletes the old file after successful copy

## Usage

### Dry Run (Safe - No Changes)

First, run in dry-run mode to see what would be migrated:

```bash
python scripts/migrate_training_data_structure.py
```

This will show:
- How many files need to be migrated
- The old and new paths for each file
- Any files that cannot be migrated (missing goal)

### Perform Migration

To actually perform the migration:

```bash
python scripts/migrate_training_data_structure.py --no-dry-run
```

### Delete Old Files

To delete old files after successful migration:

```bash
python scripts/migrate_training_data_structure.py --no-dry-run --delete-old
```

⚠️ **Warning:** Use `--delete-old` only after verifying that the migration was successful!

### Custom Bucket

To use a different GCS bucket:

```bash
python scripts/migrate_training_data_structure.py --bucket my-custom-bucket
```

## Prerequisites

- Google Cloud SDK authentication configured
- Appropriate IAM permissions on the GCS bucket:
  - `storage.objects.list`
  - `storage.objects.get`
  - `storage.objects.create`
  - `storage.objects.delete` (if using `--delete-old`)

## Authentication

Make sure you have authenticated with Google Cloud:

```bash
gcloud auth application-default login
```

Or set the service account key:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

## Migration Process

1. **Dry run first:**
   ```bash
   python scripts/migrate_training_data_structure.py
   ```

2. **Review the output** to ensure:
   - All files have a valid `selected_goal` field
   - New paths look correct
   - Count of files matches expectations

3. **Perform migration:**
   ```bash
   python scripts/migrate_training_data_structure.py --no-dry-run
   ```

4. **Verify in GCS** that files are in the correct locations

5. **Optional - Delete old files** (only after verifying migration):
   ```bash
   python scripts/migrate_training_data_structure.py --no-dry-run --delete-old
   ```

## Troubleshooting

### Files with Missing Goal

If some files don't have a `selected_goal` field, they will be skipped. You can:

1. Manually inspect these files in GCS
2. Add a default goal if appropriate
3. Delete them if they are invalid/outdated

### Permission Errors

If you get permission errors, verify:
- You are authenticated: `gcloud auth application-default login`
- Your account has the required IAM roles
- The bucket name is correct

## Example Output

```
2024-01-27 10:00:00 - INFO - Starting migration for bucket: mmm-app-output
2024-01-27 10:00:00 - INFO - Dry run: True
2024-01-27 10:00:00 - INFO - Delete old files: False
2024-01-27 10:00:01 - INFO - Found 15 files in old format
2024-01-27 10:00:01 - INFO - Processing: training_data/de/20240115_120000/selected_columns.json (country=de, goal=revenue, timestamp=20240115_120000)
2024-01-27 10:00:01 - INFO -   [DRY RUN] Would migrate to: training_data/de/revenue/20240115_120000/selected_columns.json
...
============================================================
MIGRATION SUMMARY
============================================================
Total files found: 15
Successfully migrated: 15
Failed: 0
Skipped (no goal): 0
============================================================

This was a DRY RUN. No changes were made to GCS.
To perform the actual migration, run with --no-dry-run flag.
```
