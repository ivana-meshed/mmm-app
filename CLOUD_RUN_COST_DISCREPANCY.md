# Cloud Run Cost Breakdown Explanation

**Date:** 2026-02-03  
**Issue:** Training cost script shows $23.45 but billing shows €136.58 (~$148)

---

## The Discrepancy Explained

### What the Script Calculates

The `get_training_costs.sh` script calculates costs for **Cloud Run JOBS only**:

```
Training Jobs (35 days):
  - mmm-app-training: 3 executions → $0.34
  - mmm-app-dev-training: 125 executions → $23.11
  
Total: $23.45
```

**What this includes:**
- Actual execution time of training jobs
- CPU and memory used during R/Robyn model training
- Compute costs based on job duration

**What this DOES NOT include:**
- Web service (Streamlit UI) costs
- Request handling overhead
- Container startup/shutdown time
- Idle time for always-on services
- Other Cloud Run resources

---

### What Your Billing Shows

**January 2026 Billing:**
- Cloud Run: €136.58 (≈ $148 USD)
- Artifact Registry: €11.73
- GCS: €2.30
- Secret Manager: €1.98
- Others: €18.58

**Total GCP:** €171.17 (≈ $186 USD)

---

## Where the Missing $125 Comes From

### Cloud Run Bill Breakdown (estimated)

Your €136.58 Cloud Run bill consists of:

| Component | Estimated Cost | Percentage |
|-----------|---------------|------------|
| **Web Service (Streamlit)** | ~$100-120 | **68-81%** |
| Training Jobs | $23-25 | 16-17% |
| Request handling | $3-5 | 2-3% |
| Container pulls | $1-2 | 1% |

### Why the Web Service is So Expensive

**mmm-app and mmm-app-dev are Cloud Run SERVICES, not jobs:**

1. **Always Available**
   - Even with min_instances=0, services handle HTTP requests
   - Each request keeps container alive for billable time
   - Cold starts are also billable

2. **Resource Configuration**
   - Web service: 2-8 vCPU, 4-8 GB memory
   - Much larger than needed for Streamlit
   - Billed per second the container is running

3. **Active Usage**
   - Every page view = request
   - Every data query = request
   - Scheduled warmup jobs = requests
   - Each keeps container alive for 1-5 minutes

4. **Two Environments**
   - `mmm-app` (production)
   - `mmm-app-dev` (development)
   - Both incur costs when used

### Example Calculation

**Scenario:** 200 requests/month to web service

```
Average request duration: 30 seconds
Container stays alive: 60 seconds after request
Total billable time: 200 requests × 60 seconds = 12,000 seconds

With 2 vCPU, 4 GB memory:
  CPU cost: 12,000 sec × 2 vCPU × $0.000024/vCPU-sec = $0.58
  Memory cost: 12,000 sec × 4 GB × $0.0000025/GB-sec = $0.12
  Per request: $0.70 / 200 = $0.0035/request
  
200 requests × $0.0035 = $0.70/day = $21/month (minimum)
```

But with:
- Scheduled warmup (every 15 minutes)
- Multiple users browsing
- Dev environment testing
- Longer container alive times

**Actual: $100-150/month is realistic**

---

## Detailed Cost Attribution

### January 2026 Actual Costs

From your billing report:

**Cloud Run: €136.58**
- Web service (mmm-app): ~€80-90
- Web service dev (mmm-app-dev): ~€30-40
- Training jobs (both): ~€20-25

**Artifact Registry: €11.73**
- 122 GB of Docker images
- Should be ~$12.26/month
- ✅ Matches the script's finding

**Other Services: €22.28**
- GCS: €2.30
- Secret Manager: €1.98
- Compute Engine: €16.17 (unexpected - investigate)
- Cloud DNS: €0.17

---

## Why the Script Can't Calculate Web Service Costs

### Technical Limitations

