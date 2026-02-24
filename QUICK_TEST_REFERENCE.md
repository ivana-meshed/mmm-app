# Quick Test Reference Card

One-page reference for testing the benchmarking system.

## 🚀 Quick Start (5 Minutes)

### 1. Setup
```bash
gcloud auth application-default login
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json
```

### 2. Verify
```bash
gsutil ls gs://mmm-app-output/
python scripts/benchmark_mmm.py --list-configs
```

### 3. Test
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run
python scripts/process_queue_simple.py --loop --cleanup
```

**Expected Time:** 5-10 minutes
**Expected Result:** Job completes, results verified in GCS

---

## 📋 Command Cheat Sheet

### Benchmarking

```bash
# List available benchmarks
python scripts/benchmark_mmm.py --list-configs

# Preview without submission
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run

# Quick test (10 iterations, 1 trial, first variant only)
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run

# Full benchmark (all variants, full iterations)
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json

# Collect results
python scripts/benchmark_mmm.py --collect-results BENCHMARK_ID --export-format csv

# List results
python scripts/benchmark_mmm.py --list-results BENCHMARK_ID

# Show result location
python scripts/benchmark_mmm.py --show-results-location BENCHMARK_ID
```

### Queue Processing

```bash
# Process queue with cleanup (recommended)
python scripts/process_queue_simple.py --loop --cleanup

# Process one job only
python scripts/process_queue_simple.py

# Process until empty (no cleanup)
python scripts/process_queue_simple.py --loop

# Process N jobs
python scripts/process_queue_simple.py --count 5
```

### GCS Operations

```bash
# List benchmark plans
gsutil ls gs://mmm-app-output/benchmarks/

# List job configs
gsutil ls gs://mmm-app-output/training-configs/

# List results
gsutil ls gs://mmm-app-output/robyn/default/de/

# View specific result
gsutil cat gs://mmm-app-output/robyn/default/de/TIMESTAMP/model_summary.json | jq .

# Check queue
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq .
```

### Cloud Run

```bash
# List recent executions
gcloud run jobs executions list --job=mmm-app-dev-training --region=europe-west1 --limit=5

# View execution logs
gcloud run jobs executions logs read EXECUTION_NAME --region=europe-west1 --limit=100

# Describe job
gcloud run jobs describe mmm-app-dev-training --region=europe-west1
```

---

## ✅ Success Checklist

### Quick Test
- [ ] Auth setup complete (no errors with `gsutil ls`)
- [ ] Benchmark submission works (`✅ Benchmark submitted successfully!`)
- [ ] Queue processor launches job (`✅ Launched job: mmm-app-dev-training`)
- [ ] Job completes (`✅ Job completed: geometric`)
- [ ] Results verified (`✓ Results verified: Found X files`)
- [ ] Files exist in GCS at logged path

### Full Test
- [ ] All variants submitted (count matches config)
- [ ] Jobs process sequentially
- [ ] Each job creates results in GCS
- [ ] Result paths match logs
- [ ] Results collected successfully
- [ ] CSV exported with all metrics

---

## 🐛 Troubleshooting Quick Fixes

| Problem | Quick Fix |
|---------|-----------|
| Permission denied | Keep export on ONE line (no line breaks) |
| Module not found | `pip install -r requirements.txt` |
| No files after 10s | Job still running, wait or check logs |
| Wrong GCS path | Verify fix: Python and R use same timestamp |
| Queue stuck | Check Cloud Run status, may need restart |
| Config not found | Use `--list-configs` to see available |

---

## 📊 Expected Outputs

### Console: Benchmark Submission
```
✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260224_111231_test
Variants queued: 1
Queue: default-dev
```

### Console: Queue Processing
```
✅ Launched job: mmm-app-dev-training
   Execution: projects/.../executions/mmm-app-dev-training-abc123
📂 Results will be saved to:
   gs://mmm-app-output/robyn/default/de/20260224_111301_890/
```

### Console: Job Completion
```
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260224_111301_890/
   Verifying results in GCS...
   ✓ Results verified: Found 12 files
   ✓ Key files found: model_summary.json, best_model_plots.png, console.log
```

### GCS: Result Files
```
model_summary.json       # Main metrics and model info
best_model_plots.png     # Visualization
console.log             # R script output
pareto_front.csv        # Pareto optimal models
allocation_*.csv        # Budget allocation results
decomposition_*.csv     # Media contribution
```

---

## ⏱️ Timing Expectations

| Operation | Test Mode | Full Mode |
|-----------|-----------|-----------|
| Submit benchmark | 10s | 10s |
| Launch job | 5s | 5s |
| Job execution | 2-5 min | 15-30 min |
| Verification | 10s | 10s |
| **Per job** | **3-6 min** | **16-31 min** |
| **3 variants** | **10-20 min** | **50-95 min** |

---

## 🎯 Common Workflows

### Workflow 1: Quick Validation
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run
python scripts/process_queue_simple.py --loop --cleanup
# Wait 5-10 minutes
gsutil ls gs://mmm-app-output/robyn/default/de/
```

### Workflow 2: Full Benchmark
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
python scripts/process_queue_simple.py --loop --cleanup
# Wait 1-2 hours
python scripts/benchmark_mmm.py --collect-results BENCHMARK_ID --export-format csv
```

### Workflow 3: Multiple Benchmarks
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json
python scripts/benchmark_mmm.py --config benchmarks/time_aggregation.json
python scripts/benchmark_mmm.py --config benchmarks/spend_var_mapping.json
python scripts/process_queue_simple.py --loop --cleanup
# Wait for all to complete
python scripts/benchmark_mmm.py --collect-results adstock_comparison_ID --export-format csv
python scripts/benchmark_mmm.py --collect-results time_aggregation_ID --export-format csv
python scripts/benchmark_mmm.py --collect-results spend_var_mapping_ID --export-format csv
```

---

## 📚 Documentation Links

- **TESTING_GUIDE.md** - Full testing guide (this document's detailed version)
- **BENCHMARKING_GUIDE.md** - Complete benchmarking documentation
- **benchmarks/README.md** - Configuration reference
- **benchmarks/WORKFLOW_EXAMPLE.md** - Detailed workflow examples
- **JOB_CONFIG_FIX.md** - Technical details of fixes
- **DATA_FLOW_VERIFICATION.md** - System architecture

---

## 🆘 Getting Help

```bash
# Script help
python scripts/benchmark_mmm.py --help
python scripts/process_queue_simple.py --help

# Check logs
gcloud run jobs executions logs read EXECUTION_NAME --region=europe-west1

# View queue
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq .

# Check results
gsutil ls gs://mmm-app-output/robyn/default/de/
```

---

## 💡 Pro Tips

1. **Always use --test-run first** to validate before expensive runs
2. **Use --dry-run to preview** configuration changes
3. **Monitor costs** - full benchmarks can be expensive
4. **Check queue before submitting** to avoid conflicts
5. **Use --cleanup** to keep queue manageable
6. **Save benchmark IDs** for result collection later
7. **Document your findings** as you test different configurations

---

**Last Updated:** 2026-02-24  
**Version:** PR #170 - Complete Benchmarking System
