# MMM Trainer - Cost Optimization Status

**Last Updated:** February 20, 2026 - **Scheduler Configuration Updated**  
**Document Version:** 2.1  
**Status:** ✅ Optimizations Applied - Scheduler Disabled in Production, Optimized in Dev

---

## Executive Summary

This document consolidates all cost optimization information for the MMM Trainer application into a single source of truth. It replaces multiple scattered cost documents with one comprehensive status report.

### Current Cost Status (Updated February 20, 2026)

| Metric | Value | Notes |
|--------|-------|-------|
| **Current Monthly Cost** | ~$9.30/month (projected) | Production with scheduler disabled |
| **GCP Infrastructure** | ~$9.10/month | Excludes scheduler costs in production |
| **GitHub Actions** | $0.21/month | Weekly cleanup workflow |
| **Combined Total** | ~$9.30/month | **Within target range** ✅ |
| **Baseline (Pre-Optimization)** | €148/month (~$160/month) | Historical costs before optimizations |
| **Cost Reduction** | ~94% | Optimizations successfully applied |
| **Target Cost Range** | $8-15/month (idle) | Minimal activity baseline |
| | $25-45/month (moderate) | With regular training jobs |

### Key Optimizations Applied ✅

1. **Scale-to-Zero Enabled** (min_instances=0) - Eliminates idle costs
2. **CPU Throttling Enabled** - Reduces CPU allocation when idle  
3. **Scheduler Configuration Optimized**:
   - **Production**: DISABLED - Manual job triggering (~$0.70/month savings)
   - **Dev**: ENABLED at 30-minute intervals - Reduced from 10 minutes (~$0.20/month savings)
4. **Resource Optimization** (1 vCPU, 2 GB) - Reduced from 2 vCPU, 4 GB
5. **GCS Lifecycle Policies** - Automatic storage class transitions
6. **Artifact Registry Cleanup** - Weekly cleanup of old images

---

## Scheduler Status (Updated: February 20, 2026)

### Production Environment

**Current State:** **DISABLED** for cost optimization

Configuration:
- `scheduler_enabled = false` in `infra/terraform/envs/prod.tfvars`
- `scheduler_interval_minutes = 30` (if re-enabled)

**Benefits:**
- ✅ Eliminates ~$0.70/month in scheduler costs
- ✅ Achieves lowest possible idle costs
- ✅ Production environment typically has on-demand job execution

**Manual Job Triggering:**
```bash
# Trigger queue processing manually via API
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "https://mmm-app-web-wuepn6nq5a-ew.a.run.app/?queue_tick=1&name=default"
```

### Development Environment

**Current State:** **ENABLED** at 30-minute intervals

Configuration:
- `scheduler_enabled = true` in `infra/terraform/envs/dev.tfvars`
- `scheduler_interval_minutes = 30` (reduced from 10 minutes)

**Benefits:**
- ✅ Automatic queue processing for development/testing
- ✅ Saves ~$0.20/month compared to 10-minute intervals
- ✅ Jobs start within 30 minutes (acceptable for dev)

**Cost Impact:**
- Scheduler costs: ~$0.50/month (48 wake-ups/day vs 144 previously)
- Down from ~$0.70/month with 10-minute intervals

---

## Actual Cost Breakdown (February 2026 - Updated Configuration)

Based on actual costs with scheduler optimization:

### Monthly Projections by Environment

| Environment | Idle Cost | With Scheduler | Training Jobs (50/month) |
|-------------|-----------|----------------|-------------------------|
| **Production** | ~$5/month | ~$5/month (disabled) | ~$30/month |
| **Development** | ~$4/month | ~$4.50/month (30-min) | ~$29/month |
| **Total** | **~$9/month** | **~$9.50/month** | **~$59/month** |

### Cost by Category (Current Configuration)

| Category | Estimated Cost | Percentage | Notes |
|----------|---------------|------------|-------|
| **Web Services (base)** | $5.32 | 58.0% | Always-on web application costs |
| **Scheduler (dev only)** | $0.50 | 5.5% | Dev environment at 30-min intervals |
| **Storage & Registry** | $0.14 | 1.5% | Container images & data storage |
| **Base Infrastructure** | $3.14 | 34.5% | Network, API calls, secrets |
| **GitHub Actions** | $0.21 | 2.3% | Weekly cleanup workflow |

**Total Monthly (Idle):** ~$9.30/month

---

## Cost Tracking Scripts

### Available Tools

1. **Daily Cost Tracking**
   ```bash
   python scripts/track_daily_costs.py --days 7 --use-user-credentials
   ```
   - Tracks daily costs by service and category
   - Provides monthly projections
   - Exports to CSV for analysis

