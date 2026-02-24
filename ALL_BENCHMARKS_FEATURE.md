# All Benchmarks Feature

## Overview

The `--all-benchmarks` flag allows you to run ALL benchmark test configurations with a single command, eliminating the need to run each benchmark separately.

## Problem Solved

**Before:** You had to run each benchmark type individually:
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
python scripts/benchmark_mmm.py --config benchmarks/train_val_test_splits.json
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json
python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json
```

**After:** Run everything with one command:
```bash
python scripts/benchmark_mmm.py --all-benchmarks
```

## Usage

### Basic Usage

```bash
# Run all benchmarks with full settings
python scripts/benchmark_mmm.py --all-benchmarks
```

### With Test Mode (Recommended for Validation)

```bash
# Run all benchmarks with reduced resources (10 iterations, 1 trial per variant)
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

This is **highly recommended** for:
- First-time validation
- Testing the complete pipeline
- Verifying queue processing works
- Lower cost testing (~1-2 hours vs 4-6+ hours)

### Dry Run (Preview)

```bash
# See what would be submitted without actually submitting
python scripts/benchmark_mmm.py --all-benchmarks --dry-run
```

### With Custom Queue

```bash
# Submit to a specific queue
python scripts/benchmark_mmm.py --all-benchmarks --queue-name my-queue
```

## What It Does

