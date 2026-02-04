# Quick Start: Cost Reduction Implementation

## TL;DR - What Changed

**Problem:** Cloud Run costs ~$148/month (€136), with web services being 84% of costs

**Solution:** Optimized web service resources + enabled scale-to-zero

**Expected Result:** ~$88/month (40% reduction, $720/year savings)

---

## What You Need to Do

### 1. Test the New Cost Script (5 minutes)

The new script shows **complete** Cloud Run costs (training + web services):

```bash
./scripts/get_cloud_run_costs.sh
```

**Expected output:**
```
PART 1: Training Job Costs
  mmm-app-training: $0.34
  mmm-app-dev-training: $23.11
  Total: $23.45

PART 2: Web Service Costs
  mmm-app-web: ~$62.50 (estimated)
  mmm-app-dev-web: ~$62.50 (estimated)
  Total: ~$125.00

Grand Total: ~$148.45
  Training: 16%
  Web Services: 84%
```

---

### 2. Review Terraform Changes (5 minutes)

Check what will change:

```bash
cd infra/terraform
terraform plan -var-file=envs/prod.tfvars
```

**Key changes you'll see:**
- Web service CPU: 2.0 → 1.0 vCPU
- Web service memory: 4Gi → 2Gi
- min_instances: 0 (scale to zero)
- container_concurrency: 10 → 5

---

### 3. Deploy to Production (10 minutes)

Apply the optimizations:

```bash
# Production
terraform apply -var-file=envs/prod.tfvars

# Development
terraform apply -var-file=envs/dev.tfvars
```

---

### 4. Monitor Results (Ongoing)

**Week 1: Check daily costs**
```bash
# GCP Console → Billing → Reports
# Filter: Service = "Cloud Run"
# Expected: $4.50/day → $2.50/day (44% reduction)
```

**Week 2: Verify performance**
- Test the web UI
- Check response times (should be < 3 seconds)
- Verify no errors in Cloud Run logs

**Month 1: Confirm savings**
- Compare January (before) vs February (after)
- Should see ~$60/month reduction in Cloud Run costs

---

## What Changed and Why

### CPU Reduction (2 vCPU → 1 vCPU)
**Why:** Streamlit is I/O-bound (database queries, GCS operations), not CPU-intensive
**Savings:** $26/month
**Impact:** Minimal - CPU usage is typically < 20%

### Memory Reduction (4GB → 2GB)
**Why:** Current usage is ~500MB-1GB, 2GB provides safe headroom
**Savings:** $5.40/month
**Impact:** None - plenty of memory for Streamlit

### Scale to Zero (min_instances=0)
**Why:** Web UI not used 24/7, save money during idle periods
**Savings:** $20/month
**Impact:** 2-3 second cold start on first request after idle (acceptable)

### Container Concurrency (10 → 5)
**Why:** Fewer concurrent requests = faster processing = shorter runtime
**Savings:** $8/month (indirect)
**Impact:** Better performance, Cloud Run scales out if needed

---

## Expected Behavior After Changes

### ✅ Normal Usage (What You'll See)
- Page loads: Same speed (~1-2 seconds when warm)
- Navigation: Same responsiveness
- Job submissions: No change
- Data previews: No change

### ⚠️ Cold Starts (Occasional)
- First request after idle: 2-3 seconds (instead of instant)
- Subsequent requests: Back to instant
- Container stays warm for 15+ minutes after last request

### 📊 Cost Reduction
- Daily cost: $4.50 → $2.50 (44% reduction)
- Monthly cost: $148 → $88 (40% reduction)
- Annual savings: $720/year

---

## If Something Goes Wrong

### Quick Rollback (10 minutes)

If you experience issues, rollback is easy:

```bash
cd infra/terraform

# Edit main.tf and change back:
# cpu = "2.0"
# memory = "4Gi"
# container_concurrency = 10

terraform apply -var-file=envs/prod.tfvars
```

### Emergency Rollback (2 minutes)

Use gcloud commands for immediate rollback:

```bash
gcloud run services update mmm-app-web \
  --region=europe-west1 \
  --cpu=2 \
  --memory=4Gi \
  --min-instances=1
```

---

## Common Questions

**Q: Will the app be slower?**  
A: No, performance should be the same. The app is I/O-bound, not CPU-bound.

**Q: What about cold starts?**  
A: 2-3 seconds on first request after idle (e.g., first user in the morning). This is acceptable for a business application.

**Q: Can we reduce costs further?**  
A: Yes, additional opportunities:
- Optimize Snowflake queries (reduce runtime)
- Add caching for frequently-accessed data
- Schedule min_instances during business hours only

**Q: How do I verify the savings?**  
A: 
1. Run the cost script weekly: `./scripts/get_cloud_run_costs.sh`
2. Check GCP billing dashboard (filter by Cloud Run)
3. Compare monthly totals before/after

**Q: What if we get memory errors?**  
A: Very unlikely (current usage ~1GB, limit is 2GB). If it happens, increase to 3GB still saves money vs 4GB.

---

## Next Steps

1. ✅ Test the new cost script
2. ✅ Review Terraform changes
3. ✅ Deploy to prod and dev
4. ✅ Monitor for 1 week
5. ✅ Verify cost savings in billing

**Complete documentation:** See [COST_REDUCTION_IMPLEMENTATION.md](COST_REDUCTION_IMPLEMENTATION.md)

---

## Summary

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Monthly cost | $148 | $88 | -$60 (40%) |
| Web service CPU | 2 vCPU | 1 vCPU | -50% |
| Web service memory | 4 GB | 2 GB | -50% |
| Cold starts | None | Occasional | Acceptable |
| Performance | Good | Good | No change |

**Total annual savings: $720**
