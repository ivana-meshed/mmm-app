# Warmup Job Analysis and Removal Guide

## Overview

The **warmup job** (`mmm-warmup-job`) is a Cloud Scheduler job that pings Cloud Run services every 5 minutes to keep them "warm" and avoid cold starts.

## Current Configuration

**From collected data (2026-02-02):**
```
Job Name:   mmm-warmup-job
Schedule:   */5 * * * * (every 5 minutes)
State:      ENABLED
Invocations: ~8,640/month (12/hour × 24 hours × 30 days)
```

## Cost Analysis

### Direct Costs

**Cloud Scheduler:**
- First 3 jobs: FREE (currently have 3 total jobs, so warmup is within free tier)
- If you have >3 jobs: $0.10/month per additional job

**Cloud Run Requests:**
- Each warmup ping = 1 Cloud Run request
- Request cost: $0.0000004 per request
- Monthly requests: 8,640
- **Cost: $0.003/month** (negligible)

### Indirect Costs

**Container Instance Time:**
- Each request keeps container alive for ~15 seconds (minimum billing unit)
- 8,640 requests × 15 seconds = 129,600 seconds = 36 hours/month
- For 1 vCPU, 2GB service:
  - CPU: 36 hours × $0.024/vCPU-hour = $0.86/month
  - Memory: 36 hours × 2GB × $0.0025/GB-hour = $0.18/month
  - **Total: ~$1.04/month per service**

**For both services (mmm-app-web + mmm-app-dev-web):**
- **Total monthly cost: ~$2.08/month**
- **Annual cost: ~$25/year**

## Impact Analysis

### With Warmup Job (Current)

**Pros:**
- ✅ No cold starts - instant response
- ✅ Consistent user experience
- ✅ <1 second response time always
- ✅ Good for production applications

**Cons:**
- ❌ Costs ~$2/month ($25/year)
- ❌ Prevents full scale-to-zero
- ❌ Continuous resource usage even when idle
- ❌ Container always warm = always billing

### Without Warmup Job (Optimized)

**Pros:**
- ✅ Saves ~$2/month ($25/year)
- ✅ True scale-to-zero when idle
- ✅ Only pay for actual usage
- ✅ More efficient resource utilization

**Cons:**
- ❌ 2-3 second cold start on first request after idle
- ❌ User may notice delay if application idle >15 minutes
- ❌ Less predictable response times
- ❌ Not ideal for real-time applications

## Decision Framework

### Remove Warmup Job If:

1. **Usage Pattern:**
   - Application used intermittently (not 24/7)
   - Long idle periods (nights, weekends)
   - Batch/scheduled access patterns

2. **User Expectations:**
   - 2-3s cold start is acceptable
   - Not a real-time application
   - Analytical/reporting tool (not transactional)

3. **Priority:**
   - Cost optimization is priority
   - Willing to trade slight delay for savings

### Keep Warmup Job If:

1. **Usage Pattern:**
   - Application accessed frequently (24/7)
   - Continuous user activity
   - Unpredictable access times

2. **User Expectations:**
   - Need instant response (<1s)
   - Real-time application
   - High performance requirements

3. **Priority:**
   - User experience is priority
   - Cost is not primary concern

## Implementation

### Option 1: Remove via Script (Recommended)

```bash
./scripts/remove_warmup_job.sh
```

The script will:
- Check if warmup job exists
- Show job details and impact
- Ask for confirmation
- Remove the job
- Provide rollback instructions

### Option 2: Remove via gcloud Command

```bash
gcloud scheduler jobs delete mmm-warmup-job \
  --location=europe-west1 \
  --quiet
```

### Option 3: Disable (Keep for Later)

```bash
gcloud scheduler jobs pause mmm-warmup-job \
  --location=europe-west1
```

To re-enable later:
```bash
gcloud scheduler jobs resume mmm-warmup-job \
  --location=europe-west1
```

## Rollback: Re-creating Warmup Job

If you remove the job and want it back:

```bash
gcloud scheduler jobs create http mmm-warmup-job \
  --location=europe-west1 \
  --schedule='*/5 * * * *' \
  --uri='https://mmm-app-web-wuepn6nq5a-ew.a.run.app/health' \
  --http-method=GET \
  --oidc-service-account-email=robyn-queue-scheduler@datawarehouse-422511.iam.gserviceaccount.com
```

