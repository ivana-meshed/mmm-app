# GCS Data Management Scripts

This directory contains scripts for managing test data in the `mmm-app-output` Google Cloud Storage bucket.

## Scripts

### 1. `download_test_data.py`

Downloads data from ALL folders in the GCS bucket for testing purposes.

**What it downloads:**
- Data with timestamp "20251211_115528" (or close to it) from structured folders
- ~3 latest examples for countries "de", "fr", "es" from country-based folders
- Files in folders/subfolders "latest" and "universal"
- From "robyn" folder: only data in folders starting with "r" (like r100, r101)
- From other folders (training-configs, training-data, etc.): latest 3 files

**Usage:**

```bash
# Dry run (safe, only lists what would be downloaded)
python scripts/download_test_data.py --dry-run

# Download to default directory (./test_data)
python scripts/download_test_data.py

# Download to custom directory
python scripts/download_test_data.py --output-dir /path/to/download

# Download with custom timestamp
python scripts/download_test_data.py --timestamp 20251210_120000

# Download from different bucket
python scripts/download_test_data.py --bucket my-bucket-name
```

**Arguments:**
- `--bucket`: GCS bucket name (default: mmm-app-output)
- `--output-dir`: Output directory for downloaded files (default: ./test_data)
- `--timestamp`: Target timestamp to search for (default: 20251211_115528)
- `--dry-run`: Only list files without downloading

---

### 2. `delete_bucket_data.py`

Deletes all data in the bucket **EXCEPT** folders in "robyn" that start with "r".

⚠️ **WARNING: This is a destructive operation!** Always use `--dry-run` first!

**What it keeps:**
- `robyn/r*/**` - All data in robyn folders with revisions starting with "r" (e.g., r100, r101)

**What it deletes:**
- Everything else in the bucket

**Usage:**

```bash
# Dry run (ALWAYS DO THIS FIRST!)
python scripts/delete_bucket_data.py --dry-run

# Actually delete data (use with extreme caution!)
python scripts/delete_bucket_data.py --no-dry-run

# Delete from different bucket
python scripts/delete_bucket_data.py --bucket my-bucket-name --no-dry-run
```

**Arguments:**
- `--bucket`: GCS bucket name (default: mmm-app-output)
- `--dry-run`: Only list files without deleting (default: True)
- `--no-dry-run`: Actually delete files (use with caution!)

**Safety features:**
- Dry run is enabled by default
- 5-second countdown before actual deletion
- Shows samples of files to keep and delete

---

### 3. `upload_test_data.py`

Uploads downloaded test data back to GCS bucket, maintaining the same structure.

**Features:**
- Maintains folder/subfolder/naming structure
- Skips files that already exist on GCS (by default)
- Can force overwrite with `--no-skip-existing`
- Can upload to a specific folder prefix (e.g., TEST/)

**Usage:**

```bash
# Dry run (safe, only lists what would be uploaded)
python scripts/upload_test_data.py --dry-run

# Upload with skipping existing files (default)
python scripts/upload_test_data.py

# Upload to TEST folder
python scripts/upload_test_data.py --prefix TEST

# Force upload (overwrite existing files)
python scripts/upload_test_data.py --no-skip-existing

# Upload from custom directory
python scripts/upload_test_data.py --input-dir /path/to/data

# Upload to different bucket
python scripts/upload_test_data.py --bucket my-bucket-name
```

**Arguments:**
- `--bucket`: GCS bucket name (default: mmm-app-output)
- `--input-dir`: Input directory containing files to upload (default: ./test_data)
- `--prefix`: Optional prefix to prepend to all files (e.g., TEST)
- `--dry-run`: Only list files without uploading
- `--skip-existing`: Skip files that already exist on GCS (default: True)
- `--no-skip-existing`: Overwrite files that already exist on GCS

---

### 4. `copy_test_to_root.py`

Copies all files from the TEST/ folder to the root of the bucket, maintaining folder structure.

**Features:**
- Copies within the same bucket (no download needed)
- Skips existing files by default
- Can overwrite with `--overwrite` flag

**Usage:**