1. **Discovers** all benchmark configuration files in `benchmarks/` directory
2. **Filters out** comprehensive_benchmark.json (since it's for cartesian testing)
3. **Loads** each benchmark configuration
4. **Generates** variants for each test type
5. **Submits** all variants to the queue with unique benchmark IDs
6. **Reports** summary of all submitted jobs

## Benchmarks Included

By default, runs these test types:

1. **adstock_comparison** - Tests different adstock transformations
   - Variants: 3 (geometric, weibull_cdf, weibull_pdf)

2. **train_val_test_splits** - Tests different split ratios
   - Variants: 5 (70/90, 70/95, 65/80, 75/90, 60/85)

3. **time_aggregation** - Tests daily vs weekly
   - Variants: 2 (daily, weekly)

4. **spend_var_mapping** - Tests spend→var mapping strategies
   - Variants: 3 (all_spend, all_proxy, mixed_by_funnel)

**Total:** ~13 variants across 4 benchmark types

## Example Output

### Full Execution

```bash
$ python scripts/benchmark_mmm.py --all-benchmarks

🚀 ALL BENCHMARKS MODE
================================================================================

Discovered 4 benchmark configuration(s):
--------------------------------------------------------------------------------
  ✓ adstock_comparison: 3 variants
    (Compare different adstock transformation types to find best fit per c...)
  ✓ train_val_test_splits: 5 variants
    (Compare different train/validation/test split ratios to understand ove...)
  ✓ time_aggregation: 2 variants
    (Compare daily vs weekly aggregation to find optimal granularity)
  ✓ spend_var_mapping: 3 variants
    (Compare spend→spend vs spend→proxy mappings to determine best approac...)
--------------------------------------------------------------------------------
Total estimated variants: 13

⏱️  Full benchmark execution
  Expected time: ~260-390 minutes

================================================================================
Processing benchmarks...


📊 Processing: adstock_comparison
------------------------------------------------------------
  Generated 3 variant(s)
  ✅ Submitted 3 job(s) to queue 'default-dev'

📊 Processing: train_val_test_splits
------------------------------------------------------------
  Generated 5 variant(s)
  ✅ Submitted 5 job(s) to queue 'default-dev'

📊 Processing: time_aggregation
------------------------------------------------------------
  Generated 2 variant(s)
  ✅ Submitted 2 job(s) to queue 'default-dev'

📊 Processing: spend_var_mapping
------------------------------------------------------------
  Generated 3 variant(s)
  ✅ Submitted 3 job(s) to queue 'default-dev'

================================================================================
📋 SUMMARY
================================================================================
  ✅ Submitted: adstock_comparison
    Benchmark ID: adstock_comparison_20260224_120000
    Variants: 3
  ✅ Submitted: train_val_test_splits
    Benchmark ID: train_val_test_splits_20260224_120001
    Variants: 5
  ✅ Submitted: time_aggregation
    Benchmark ID: time_aggregation_20260224_120002
    Variants: 2
  ✅ Submitted: spend_var_mapping
    Benchmark ID: spend_var_mapping_20260224_120003
    Variants: 3
--------------------------------------------------------------------------------
✅ Total variants queued: 13
Queue: default-dev

💡 Process the queue with:
  python scripts/process_queue_simple.py --loop --cleanup
```

### With --test-run-all

```bash
$ python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

🚀 ALL BENCHMARKS MODE
================================================================================

Discovered 4 benchmark configuration(s):
--------------------------------------------------------------------------------
  ✓ adstock_comparison: 3 variants
  ✓ train_val_test_splits: 5 variants
  ✓ time_aggregation: 2 variants
  ✓ spend_var_mapping: 3 variants
--------------------------------------------------------------------------------
Total estimated variants: 13

🧪 TEST RUN ALL MODE
  Iterations: 10 (reduced from default)
  Trials: 1 (reduced from default)
  Expected time: ~65-130 minutes

================================================================================
Processing benchmarks...

[... processing output ...]

✅ Total variants queued: 13
Queue: default-dev

💡 Process the queue with:
  python scripts/process_queue_simple.py --loop --cleanup
```

## Timing Expectations

| Mode | Variants | Estimated Time | Use Case |
|------|----------|----------------|----------|
| Full | 13 | ~4-6 hours | Production benchmarking |
| --test-run-all | 13 | ~1-2 hours | Quick validation |
| --dry-run | 13 | <1 minute | Preview only |

**Per Variant Times:**
- Full run: ~15-30 minutes
- Test run: ~5-10 minutes

## Complete Workflow

### 1. Preview What Will Run

```bash
python scripts/benchmark_mmm.py --all-benchmarks --dry-run
```

### 2. Run with Test Mode (Recommended First)

```bash
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all
```

### 3. Process the Queue

```bash
python scripts/process_queue_simple.py --loop --cleanup
```

### 4. Monitor Progress

Watch the queue processor output to see jobs completing.

### 5. Collect Results (When Complete)

For each benchmark:
```bash
python scripts/benchmark_mmm.py --collect-results adstock_comparison_20260224_120000 --export-format csv
python scripts/benchmark_mmm.py --collect-results train_val_test_splits_20260224_120001 --export-format csv
```

Or check individual results in GCS at:
```
gs://mmm-app-output/robyn/default/{country}/{timestamp}/
```

## Benefits

✅ **Single Command** - No need to run 4+ separate commands
✅ **Comprehensive Testing** - All test dimensions covered
✅ **Time Efficient** - With --test-run-all, validates everything in ~1-2 hours
✅ **Queue Validation** - Tests multi-job queue processing
✅ **Systematic** - Consistent execution across all test types
✅ **Cost Effective** - Test mode reduces compute costs

## Flags Compatibility

| Flag | Compatible? | Notes |
|------|-------------|-------|
| --test-run-all | ✅ Yes | Recommended for quick validation |
| --test-run | ⚠️ Caution | Only runs first variant of each benchmark |
| --dry-run | ✅ Yes | Preview without submitting |
| --no-submit | ✅ Yes | Save plans without queueing |
| --queue-name | ✅ Yes | Specify target queue |
| --config | ❌ No | Mutually exclusive |
| --trigger-queue | ✅ Yes | Auto-trigger processing after submit |

## Error Handling

### No Configs Found
```
❌ No benchmark configuration files found in benchmarks/ directory
```
**Solution:** Ensure you're running from the repository root with `benchmarks/` directory present.

### Both --all-benchmarks and --config
```
❌ Error: Use either --all-benchmarks OR --config, not both
```
**Solution:** Choose one or the other, not both.

### Config Generation Failed
Individual benchmarks that fail to generate variants will be skipped with a warning, and processing continues with remaining benchmarks.

## Advanced Usage

### Custom Selection

To run only specific benchmarks, use individual --config commands:
```bash
# Run just adstock and time aggregation tests
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json --test-run-all
```

### Batch Processing

Submit all benchmarks, then let queue processor handle them:
```bash
# Submit everything
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# In another terminal/session, process continuously
python scripts/process_queue_simple.py --loop --cleanup

# Queue processor will work through all 13 jobs
```

## See Also

- `TESTING_GUIDE.md` - Complete testing documentation
- `BENCHMARKING_GUIDE.md` - Full benchmarking system guide
- `QUICK_TEST_REFERENCE.md` - Quick reference for commands
- `TEST_RUN_ALL_FEATURE.md` - Details on --test-run-all flag
