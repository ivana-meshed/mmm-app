# Cleanup Corrupt Parquet Files

This script helps identify and delete parquet files that were created with database-specific types (dbdate, dbtime, etc.) before the fix was applied in commit a0fb322.

## Problem

Parquet files created between 2026-01-20 (before commit a0fb322) contain database-specific types that PyArrow couldn't serialize properly, making them unreadable by pandas.

## Usage

### Dry Run (recommended first)

Check which files would be deleted without actually deleting them:

```bash
python scripts/cleanup_corrupt_parquet.py --dry-run
```

### Delete files from today

```bash
python scripts/cleanup_corrupt_parquet.py --date 20260120
```

### Delete files from a specific date

```bash
python scripts/cleanup_corrupt_parquet.py --date YYYYMMDD
```

### Specify a different bucket

```bash
python scripts/cleanup_corrupt_parquet.py --bucket my-bucket --date 20260120
```

### Specify a different prefix

```bash
python scripts/cleanup_corrupt_parquet.py --prefix datasets/ --date 20260120
```

## Options

- `--bucket`: GCS bucket name (default: `mmm-app-output`)
- `--date`: Date to filter files in YYYYMMDD format (default: today)
- `--prefix`: GCS prefix to search (default: `datasets/`)
- `--dry-run`: List files without deleting them

## Examples

```bash
# Dry run to see what would be deleted
python scripts/cleanup_corrupt_parquet.py --date 20260120 --dry-run

# Actually delete files from today
python scripts/cleanup_corrupt_parquet.py --date 20260120

# Delete files from yesterday
python scripts/cleanup_corrupt_parquet.py --date 20260119
```

## Safety Features

- Requires explicit confirmation before deleting files (unless dry-run)
- Logs all operations for audit trail
- Dry-run mode for safe testing

## What Gets Deleted

The script searches for parquet files in the following pattern:
- Path: `datasets/{country}/{YYYYMMDD_HHMMSS}/raw.parquet`
- Date filter: Files containing the specified date in their path
- File type: Only `.parquet` files

## After Running

After deleting corrupt files, users should:
1. Re-upload their data through the Connect Data page
2. The new files will be saved with the fix applied (database types converted to standard types)