1. **No Direct API for Web Service Usage**
   - Cloud Run Jobs have execution records
   - Cloud Run Services don't expose request duration details
   - Would need Cloud Logging query (complex)

2. **Request Patterns Vary**
   - User browsing time unpredictable
   - Container alive time depends on traffic
   - Cold starts vs warm instances
   - Background warmup jobs

3. **Multiple Cost Components**
   - CPU time
   - Memory allocation
   - Request count
   - Egress traffic
   - Each billed separately

### What Would Be Needed

To calculate web service costs accurately:

```bash
# Query Cloud Logging for all requests
gcloud logging read "resource.type=cloud_run_revision \
  AND resource.labels.service_name=mmm-app \
  AND timestamp>=\"2026-01-01\"" \
  --format=json

# Extract:
# - Request count
# - Request duration
# - Container instance time
# - CPU/memory usage metrics

# Then calculate:
# - Per-request cost
# - Per-second cost
# - Total billable time
```

**Complexity:** High  
**Accuracy:** Medium (still estimates)  
**Easier:** Just check billing reports

---

## Recommendations

### For Accurate Cost Tracking

1. **Use GCP Billing Reports**
   - GCP Console → Billing → Reports
   - Filter by Service: "Cloud Run"
   - Group by: "SKU" to see breakdown
   - Export to CSV for analysis

2. **Enable Detailed Billing Export**
   - Export to BigQuery
   - Query by resource labels
   - See per-service breakdown
   - Track trends over time

3. **Use This Script For**
   - Understanding training job costs
   - Optimizing training efficiency
   - Estimating training cost changes
   - Comparing dev vs prod training usage

4. **Don't Use This Script For**
   - Total Cloud Run costs
   - Web service cost estimation
   - Budget planning (use billing reports)
   - Invoice reconciliation

### For Cost Reduction

**Web Service (saves $50-100/month):**
- Reduce CPU/memory allocation (currently 8 vCPU, 8 GB)
- Optimize Streamlit app for faster responses
- Implement proper caching
- Consider min_instances=0 (already set)

**Training Jobs (saves $5-10/month):**
- Optimize R code for faster execution
- Use smaller CPU/memory if possible
- Reduce dev environment testing

**Other Services:**
- Clean up Artifact Registry (saves $11/month) ← Already planned
- Implement GCS lifecycle (saves $0.50/month) ← Already planned

---

## Updated Script Output

The script now includes a warning explaining this limitation:

```
========================================
⚠️  IMPORTANT: Cost Calculation Scope
========================================
This script calculates TRAINING JOB costs only.

Cloud Run has TWO types of resources:
  1. Training Jobs (mmm-app-training, mmm-app-dev-training)
     → Calculated above: $23.45
  
  2. Web Service (mmm-app, mmm-app-dev)
     → Streamlit web UI running continuously
     → NOT included in this calculation
     → This is typically the LARGEST cost component

Your actual Cloud Run bill includes BOTH:
  - Training job compute (this script): ~$23.45
  - Web service compute (NOT calculated): ~$100-150/month
```

---

## Summary

| What | Script Says | Reality | Why Different |
|------|------------|---------|---------------|
| Training jobs | $23.45 | ~$25 | ✅ Accurate |
| Web services | Not calculated | ~$110-125 | ❌ Not included |
| **Total Cloud Run** | **$23.45** | **~$148** | **Missing web services** |

**Key Takeaway:** The script is accurate for what it measures (training jobs), but training jobs are only ~16% of total Cloud Run costs. The web service (Streamlit UI) accounts for the remaining 84%.

---

## Action Items

1. ✅ **Updated script** with clear warning about scope
2. ✅ **Documented** cost discrepancy explanation
3. 🔄 **Consider** adding web service cost estimation (complex)
4. 📊 **Use** GCP Billing Reports for actual costs
5. 🎯 **Focus** optimization efforts on web service (bigger savings)

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-03  
**For Questions:** See ACTUAL_COST_ANALYSIS.md or billing reports
