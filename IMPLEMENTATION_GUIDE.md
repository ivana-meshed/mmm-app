# Implementation Guide - PR #170 Benchmarking System

## Overview

This PR implements a comprehensive benchmarking system for systematically testing Marketing Mix Modeling (MMM) configurations. The system allows testing different configurations (adstock types, train/test splits, time aggregation, spend→variable mappings) to identify optimal setups for MMM models.

## What Was Implemented

### 1. Core Benchmarking Script (`scripts/benchmark_mmm.py`)

**Purpose:** Orchestrates benchmark execution, variant generation, and result collection.

**Key Features:**
- Generates test variants based on configuration files
- Submits jobs to Cloud Run queue
- Collects and exports results
- Supports multiple benchmark types

**Components:**
- `BenchmarkConfig` class - Configuration validation
- `BenchmarkRunner` class - Variant generation and submission
- `ResultsCollector` class - Result gathering and export

### 2. Queue Processor (`scripts/process_queue_simple.py`)

**Purpose:** Processes queued benchmark jobs and launches Cloud Run training jobs.

**Key Features:**
- Job config JSON creation and GCS upload
- Sets `JOB_CONFIG_GCS_PATH` environment variable
- Passes `output_timestamp` for result path consistency
- Result verification after job completion
- Automatic cleanup of old completed jobs

**Critical Fix:** 
- R script expects config from GCS JSON file, not env vars
- Uploads complete job config to `training-configs/{timestamp}/job_config.json`
- Ensures results are saved exactly where Python logs them

### 3. R Script Updates (`r/run_all.R`)

**Purpose:** Prioritizes `output_timestamp` from config for consistent result paths.

**Key Changes:**
- Lines 658-670: Timestamp priority logic
  1. First: `cfg$output_timestamp` (from Python)
  2. Fallback: `cfg$timestamp`
  3. Last resort: Generate new timestamp
- Ensures results match logged paths

### 4. Benchmark Configurations (`benchmarks/`)

Five benchmark types for systematic testing:

1. **adstock_comparison.json** - Test adstock types
   - Geometric
   - Weibull CDF
   - Weibull PDF

2. **train_val_test_splits.json** - Test split ratios
   - 70/90, 70/95, 65/80, 75/90, 60/85

3. **time_aggregation.json** - Test aggregation levels
   - Daily
   - Weekly

4. **spend_var_mapping.json** - Test spend→variable mappings
   - All spend→spend
   - All spend→proxy (sessions)
   - Mixed by funnel type

5. **comprehensive_benchmark.json** - Cartesian combinations

## System Architecture

### Data Flow

```
1. User runs benchmark_mmm.py with config
2. Script generates variants based on test dimensions
3. Variants submitted to GCS queue (robyn-queues/default-dev/)
4. process_queue_simple.py monitors queue
5. For each job:
   a. Build complete job config JSON
   b. Upload to GCS: training-configs/{timestamp}/job_config.json
   c. Set JOB_CONFIG_GCS_PATH env var
   d. Launch Cloud Run Job with config path
6. R script downloads config from GCS path
7. R uses output_timestamp from config
8. Results saved to: robyn/default/{country}/{timestamp}/
9. Python verifies results exist
10. Results collected and exported
```

### Result Path Consistency

**Problem Solved:** Results weren't appearing where Python logged them.

**Root Cause:** R script expected config from GCS JSON file but was getting individual env vars which it ignored.

**Solution:**
1. Python creates complete job config JSON
2. Uploads to GCS at `training-configs/{timestamp}/job_config.json`
3. Sets `JOB_CONFIG_GCS_PATH` to that path
4. R downloads and reads JSON config
5. R uses `output_timestamp` from config
6. Results appear at exact logged path

## Key Functions

### benchmark_mmm.py

```python
# Generate variants for a benchmark
def generate_variants(config, test_dimensions)
    # Returns list of variant configs

# Submit variants to queue
def submit_to_queue(variants, queue_name)
    # Uploads to GCS queue

# Collect results
def collect_results(benchmark_id, export_format)
    # Gathers metrics, exports CSV/Parquet
```

### process_queue_simple.py

```python
# Process one job from queue
def process_one_job(job, queue_name)
    # Builds config, uploads to GCS, launches job

# Launch Cloud Run Job
def launch_cloud_run_job(job_config, execution_name)
    # Creates job config JSON
    # Uploads to GCS
    # Sets JOB_CONFIG_GCS_PATH
    # Launches Cloud Run Job

# Verify results exist
def verify_results_exist(gcs_path, timeout=10)
    # Checks GCS for result files
    # Returns file list or None
```

## Configuration Structure

### Benchmark Config File

```json
{
  "name": "benchmark_name",
  "description": "What this tests",
  "base_config": {
    "country": "de",
    "goal": "N_UPLOADS_WEB",
    "version": "20260113_160850"
  },
  "iterations": 2000,
  "trials": 5,
  "variants": {
    "dimension_name": [
      {
        "name": "variant_name",
        "description": "What this variant does",
        "parameter": "value"
      }
    ]
  }
}
```

### Job Config (uploaded to GCS)