```bash
# Dry run (safe, only lists what would be copied)
python scripts/copy_test_to_root.py --dry-run

# Copy files (skip existing)
python scripts/copy_test_to_root.py

# Copy and overwrite existing files
python scripts/copy_test_to_root.py --overwrite

# Copy from different bucket
python scripts/copy_test_to_root.py --bucket my-bucket-name
```

**Arguments:**
- `--bucket`: GCS bucket name (default: mmm-app-output)
- `--dry-run`: Only list files without copying
- `--overwrite`: Overwrite existing files in destination (default: False)

---

## Typical Workflow

### Creating test data backup:

1. **Download test data:**
   ```bash
   python scripts/download_test_data.py --dry-run  # Check what will be downloaded
   python scripts/download_test_data.py            # Actually download
   ```

2. **Archive for safekeeping:**
   ```bash
   tar -czf test_data_backup.tar.gz test_data/
   ```

### Restoring test data:

1. **Extract archive:**
   ```bash
   tar -xzf test_data_backup.tar.gz
   ```

2. **Upload to GCS:**
   ```bash
   python scripts/upload_test_data.py --dry-run  # Check what will be uploaded
   python scripts/upload_test_data.py            # Actually upload
   ```

### Cleaning bucket (with extreme caution):

1. **Always dry run first:**
   ```bash
   python scripts/delete_bucket_data.py --dry-run
   ```

2. **Review the output carefully** - verify that files you want to keep are protected

3. **Only then, actually delete:**
   ```bash
   python scripts/delete_bucket_data.py --no-dry-run
   ```

### Using TEST folder as staging:

1. **Upload test data to TEST folder:**
   ```bash
   python scripts/upload_test_data.py --prefix TEST --dry-run  # Check what will be uploaded
   python scripts/upload_test_data.py --prefix TEST            # Upload to TEST/ folder
   ```

2. **Verify data in TEST folder** (using GCS console or gsutil)

3. **Copy from TEST to root:**
   ```bash
   python scripts/copy_test_to_root.py --dry-run  # Check what will be copied
   python scripts/copy_test_to_root.py            # Actually copy to root
   ```

This workflow allows you to stage data in TEST/ folder first, verify it, then copy to production.

---

## Prerequisites

- Python 3.8+
- Google Cloud SDK configured
- Appropriate GCS permissions
- Required packages: `google-cloud-storage`

## Authentication

Ensure you're authenticated with Google Cloud:

```bash
gcloud auth application-default login
```

Or set the service account credentials:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

## Bucket Structure

The scripts handle ALL folders in the bucket:

```
mmm-app-output/
├── robyn/
│   ├── r100/          # ← PROTECTED (starts with 'r')
│   │   ├── de/
│   │   │   └── 20251211_115528/
│   │   └── fr/
│   │       └── 20251211_115528/
│   ├── r101/          # ← PROTECTED (starts with 'r')
│   └── v1/            # ← NOT PROTECTED (doesn't start with 'r')
├── datasets/
│   ├── de/
│   │   └── latest/    # ← Downloaded by download_test_data.py
│   └── fr/
│       └── latest/    # ← Downloaded by download_test_data.py
├── mapped-datasets/
│   └── */latest/      # ← Downloaded by download_test_data.py
├── metadata/
│   ├── universal/     # ← Downloaded by download_test_data.py
│   └── */
├── training-configs/  # ← Latest 3 files downloaded
├── training-data/     # ← Latest 3 files downloaded
└── (any other folder) # ← Latest 3 files downloaded
```

**Download behavior:**
- **Structured folders** (robyn, datasets, mapped-datasets, metadata): Downloads based on country/timestamp filters
- **Other folders** (training-configs, training-data, etc.): Downloads latest 3 files based on modification time

**Delete behavior:**
- **Protected**: Only `robyn/r*/` folders are kept
- **Deleted**: Everything else including all other folders

**Upload behavior:**
- All files from local test_data directory are uploaded maintaining the structure
```

## Troubleshooting

### Permission denied

Make sure you have the required GCS permissions:
- `storage.objects.list`
- `storage.objects.get` (for download)
- `storage.objects.create` (for upload)
- `storage.objects.delete` (for delete)

### Script not found

Make sure you're running from the repository root:
```bash
cd /path/to/mmm-app
python scripts/download_test_data.py
```

### Module not found

Install required dependencies:
```bash
pip install google-cloud-storage
```