2. **Idle Cost Analysis** (Updated February 20, 2026)
   ```bash
   python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
   ```
   - Analyzes costs during idle periods
   - **NOW: Provides dynamic recommendations based on actual configuration**
   - Only suggests changes that are relevant to current setup
   - Includes timeout optimization analysis

### Recent Script Improvements

✅ **Latest Enhancements (February 20, 2026):**
- **Dynamic Recommendations Engine** - Script now checks SERVICE_CONFIGS and only recommends changes that apply
- **Configuration-Aware Analysis** - Detects if CPU throttling is enabled, scheduler status, and intervals
- **Timeout Optimization Analysis** - Analyzes request timeout configuration (currently 300s)
- **Accurate Cost Projections** - Based on actual deployed configuration

✅ **Previous Enhancements (PR #169):**
- Added Secret Manager cost tracking
- Improved Cloud Scheduler service fee detection
- Enhanced service identification logic
- Better categorization of cost types
- Explicit scheduler run costs tracking (service fees + invocations)
- GitHub Actions cost tracking (weekly cleanup automation)
- Dedicated "Scheduler & Automation Costs" breakdown section

**Script Output Examples:**

When all optimizations are applied:
```
Current Configuration:
  - CPU throttling: ENABLED ✓
  - Scheduler: DISABLED (prod) / ENABLED at 30 min (dev)
  - Min instances: 0 (scale-to-zero)

✓ All major cost optimizations are already implemented!
```

When optimizations are needed:
```
Recommendations (in priority order):
1. ENABLE CPU THROTTLING (Highest Priority)
   Expected savings: ~$80-100/month
```

⚠️ **Known Limitations:**
- Requires BigQuery billing export to be enabled
- Requires appropriate IAM permissions (BigQuery Data Viewer)
- Cloud Scheduler base service fee ($0.10/month per job) may not appear until month-end
- Free tier credits not included in calculations
- Billing data has 24-48 hour lag

✅ **Script Accuracy Validated:**

The scripts accurately reflect the current state:
1. **Cost optimizations successfully applied** - Low costs confirm scale-to-zero, CPU throttling, and scheduler optimization are working
2. **All major cost categories captured** - Registry, compute CPU/memory, user requests, networking, storage tracked
3. **Dynamic recommendations** - Only suggests changes relevant to current configuration
4. **Configuration-aware** - Knows when CPU throttling is enabled, scheduler is disabled, etc.

---

## Infrastructure Configuration

### Cloud Run Services

#### Production Web Service (mmm-app-web)
```yaml
Resources:
  CPU: 1 vCPU
  Memory: 2 GB
  Min Instances: 0 (scale-to-zero)
  CPU Throttling: true
  Region: europe-west1
Current Cost: ~$0.01/day ($0.30/month)
```

#### Development Web Service (mmm-app-dev-web)
```yaml
Resources:
  CPU: 1 vCPU
  Memory: 2 GB
  Min Instances: 0 (scale-to-zero)
  CPU Throttling: true
  Region: europe-west1
Current Cost: ~$0.14/day ($4.20/month)
```

#### Training Jobs (prod + dev)
```yaml
Resources:
  CPU: 8 vCPU
  Memory: 32 GB
  Min Instances: 0 (on-demand only)
  Timeout: 21600s (6 hours) for training jobs
  Region: europe-west1
Current Cost: Variable, $0.50-3.00 per job
```

### Request Timeout Configuration

**Web Services:**
```yaml
Timeout: 300s (5 minutes)
```

**Analysis:**
- **Current Setting:** 300s is reasonable for most operations
- **Cost Impact:** Minimal (~$5-10/month potential savings if reduced)
- **Recommendation:** Keep at 300s unless testing shows requests complete faster
- **Trade-offs of reducing:**
  - May terminate legitimate long-running requests
  - Faster failure detection for hung requests
  - Needs testing to ensure operations complete within new limit

**When to consider reducing timeout:**
- If monitoring shows most requests complete in < 120s
- If experiencing frequent hung requests that waste resources
- After thorough testing of request duration patterns

### Cloud Scheduler

**Production:**
```yaml
Status: DISABLED (for cost optimization)
Scheduler Job: robyn-queue-tick
Cost Savings: ~$0.70/month
Manual Trigger: GET /?queue_tick=1&name=default
```

**Development:**
```yaml
Status: ENABLED
Scheduler Job: robyn-queue-tick-dev
Frequency: Every 30 minutes (48 invocations/day)
Base Service Fee: $0.10/month
Invocation Costs: ~$0.40/month
Total Scheduler Cost: ~$0.50/month
```

**Previous Configuration (Feb 18-20):**
- Both prod and dev: Every 10 minutes (144 invocations/day)
- Total cost: ~$0.70/month per environment

**Cost Savings from Optimization:**
- Production: $0.70/month (scheduler disabled)
- Development: $0.20/month (30-min vs 10-min intervals)
- **Total savings:** ~$0.90/month (~$11/year)

### Cloud Storage (GCS)

```yaml
Bucket: mmm-app-output
Region: europe-west1
Lifecycle Policies:
  - 30 days: Standard → Nearline (50% savings)
  - 90 days: Nearline → Coldline (80% savings)
  - 365 days: Delete old queue data
Current Cost: ~$0.08/day ($2.40/month)
```

### Artifact Registry

```yaml
Repository: mmm-repo
Region: europe-west1
Cleanup Policy: Weekly, keeps last 10 tags
Images: mmm-app-web, mmm-app-training, mmm-app-training-base
Current Cost: ~$0.06/day ($1.80/month)
```

---

## Cost Comparison: Before vs After Optimization

### Before Optimization (€148/month ≈ $160/month - Historical Baseline)

| Component | Monthly Cost | Issue |
|-----------|-------------|-------|
| Web Services (idle) | €15-20 | min_instances=2, always running |
| Scheduler | €45-50 | Frequent invocations causing high costs |
| Deployment Churn | €50-60 | 150 deployments × 4-hour overlap |
| Training Jobs | €21.60 | Variable (usage-dependent) ✓ |
| Artifact Registry | €12 | No cleanup, many old versions |
| GCS Storage | €1.50 | No lifecycle policies |

**Total:** €145-214/month depending on usage

**Note:** This baseline represents documented historical costs from initial deployment analysis.

### After Optimization ($9.09/month actual - February 2026)

| Component | Monthly Cost | Solution Applied |
|-----------|-------------|-----------------|
| Web Services (idle) | $0.00 | Scale-to-zero (min_instances=0) ✅ |
| Web Services (requests) | $4.42 | Dev + prod activity (minimal) ✅ |
| Scheduler Service | $0.00 | **Currently disabled** ⚠️ |
| Scheduler Invocations | $0.00 | **Currently disabled** ⚠️ |
| Training Jobs | $4.42 | Variable, minimal during test period ✅ |
| Artifact Registry | $0.06 | Weekly cleanup, keeps last 10 ✅ |
| GCS Storage | $0.08 | Lifecycle policies applied ✅ |
| **GCP Subtotal** | **$8.88/month** | **Main infrastructure costs** |
| GitHub Actions | $0.21 | Weekly cleanup workflow (external) ✅ |
| **Combined Total** | **$9.09/month** | **All costs including external** |

**Cost Reduction:** 94% (from $160 baseline to $9 actual)

**Notes on Current Configuration:**
- Scheduler is **currently disabled** (`scheduler_enabled = false` in tfvars)
- This saves ~$0.70-1.00/month in scheduler costs
- CPU throttling is **enabled** in Terraform configuration
- Training jobs must be processed manually with scheduler disabled

---

## Analysis of Recommendations from Scripts

### Script Output Review (February 18, 2026)

The `analyze_idle_costs.py` script outputs recommendations that are **no longer accurate** for the current configuration:

#### ❌ Recommendation 1: "Enable CPU Throttling"
**Status:** ALREADY IMPLEMENTED
- Terraform configuration shows: `"run.googleapis.com/cpu-throttling" = "true"`
- The script had outdated hardcoded configuration (`throttling: False`)
- **Action Required:** ✅ Script has been updated with correct configuration

#### ❌ Recommendation 2: "Increase Scheduler Interval"
**Status:** NOT APPLICABLE - Scheduler is disabled
- Scheduler is currently disabled (`scheduler_enabled = false`)
- Zero scheduler costs in billing data confirm this
- **If scheduler were enabled:** 10-minute intervals are already reasonable

#### ❌ Recommendation 3: Cost Savings Estimates
**Status:** INCORRECT - Based on wrong assumptions
- Script assumes CPU throttling is disabled (it's not)
- Script assumes scheduler is running every 10 minutes (it's not)
- Projected savings of "$9.50/month" are not achievable since optimizations are already applied

### ✅ Actual Current State Summary (Option 2 Implemented)

**All major optimizations are in place:**
1. ✅ Scale-to-zero enabled (min_instances=0)
2. ✅ CPU throttling enabled
3. ✅ Scheduler **re-enabled** for automated processing (Option 2 - Feb 18, 2026)
4. ✅ Resource optimization (1 vCPU, 2 GB for web services)
5. ✅ GCS lifecycle policies
6. ✅ Artifact Registry weekly cleanup

**Implementation Update (February 18, 2026):**
- ✅ **Option 2 has been implemented** - Scheduler re-enabled for automated job processing
- Previous cost: $9.09/month (scheduler disabled)
- Current projected cost: ~$10/month (scheduler enabled)
- Benefit: Automatic queue processing every 10 minutes, no manual intervention needed

**Current costs (~$10/month projected) are within target range for minimal activity with automation.**

---

## Monthly Cost Projections

### Scenario 1: Minimal Activity (Current State - Option 2)
- **Web Services:** $5.32/month (dev + prod activity with scheduler)
- **Training Jobs:** $4.42/month (minimal jobs)
- **Scheduler:** $0.70/month (re-enabled for automation)
- **Storage & Registry:** $0.14/month
- **GitHub Actions:** $0.21/month
- **Total:** ~$10/month ✅ **Currently implemented (Option 2)**

### Scenario 2: Light Production Use (With Scheduler Enabled)
- **Web Services:** $5-8/month (occasional user interactions)
- **Training Jobs:** $10-20/month (5-10 jobs)
- **Scheduler:** $0.70-1.00/month (enabled)
- **Storage & Registry:** $0.50-1.00/month
- **GitHub Actions:** $0.21/month
- **Total:** ~$17-30/month

### Scenario 3: Moderate Production Use (With Scheduler Enabled)
- **Web Services:** $10-15/month (regular user traffic)
- **Training Jobs:** $25-40/month (15-25 jobs)
- **Scheduler:** $1.00/month (if re-enabled)
- **Storage & Registry:** $2-4/month
- **GitHub Actions:** $0.21/month
- **Total:** ~$38-60/month

### Scenario 4: Heavy Production Use
- **Web Services:** $12-20/month (high user activity)
- **Training Jobs:** $100-200/month (50-100 jobs)
- **Scheduler:** $1/month
- **Storage & Registry:** $8-15/month
- **Total:** $121-236/month

**Note:** Current costs ($8.87/month) indicate Scenario 1 (minimal activity).

---

## Monitoring & Maintenance

### Weekly Tasks

✅ **Automated via GitHub Actions:**
- Artifact Registry cleanup (Sundays 2 AM UTC)
- Keeps last 10 tags per image
- Deletes older versions automatically

### Monthly Review

📊 **Manual monitoring recommended:**
1. Run cost tracking script:
   ```bash
   python scripts/track_daily_costs.py --days 30 --use-user-credentials
   ```
2. Review costs by service and category
3. Compare against expected scenarios above
4. Identify any cost anomalies or trends

### Cost Alerts (Recommended)

Consider setting up GCP budget alerts:
- **Warning at:** $30/month (Scenario 2 threshold)
- **Alert at:** $60/month (Scenario 3 threshold)
- **Critical at:** $100/month (unexpected high usage)

---

## Troubleshooting

### Scripts Show $0.00 Costs

**Possible causes:**
1. Billing export lag (data appears 24-48 hours after usage)
2. No activity during selected time period
3. Permission issues with BigQuery

**Solution:**
```bash
# Use longer time period
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Check with debug mode
python scripts/track_daily_costs.py --days 7 --use-user-credentials --debug
```

### Costs Higher Than Expected

**Check for:**
1. Increased training job frequency
2. Deployment churn (frequent CI/CD runs)
3. User activity spikes
4. Storage growth

**Investigate with:**
```bash
# Detailed cost breakdown
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# Check specific service
python scripts/analyze_idle_costs.py --days 7 --service mmm-app-web
```

### Permission Errors

**Error:** "Permission denied" or "bigquery.jobs.create"

**Solution:**
```bash
# Option 1: Use user credentials flag (recommended)
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Option 2: Ensure you're authenticated
gcloud auth application-default login

# Option 3: Grant BigQuery permissions
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/bigquery.user"
```

---

## Implementation Status

### ✅ Completed Optimizations

| Optimization | Status | Implementation | Savings |
|--------------|--------|----------------|---------|
| Scale-to-zero | ✅ Applied | Terraform (main.tf) | €15-20/month |
| CPU throttling | ✅ Applied | Terraform (main.tf) | €30-50/month |
| Scheduler optimization | ✅ Applied | Terraform (10-min intervals) | €40-45/month |
| Resource reduction | ✅ Applied | Terraform (1 vCPU, 2 GB) | €15-20/month |
| GCS lifecycle policies | ✅ Applied | Terraform (storage.tf) | €0.78/month |
| Artifact Registry cleanup | ✅ Automated | GitHub Actions (weekly) | €10-12/month |
| Cost tracking scripts | ✅ Enhanced | PR #169 + fixes | N/A (monitoring) |

**Total Savings:** €111-158/month (69-94% reduction)

### 🔄 Ongoing Improvements

| Item | Status | Notes |
|------|--------|-------|
| Deployment churn reduction | 🔄 In progress | Requires CI/CD workflow optimization |
| BigQuery billing export | ✅ Active | Enables accurate cost tracking |
| Cost alerting | 📋 Planned | GCP budget alerts recommended |

---

## Files & Documentation

### Primary Documents (Keep)
- **COST_STATUS.md** (this file) - **PRIMARY:** Single source of truth for current cost status and actual billing data
- **ARCHITECTURE.md** - System architecture and data flows
- **README.md** - Project overview and setup

### Related Cost Documentation (Reference)
- **COST_OPTIMIZATION.md** - Detailed optimization guide with comprehensive analysis (659 lines)
- **COST_OPTIMIZATION_SUMMARY.md** - Quick reference summary with idle cost focus
- **QUICK_COST_SUMMARY.md** - Visual cost breakdown before/after optimization
- **docs/COST_OPTIMIZATIONS_SUMMARY.md** - Technical implementation details
- **docs/IDLE_COST_ANALYSIS.md** - Technical deep-dive on idle cost causes
- **docs/IDLE_COST_EXECUTIVE_SUMMARY.md** - Executive summary of idle cost analysis

**When to use which document:**
- **Current costs & monitoring:** Use COST_STATUS.md (this file)
- **Historical optimization details:** Use COST_OPTIMIZATION.md or COST_OPTIMIZATION_SUMMARY.md
- **Idle cost analysis:** Use docs/IDLE_COST_ANALYSIS.md
- **Quick visual summary:** Use QUICK_COST_SUMMARY.md

### Cost Tracking Tools
- **scripts/track_daily_costs.py** - Daily cost tracking and reporting
- **scripts/analyze_idle_costs.py** - Idle cost analysis and recommendations
- **scripts/get_actual_costs.sh** - Shell script for quick cost queries
- **scripts/get_comprehensive_costs.sh** - Comprehensive cost estimation

### Deprecated Documents (Removed)
- ~~COST_SCRIPT_STATUS.md~~ - Superseded by this document
- ~~DEBUGGING_COST_SCRIPT.md~~ - Troubleshooting log (no longer needed)
- ~~TROUBLESHOOTING_COST_SCRIPT.md~~ - Troubleshooting log (no longer needed)
- ~~COST_SCRIPT_FIXED.md~~ - Implementation log (information integrated here)
- ~~COST_SCRIPT_HANGING_FIX.md~~ - Implementation log (information integrated here)
- ~~COST_SUMMARY_README.md~~ - Superseded by this document
- ~~Cost estimate.csv~~ - Old cost estimate (no longer accurate)

---

## Quick Reference Commands

```bash
# Track last 7 days of costs
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Analyze idle costs
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# Export to CSV
python scripts/track_daily_costs.py --days 30 --output costs.csv

# Debug mode (shows all billing data)
python scripts/track_daily_costs.py --days 7 --use-user-credentials --debug

# Check Terraform configuration
cd infra/terraform
terraform show | grep -A5 "cpu\|memory\|min_instances"

# Verify scheduler configuration
gcloud scheduler jobs describe robyn-queue-tick --location=europe-west1

# Check Cloud Run service configuration
gcloud run services describe mmm-app-web --region=europe-west1 --format=json
```

---

## Summary

✅ **Cost optimizations successfully applied** - Achieved 94% cost reduction  
✅ **Current costs: $8.87/month** - Well within target range  
✅ **All optimizations automated** - No manual intervention required  
✅ **Cost tracking enhanced** - Scripts now capture all cost categories  
✅ **Documentation consolidated** - Single source of truth established  

**Next Steps:**
1. ✅ Continue monthly cost monitoring
2. 📋 Set up GCP budget alerts (recommended)
3. 🔄 Optimize deployment churn in CI/CD (future enhancement)

---

**For questions or issues, refer to:**
- Cost tracking: `scripts/track_daily_costs.py --help`
- Optimization details: `COST_OPTIMIZATION.md`
- System architecture: `ARCHITECTURE.md`
- Development setup: `DEVELOPMENT.md`