```json
{
  "country": "de",
  "revision": "default",
  "timestamp": "20260212_172440_243",
  "output_timestamp": "20260212_172440_243",
  "iterations": 2000,
  "trials": 5,
  "train_size": [0.7, 0.9],
  "adstock": "geometric",
  "hyperparameter_preset": "Meshed recommend",
  "paid_media_spends": ["GA_TOTAL_COST_CUSTOM", ...],
  "paid_media_vars": ["GA_TOTAL_SESSIONS_CUSTOM", ...],
  "context_vars": ["TV_IS_ON"],
  "organic_vars": ["SEO_DAILY_SESSIONS", ...],
  "factor_vars": ["TV_IS_ON"],
  "dep_var": "N_UPLOADS_WEB",
  "data_gcs_path": "gs://bucket/path/to/data.parquet",
  "gcs_bucket": "mmm-app-output"
}
```

## CLI Flags

### Basic Usage

```bash
# Run single benchmark
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json

# Test with reduced resources (first variant only)
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run

# Test all variants with reduced resources
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all

# Run ALL benchmark types
python scripts/benchmark_mmm.py --all-benchmarks

# Quick test of all benchmarks
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# Preview without submitting
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run
```

### Result Management

```bash
# List available benchmarks
python scripts/benchmark_mmm.py --list-configs

# Find results for a benchmark
python scripts/benchmark_mmm.py --list-results benchmark_id

# Show expected result location
python scripts/benchmark_mmm.py --show-results-location benchmark_id

# Collect and export results
python scripts/benchmark_mmm.py --collect-results benchmark_id --export-format csv
```

### Queue Processing

```bash
# Process queue until empty
python scripts/process_queue_simple.py --loop --cleanup

# Process single job
python scripts/process_queue_simple.py

# With cleanup (keep 10 recent completed)
python scripts/process_queue_simple.py --cleanup
```

## Testing Modes

### 1. --test-run (Quick Validation)
- Runs FIRST variant only
- Iterations: 10 (instead of 2000)
- Trials: 1 (instead of 5)
- Time: ~5 minutes
- Use: Validate setup works

### 2. --test-run-all (Queue Testing)
- Runs ALL variants
- Iterations: 10 per variant
- Trials: 1 per variant
- Time: ~15-30 minutes for 3 variants
- Use: Test queue with multiple jobs

### 3. Full Run (Production)
- Runs all variants
- Full iterations and trials
- Time: 1-2 hours per benchmark
- Use: Production benchmarking

## Error Handling

### Common Issues

**1. Results not found**
- Check job logs for errors
- Verify data_gcs_path is accessible
- Check R script logs in Cloud Run

**2. Jobs stuck in "running"**
- Check Cloud Run console
- Review execution logs
- May need to cancel and resubmit

**3. Collection fails**
- Verify benchmark_id is correct
- Check result paths in GCS
- Ensure jobs completed successfully

## Dependencies

### Python Packages
- `google-cloud-storage` - GCS operations
- `google-cloud-run` - Cloud Run Jobs API
- `pandas` - Data manipulation
- `pyarrow` - Parquet support

### R Packages
- `Robyn` - MMM library
- `jsonlite` - JSON parsing
- `googleCloudStorageR` - GCS access

## File Structure

```
mmm-app/
├── scripts/
│   ├── benchmark_mmm.py         # Main benchmarking script
│   └── process_queue_simple.py  # Queue processor
├── r/
│   └── run_all.R                # R training script
├── benchmarks/
│   ├── adstock_comparison.json
│   ├── train_val_test_splits.json
│   ├── time_aggregation.json
│   ├── spend_var_mapping.json
│   └── comprehensive_benchmark.json
├── README.md                     # PR overview
├── IMPLEMENTATION_GUIDE.md       # This file
├── USAGE_GUIDE.md               # How to use
├── ANALYSIS_GUIDE.md            # How to analyze
└── ARCHITECTURE.md              # System architecture
```

## GCS Structure

```
mmm-app-output/
├── robyn-queues/
│   └── default-dev/
│       └── queue.json           # Job queue
├── training-configs/
│   ├── {timestamp}/
│   │   └── job_config.json     # Job configuration
│   └── latest/
│       └── job_config.json     # Fallback config
├── robyn/
│   └── default/
│       └── {country}/
│           └── {timestamp}/     # Results folder
│               ├── model_summary.json
│               ├── console.log
│               ├── best_model_plots.png
│               └── ...
└── benchmarks/
    └── {benchmark_id}/
        ├── plan.json            # Benchmark plan
        └── results.csv          # Collected results
```

## Implementation Timeline

1. **Phase 1:** Result path consistency fix
   - Fixed timestamp passing
   - Job config GCS upload
   - R script priority logic

2. **Phase 2:** Complete benchmarking system
   - Benchmark script with 5 test types
   - CLI flags (test-run, test-run-all, all-benchmarks)
   - Result collection and export
   - Comprehensive documentation

## Success Metrics

- ✅ Results appear at logged paths
- ✅ All 5 test types supported
- ✅ Single command execution (--all-benchmarks)
- ✅ Result verification works
- ✅ Queue processing stable
- ✅ Documentation complete
