# Critical Fix: Job Config Not Being Passed to R Script

## Problem Summary

**Symptom:** Jobs complete successfully but results don't appear at the logged GCS paths.

**Example:**
```
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260212_151325_319/
   Verifying results in GCS...
   ⚠️  No files found after 10s timeout
```

When checking manually:
```bash
gsutil ls gs://mmm-app-output/robyn/default/de/20260212_151325_319/
# CommandException: One or more URLs matched no objects.
```

## Root Cause Analysis

### How Configuration Is Supposed to Work

The R script (`r/run_all.R`) expects configuration from a **JSON file on GCS**, not from environment variables!

```r
get_cfg_from_env <- function() {
    cfg_path <- Sys.getenv("JOB_CONFIG_GCS_PATH", unset = "")
    if (cfg_path == "") {
        # Fallback when Python client didn't pass overrides
        bucket <- Sys.getenv("GCS_BUCKET", unset = "mmm-app-output")
        cfg_path <- sprintf("gs://%s/training-configs/latest/job_config.json", bucket)
        message("JOB_CONFIG_GCS_PATH not set; falling back to ", cfg_path)
    }
    tmp <- tempfile(fileext = ".json")
    gcs_download(cfg_path, tmp)  # Download config from GCS
    on.exit(unlink(tmp), add = TRUE)
    jsonlite::fromJSON(tmp)  # Parse JSON
}
```

**Key points:**
1. R script looks for `JOB_CONFIG_GCS_PATH` environment variable
2. This should point to a GCS path like `gs://bucket/training-configs/20260212_151325_319/job_config.json`
3. R script downloads this JSON file and parses it
4. If not found, falls back to `training-configs/latest/job_config.json`

### What Was Wrong

The `process_queue_simple.py` script was passing parameters as **individual environment variables**:

```python
# OLD CODE (WRONG!)
env_vars = []
for key, value in config.items():
    if value is not None:
        env_vars.append(
            run_v2.EnvVar(name=str(key).upper(), value=str(value))
        )
# Creates: COUNTRY=de, REVISION=default, TIMESTAMP=20260212_151325_319, etc.
```

**The R script completely ignored these!** It only reads from the JSON file.

### What Happened Instead

1. R script didn't find `JOB_CONFIG_GCS_PATH` environment variable
2. Fell back to `training-configs/latest/job_config.json`
3. Downloaded whatever config was there (could be from a different job!)
4. Used wrong/stale parameters:
   - Wrong timestamp
   - Wrong country
   - Wrong revision
   - Potentially wrong everything else
5. Saved results to a different location than Python logged
6. Job completed "successfully" (no crash, R script ran fine)
7. But results weren't where expected

### How Streamlit App Does It Correctly

The Streamlit app (`app/app_split_helpers.py`) does this:

```python
# 1. Build job config JSON
config = build_job_config_from_params(params, data_gcs_path, timestamp, annotations_gcs_path)

# 2. Save to temp file
with tempfile.TemporaryDirectory() as td:
    config_path = os.path.join(td, "job_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    # 3. Upload to GCS (timestamped)
    config_blob = f"training-configs/{timestamp}/job_config.json"
    config_gcs_path = upload_to_gcs(gcs_bucket, config_path, config_blob)
    
    # 4. Also upload to "latest" (fallback)
    upload_to_gcs(gcs_bucket, config_path, "training-configs/latest/job_config.json")

# 5. Launch job (job_manager.create_execution reads from latest by default)
execution_name = job_manager.create_execution(TRAINING_JOB_NAME)
```

The R script then downloads from `training-configs/latest/job_config.json` and gets the correct config!

## The Fix

Updated `launch_cloud_run_job()` in `process_queue_simple.py` to match the Streamlit app pattern:

### Step 1: Build Complete Job Config JSON

```python
job_config = {
    "country": params.get("country", ""),
    "iterations": int(params.get("iterations", 2000)),
    "trials": int(params.get("trials", 5)),
    "train_size": params.get("train_size", [0.7, 0.9]),
    "revision": params.get("revision", "default"),
    "date_input": params.get("date_input", ""),
    "start_date": params.get("start_date", "2024-01-01"),
    "end_date": params.get("end_date", ""),
    "gcs_bucket": bucket_name,
    "data_gcs_path": params.get("data_gcs_path", ""),
    "annotations_gcs_path": "",
    "paid_media_spends": params.get("paid_media_spends", []),
    "paid_media_vars": params.get("paid_media_vars", []),
    "context_vars": params.get("context_vars", []),
    "factor_vars": params.get("factor_vars", []),
    "organic_vars": params.get("organic_vars", []),
    "timestamp": timestamp,
    "output_timestamp": timestamp,  # For consistent result paths
    "dep_var": params.get("dep_var", "UPLOAD_VALUE"),
    "dep_var_type": params.get("dep_var_type", "revenue"),
    "date_var": params.get("date_var", "date"),
    "adstock": params.get("adstock", "geometric"),
    "hyperparameter_preset": params.get("hyperparameter_preset", "Meshed recommend"),
    "resample_freq": params.get("resample_freq", "none"),
    "use_parquet": True,
    "parallel_processing": True,
}
```

