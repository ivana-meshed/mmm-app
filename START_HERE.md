# ⚡ START HERE ⚡

## Your Jobs Are Stuck. Here's How To Fix It.

### THE FIX (One Command)

```bash
python scripts/process_queue_simple.py --loop
```

### Quick Setup

```bash
# 1. Get latest code
git pull origin copilot/build-benchmarking-script

# 2. Make sure you're authenticated
gcloud auth application-default login

# 3. Run the processor
python scripts/process_queue_simple.py --loop
```

### What Will Happen

You'll see:
```
============================================================
MMM Queue Processor (Standalone)
============================================================
Queue: default-dev
Bucket: mmm-app-output
...
📊 Queue Status: default-dev
  Total: 12
  Pending: 12
  Running: 0

Processing job 1/12
  Country: de
  Revision: 20251211_115528

✅ Launched job: mmm-app-dev-training
✅ Job launched successfully

...

✅ Processed 12 job(s)
============================================================
```

### Verify Jobs Are Running

**Option 1 - Cloud Console (easiest):**
```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

Look for "mmm-app-dev-training" with active executions.

**Option 2 - Command line:**
```bash
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1
```

### After Jobs Complete

Jobs take 15-30 minutes to run. Then:

1. **Check results:**
   ```bash
   gsutil ls gs://mmm-app-output/robyn-results/de/
   ```

2. **Collect benchmark results:**
   ```bash
   python scripts/benchmark_mmm.py --collect-results adstock_comparison_20260211_101832
   ```

### That's It!

No more debugging. No more complexity. Just run:
```bash
python scripts/process_queue_simple.py --loop
```

Your jobs will launch and complete.

---

## Why This Works Now

After 13 issues, we discovered:
- Streamlit doesn't work for HTTP endpoints
- Import dependencies caused errors
- Solution: Self-contained script

The simple processor:
- ✅ No Streamlit
- ✅ No app imports
- ✅ Pure Python
- ✅ Just works

## Need Help?

**Error: "Authentication failed"**
```bash
gcloud auth application-default login
```

**Error: "Permission denied"**
Make sure you have access to:
- GCS bucket: mmm-app-output
- Cloud Run jobs: mmm-app-dev-training

**Error: "Job not found"**
List available jobs:
```bash
gcloud run jobs list --region=europe-west1
```

Use the correct name with `--training-job-name` flag.

## More Details

See these files for more information:
- **FINAL_FIX.md** - Complete explanation
- **USE_SIMPLE_PROCESSOR.md** - Detailed usage guide
- **ISSUE_13_COMPLETE.md** - Full problem-solving journey

---

## Bottom Line

**Problem:** Queue stuck, jobs not running
**Solution:** Simple processor script
**Action:** Run one command
**Result:** Jobs launch and complete

```bash
python scripts/process_queue_simple.py --loop
```

**That's all you need to do!** 🎉
