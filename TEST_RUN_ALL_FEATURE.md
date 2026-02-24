# Testing All Variants with Reduced Resources

## New Feature: --test-run-all

Added `--test-run-all` flag to test ALL benchmark variants with reduced iterations and trials. This validates queue processing with multiple jobs without waiting hours.

## Usage Comparison

### Test First Variant Only (Existing)
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --test-run
```

**Result:**
- Runs: 1 variant (first one only)
- Iterations: 10 (reduced from 2000)
- Trials: 1 (reduced from 5)
- Time: ~5-10 minutes
- Purpose: Quick validation

### Test ALL Variants (NEW)
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --test-run-all
```

**Result:**
- Runs: ALL variants (e.g., 3 for adstock comparison)
- Iterations: 10 (reduced from 2000)
- Trials: 1 (reduced from 5)
- Time: ~15-30 minutes for 3 variants
- Purpose: Queue validation with multiple jobs

## Example Output

### adstock_comparison.json (3 variants)

```bash
$ python scripts/benchmark_mmm.py \
    --config benchmarks/adstock_comparison.json \
    --test-run-all
```

**Expected Output:**
```
2026-02-24 12:45:00,123 - INFO - Loaded benchmark: adstock_comparison
2026-02-24 12:45:00,124 - INFO - Description: Compare different adstock transformation types
2026-02-24 12:45:00,456 - INFO - Loaded base config: de/UPLOAD_VALUE
2026-02-24 12:45:00,456 - INFO - Generated 3 test variants

🧪 TEST RUN ALL MODE
Generated 3 variants - ALL will run with reduced resources
Iterations: 10 (reduced from 2000)
Trials: 1 (reduced from 5)

Variants to test:
  1. geometric
  2. weibull_cdf
  3. weibull_pdf

💡 This tests queue processing with multiple jobs
💡 Expected time: ~15-30 minutes
💡 To test just one variant, use --test-run instead

2026-02-24 12:45:01,234 - INFO - Saved benchmark plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260224_124500_testall/plan.json
2026-02-24 12:45:01,567 - INFO - Saved queue: gs://mmm-app-output/robyn-queues/default-dev/queue.json
2026-02-24 12:45:01,568 - INFO - Submitted 3 benchmark jobs to queue 'default-dev'

✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260224_124500_testall
Variants queued: 3
Queue: default-dev
Plan: gs://mmm-app-output/benchmarks/adstock_comparison_20260224_124500_testall/plan.json
```

### Process Queue
```bash
python scripts/process_queue_simple.py --loop --cleanup
```

**What Happens:**
1. Launches job 1 (geometric) → runs ~5 min → completes
2. Launches job 2 (weibull_cdf) → runs ~5 min → completes
3. Launches job 3 (weibull_pdf) → runs ~5 min → completes
4. All results verified in GCS

**Total Time:** ~15-20 minutes for 3 variants

## Use Cases

### 1. Queue Validation
Test that queue processor can handle multiple jobs correctly:
```bash
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json --test-run-all
python scripts/process_queue_simple.py --loop --cleanup
```

### 2. Config Validation
Verify all variants in a benchmark can be generated without errors:
```bash
python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json --test-run-all
```

### 3. End-to-End Test
Full pipeline test with minimal cost:
```bash
# Submit all variants
python scripts/benchmark_mmm.py --config benchmarks/train_val_test_splits.json --test-run-all

# Process
python scripts/process_queue_simple.py --loop --cleanup

# Collect results
python scripts/benchmark_mmm.py --collect-results train_val_test_splits_TIMESTAMP_testall --export-format csv
```

## Timing Estimates

| Benchmark | Variants | Time (test-run) | Time (test-run-all) | Time (full) |
|-----------|----------|-----------------|---------------------|-------------|
| adstock_comparison | 3 | ~5 min | ~15 min | ~90 min |
| time_aggregation | 2 | ~5 min | ~10 min | ~60 min |
| spend_var_mapping | 3 | ~5 min | ~15 min | ~90 min |
| train_val_test_splits | 5 | ~5 min | ~25 min | ~150 min |
| comprehensive_benchmark | 30 | ~5 min | ~150 min | ~900 min |

## Benefits

1. **Queue Testing**: Validates queue can handle multiple jobs
2. **Variant Validation**: Ensures all variants generate correctly
3. **Cost Effective**: 10 iterations vs 2000 iterations
4. **Time Efficient**: Minutes instead of hours
5. **Complete Coverage**: Tests all scenarios, not just first

## Comparison Table

| Feature | --test-run | --test-run-all | Full Run |
|---------|-----------|----------------|----------|
| Variants | First only | All | All |
| Iterations | 10 | 10 | 2000 |
| Trials | 1 | 1 | 5 |
| Purpose | Quick check | Queue validation | Production |
| Time | ~5 min | ~5-30 min | 1-2 hours |
| Cost | Minimal | Low | Standard |

## Error Handling

Cannot use both flags together:
```bash
$ python scripts/benchmark_mmm.py --config benchmarks/adstock.json --test-run --test-run-all

❌ Error: Use either --test-run OR --test-run-all, not both
  --test-run: Tests first variant only
  --test-run-all: Tests all variants with reduced resources
```

## Tips

1. **Start with --test-run** for initial validation
2. **Use --test-run-all** to validate queue processing
3. **Run full benchmark** only after both pass
4. **Check logs** to confirm reduced iterations are used
5. **Monitor costs** - even test runs have minimal compute costs

## Next Steps

After successful test-run-all:
1. Review results in GCS
2. Verify all variants completed
3. Check that paths are correct
4. Run full benchmark if needed
5. Collect and analyze results

---

**Added in:** PR #170  
**Date:** 2026-02-24  
**Purpose:** Queue validation with multiple jobs