Note: Adjust URI and service account email as needed.

## Monitoring After Removal

### Week 1: Performance Check
- Monitor response times
- Check for user complaints
- Measure cold start frequency

### Week 2-4: Cost Verification
- Check billing dashboard
- Verify cost reduction (~$2/month)
- Compare before/after metrics

### Metrics to Track
```bash
# Check Cloud Run metrics
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/request_latencies"' \
  --format=json

# Check request count
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/request_count"' \
  --format=json
```

## Additional Cost Optimization Opportunities

Beyond the warmup job, consider these optimizations:

### 1. Web Service Resources (Already Implemented)
- **Current:** 1 vCPU, 2GB RAM
- **Savings:** ~$60/month if reduced from 2 vCPU, 4GB
- **Status:** ✅ Already optimized in Terraform

### 2. Artifact Registry Cleanup (Available)
- **Current:** ~122 GB, $12/month
- **Target:** <10 GB, <$1/month
- **Savings:** ~$11/month
- **Action:** Run `./scripts/cleanup_artifact_registry.sh`

### 3. GCS Lifecycle Policies (Available)
- **Current:** ~$0.58/month in Standard storage
- **Target:** Move old data to Nearline/Coldline
- **Savings:** ~$0.20/month
- **Action:** Apply via Terraform or `gsutil lifecycle`

### 4. Training Job Optimization (Already Optimized)
- **Current:** 8 vCPU, 32GB RAM
- **Status:** ✅ Well optimized for R/Robyn workload
- **Recommendation:** No changes needed

### 5. Cloud Logging (Future)
- **Current:** ~$1-2/month
- **Potential:** Exclude verbose logs, adjust retention
- **Savings:** ~$0.50-1/month
- **Priority:** Low (small impact)

## Summary

### Quick Decision Matrix

| Scenario | Recommendation | Savings | Trade-off |
|----------|----------------|---------|-----------|
| Dev environment | **Remove** | $1/month | 2-3s cold start (acceptable) |
| Low-traffic prod | **Remove** | $1/month | 2-3s cold start (acceptable) |
| High-traffic prod | **Keep** | $0 | Always fast (<1s response) |
| 24/7 usage | **Keep** | $0 | No cold starts |
| Batch/scheduled | **Remove** | $1/month | Cold starts don't matter |

### Total Potential Savings

If you remove warmup job from both dev and prod:
- **Monthly:** $2.08
- **Annual:** $25

Combined with other optimizations:
- Warmup removal: $25/year
- Web resources (done): $720/year
- Artifact cleanup: $132/year
- GCS lifecycle: $3/year
- **Total: ~$880/year in savings**

## Recommendation for This Project

Based on the analysis:

**For mmm-app-dev-web:**
- ✅ **REMOVE warmup job**
- Savings: $1/month ($12/year)
- Impact: Minimal (dev environment)

**For mmm-app-web (production):**
- ⚠️ **Consider removing** if:
  - Usage is primarily during business hours
  - Users can tolerate 2-3s cold start
  - Cost is a priority
- 💡 **Keep** if:
  - Need instant response 24/7
  - Real-time user expectations
  - Cold starts unacceptable

## Next Steps

1. **Test in dev first:**
   ```bash
   # Remove from dev environment
   gcloud scheduler jobs delete mmm-warmup-job --location=europe-west1
   ```

2. **Monitor for 1 week:**
   - Check cold start frequency
   - Measure user impact
   - Verify cost reduction

3. **Decide on production:**
   - If dev test successful → remove from prod
   - If issues arise → keep in prod

4. **Update cost tracking:**
   ```bash
   # Run cost script to see updated costs
   ./scripts/get_cloud_run_costs.sh
   ```

## Conclusion

The warmup job costs **~$2/month** ($25/year) and prevents cold starts. For a dev environment or low-traffic application, removing it is recommended. For production with 24/7 usage expectations, evaluate based on your specific requirements.

**Action:** Run `./scripts/remove_warmup_job.sh` to remove the job with guided prompts and impact explanation.