### Step 2: Upload to GCS

```python
import tempfile
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
    json.dump(job_config, tmp, indent=2)
    tmp_path = tmp.name

try:
    client_storage = storage.Client(credentials=credentials)
    bucket = client_storage.bucket(bucket_name)
    
    # Upload to timestamped location
    config_blob_path = f"training-configs/{timestamp}/job_config.json"
    blob = bucket.blob(config_blob_path)
    blob.upload_from_filename(tmp_path, content_type="application/json")
    config_gcs_path = f"gs://{bucket_name}/{config_blob_path}"
    
    # Also upload to "latest" for fallback
    latest_blob = bucket.blob("training-configs/latest/job_config.json")
    latest_blob.upload_from_filename(tmp_path, content_type="application/json")
    
    logger.info(f"   Uploaded job config to: {config_gcs_path}")
finally:
    os.unlink(tmp_path)
```

### Step 3: Set JOB_CONFIG_GCS_PATH Environment Variable

```python
# Set JOB_CONFIG_GCS_PATH environment variable (this is what R script reads!)
env_vars = [
    run_v2.EnvVar(name="JOB_CONFIG_GCS_PATH", value=config_gcs_path)
]
```

Now the R script will:
1. Find `JOB_CONFIG_GCS_PATH` environment variable
2. Download config from that specific GCS path
3. Use the correct timestamp, country, revision, etc.
4. Save results to the exact location Python logged

## Expected Behavior After Fix

### Job Launch Log

```
Processing job 10/12
  Country: de
  Revision: default
  Benchmark variant: geometric
  Benchmark test: adstock
  Job ID: N/A
📂 Results will be saved to:
   gs://mmm-app-output/robyn/default/de/20260212_151325_319/
   Key files: model_summary.json, best_model_plots.png, console.log
📋 Job configuration:
   country: de
   revision: default
   timestamp: 20260212_151325_319
   data_gcs_path: gs://mmm-app-output/mapped-datasets/de/20251211_115528/raw.parquet
   benchmark_variant: geometric
   Uploaded job config to: gs://mmm-app-output/training-configs/20260212_151325_319/job_config.json
✅ Launched job: mmm-app-dev-training
   Execution: projects/.../executions/mmm-app-dev-training-xbwds
```

### Job Completion Log

```
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260212_151325_319/
   Verifying results in GCS...
   ✓ Results verified: Found 12 files
   ✓ Key files found: model_summary.json, best_model_plots.png, console.log
```

### Manual Verification

```bash
gsutil ls gs://mmm-app-output/robyn/default/de/20260212_151325_319/
# gs://mmm-app-output/robyn/default/de/20260212_151325_319/best_model_plots.png
# gs://mmm-app-output/robyn/default/de/20260212_151325_319/console.log
# gs://mmm-app-output/robyn/default/de/20260212_151325_319/model_summary.json
# ... etc
```

## Testing

### Run a Test Benchmark

```bash
# 1. Submit benchmark jobs
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run

# 2. Process the queue
python scripts/process_queue_simple.py --loop --cleanup
```

### What to Check

1. **Config upload log appears:**
   ```
   Uploaded job config to: gs://mmm-app-output/training-configs/20260212_151325_319/job_config.json
   ```

2. **Config file exists in GCS:**
   ```bash
   gsutil ls gs://mmm-app-output/training-configs/20260212_151325_319/
   gsutil cat gs://mmm-app-output/training-configs/20260212_151325_319/job_config.json | jq .
   ```

3. **Results appear at logged location:**
   ```bash
   gsutil ls gs://mmm-app-output/robyn/default/de/20260212_151325_319/
   ```

4. **Verification shows files found:**
   ```
   ✓ Results verified: Found 12 files
   ✓ Key files found: model_summary.json, best_model_plots.png, console.log
   ```

## Backward Compatibility

The fix maintains backward compatibility:
- Still sets individual environment variables (for any other code that might use them)
- Still sets JOB_PARAMS as JSON
- Just adds the critical JOB_CONFIG_GCS_PATH that R script needs

## Summary

**Before:** R script used wrong/stale config → saved to wrong location → results not found

**After:** R script gets correct config from GCS → saves to logged location → results found!

This was a critical architectural mismatch that went undetected because:
1. Jobs didn't crash (R script ran fine with fallback config)
2. Results were being saved somewhere (just not where expected)
3. No error messages (everything "succeeded")

The fix aligns the queue processor with how the Streamlit app launches jobs, ensuring consistent behavior across all job launch mechanisms.
