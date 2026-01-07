# Quick Testing Guide - 8 vCPU Upgrade

## Objective
Verify that upgrading to 8 vCPU with Gen2 execution environment provides more than 2 cores for parallel processing.

## Quick Test Steps

### 1. Deploy to Dev Environment (5 minutes)

```bash
cd infra/terraform
terraform init
terraform apply -var-file=envs/dev.tfvars
```

**Expected**: Terraform will update the Cloud Run Job configuration to 8 vCPU / 32Gi.

### 2. Trigger a Test Job (via UI)

1. Open the Streamlit app: https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
2. Navigate to "Run Experiment"
3. Submit a small test job (short date range for faster completion)

### 3. Monitor Job Execution (Real-time)

```bash
# Watch job executions
watch -n 5 'gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=3 \
  --format="table(name,status,duration)"'
```

### 4. Check Core Allocation (After Job Starts)

```bash
# Wait 2-3 minutes after job starts, then check logs
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"Final cores for training\"" \
  --limit=10 \
  --format=json | jq -r '.[].textPayload' | grep -A 5 "Final cores"
```

**Look for**:
```
Final cores for training: 7
```

### 5. Check Detailed Diagnostics (If Needed)

```bash
# Get full core detection analysis
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"CORE DETECTION ANALYSIS\"" \
  --limit=50 --format=json | jq -r '.[].textPayload' | less
```

**Look for**:
```
🔍 Detection Methods:
  - parallelly::availableCores():      7 (cgroup-aware)
  - parallel::detectCores():           8 (system CPUs)
  - Conservative estimate:              7
  - Actual cores to use:                7
  - Safety buffer applied:              Yes (-1)
  - Final cores for training:           7
```

## Success Criteria

### ✅ Success (Deploy to Production)
- `parallelly::availableCores()` reports **6-8 cores**
- Training completes **2-4x faster** than baseline
- No job failures

**Action**: Deploy to production
```bash
terraform apply -var-file=envs/prod.tfvars
```

### ⚠️ Partial Success (Keep Testing)
- `parallelly::availableCores()` reports **4-5 cores**
- Training completes **1.5-2x faster**
- No job failures

**Action**: Monitor a few more jobs, then decide to keep or try 16 vCPU

### ❌ No Improvement (Revert or Try Alternative)
- `parallelly::availableCores()` still reports **2 cores**
- Training time unchanged
- May see job failures

**Action**: Revert to 4 vCPU or try alternative solutions (GKE, Cloud Batch)

## Quick Rollback (If Needed)

```bash
cd infra/terraform

# Edit envs/dev.tfvars and envs/prod.tfvars
# Change to:
#   training_cpu = "4.0"
#   training_memory = "16Gi"
#   training_max_cores = "4"

# Optionally remove execution_environment from main.tf

terraform apply -var-file=envs/dev.tfvars  # Test first
terraform apply -var-file=envs/prod.tfvars # Then prod
```

## Troubleshooting

### Job Won't Start
```bash
# Check job configuration
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format=yaml

# Check recent errors
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND severity>=ERROR" \
  --limit=20
```

### Job Fails During Execution
```bash
# Get execution logs
gcloud run jobs executions describe EXECUTION_NAME \
  --job=mmm-app-dev-training \
  --region=europe-west1

# Check console logs
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training" \
  --limit=100 --format=json | jq -r '.[].textPayload' | tail -100
```

### Can't Find Core Detection Logs
The logs appear ~2-3 minutes after job starts (during R initialization).

```bash
# Check job is actually running
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=1

# Wait and try again
sleep 180  # Wait 3 minutes
# Then retry the core detection log query
```

## Performance Comparison

Track these metrics:

| Metric | Baseline (4 vCPU, 2 cores) | Expected (8 vCPU, 7 cores) |
|--------|---------------------------|---------------------------|
| Cores Available | 2 | 7 |
| Training Time (30-day dataset) | ~30 min | ~10 min |
| Cost per Job | $2.92 | $1.95 |
| Throughput (jobs/hour) | 2 | 6 |

## Next Steps After Testing

### If Successful in Dev
1. Document actual core allocation achieved
2. Document actual training time improvement
3. Deploy to production
4. Monitor production for 1 week
5. Update documentation with final results
6. Close the issue

### If Not Successful
1. Document what was observed
2. Revert dev environment
3. Evaluate alternatives:
   - Try 16 vCPU tier
   - Migrate to GKE Autopilot
   - Use Cloud Batch
   - Contact Google Cloud Support
4. Document decision and rationale

## Useful Commands Reference

```bash
# List recent job executions with duration
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=10 \
  --format="table(name,status,duration)"

# Stream logs in real-time
gcloud logging tail \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training"

# Check job configuration
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format=json | jq '.template.template.spec.containers[0].resources'

# Get cost estimate (from Cloud Billing)
gcloud billing accounts list
gcloud billing projects describe datawarehouse-422511
```

## Contact

If you need help with testing or interpretation of results:
- Check `docs/CPU_ALLOCATION_UPGRADE.md` for detailed analysis
- Check `docs/CLOUD_RUN_CPU_SOLUTION.md` for alternative solutions
- Review diagnostic output in logs for specific recommendations
