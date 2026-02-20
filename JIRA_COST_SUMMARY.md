# MMM Trainer - Monthly Cost Estimate

## Quick Reference

**Current Status:** ✅ Optimized & Running  
**Baseline Cost (Pre-optimization):** $160/month  
**Current Cost (Idle):** ~$10/month  
**Cost Reduction:** 94%  

�� **Detailed Documentation:** [COST_STATUS.md](./COST_STATUS.md)

---

## Monthly Cost Estimates by Usage

| Scenario | Training Jobs/Month | Monthly Cost | Notes |
|----------|-------------------|--------------|-------|
| **Idle** | 0-2 jobs | **~$10** | Minimal activity, scheduler enabled |
| **10 Jobs** | 10 production jobs | **~$15** | Light production usage (medium jobs) |
| **100 Jobs** | 100 production jobs | **~$60** | Moderate production usage |
| **500 Jobs** | 500 production jobs | **~$260** | Heavy production usage |
| **Benchmark** | 1 small test job | **~$0.20** | 12-minute optimized benchmark |

---

## Cost Breakdown by Component

### Fixed Monthly Costs (Idle)
- **Web Services:** $5.32/month (scheduler + minimal traffic)
- **Scheduler:** $0.70/month (automated queue processing every 10 min)
- **Storage & Registry:** $0.14/month (with lifecycle policies)
- **GitHub Actions:** $0.21/month (weekly cleanup automation)
- **Base infrastructure:** $3.63/month (networking, secrets, etc.)
- **Total Fixed:** ~$10/month

### Variable Costs (Training Jobs)
- **Benchmark Job (small, optimized):** ~$0.20/job (12 minutes)
- **Production Job (medium, typical):** ~$0.50/job (30 minutes)
- **Production Job (large):** ~$1.10/job (67 minutes)
- **Per Hour Cost:** ~$0.98/hour (8 vCPU, 32GB RAM compute)

---

## Detailed Scenario Breakdown

### Scenario 1: Idle (~$10/month)
- Minimal/no production training jobs
- Scheduler running for automation
- Development testing only
- **Current state** ✅

### Scenario 2: 10 Production Jobs/Month (~$15/month)
- **Training Jobs:** 10 × $0.50 = $5.00 (medium jobs)
- **Base Infrastructure:** $10
- **Total:** ~$15/month

### Scenario 3: 100 Production Jobs/Month (~$60/month)
- **Training Jobs:** 100 × $0.50 = $50.00 (medium jobs)
- **Base Infrastructure:** $10
- **Total:** ~$60/month

### Scenario 4: 500 Production Jobs/Month (~$260/month)
- **Training Jobs:** 500 × $0.50 = $250.00 (medium jobs)
- **Base Infrastructure:** $10
- **Total:** ~$260/month

### Job Type Details

**Benchmark Job (Small, Optimized):**
- **Duration:** 12 minutes (10-15 min range)
- **Use case:** Testing, development, quick validation
- **Cost:** ~$0.20/job

**Production Job (Medium, Typical):**
- **Duration:** 30 minutes (25-35 min range)
- **Use case:** Standard MMM training, typical datasets
- **Cost:** ~$0.50/job
- **Performance:** 40% faster than pre-optimization (45-60 min baseline)

**Production Job (Large):**
- **Duration:** 67 minutes (60-75 min range)
- **Use case:** Complex models, large datasets, high iterations
- **Cost:** ~$1.10/job
- **Performance:** 30% faster than pre-optimization (90-120 min baseline)

**All jobs use:** 8 vCPU, 32GB RAM at $0.98/hour compute rate

---

## Key Optimizations Applied

1. ✅ **Scale-to-zero** (min_instances=0) - No idle compute costs
2. ✅ **CPU throttling** - Efficient resource usage
3. ✅ **Scheduler automation** - 10-minute intervals
4. ✅ **Resource optimization** - Right-sized compute (8 vCPU vs previous 4 vCPU = faster + cheaper)
5. ✅ **Storage lifecycle policies** - Automatic cost reduction over time
6. ✅ **Registry cleanup** - Weekly automated cleanup

---

## Cost Monitoring

**Scripts Available:**
```bash
# Track daily costs
python scripts/track_daily_costs.py --days 30 --use-user-credentials

# Analyze idle costs
python scripts/analyze_idle_costs.py --days 30 --use-user-credentials
```

**Alerting Thresholds:**
- ⚠️ Warning: $30/month
- 🚨 Alert: $60/month
- 🔥 Critical: $100/month

---

## Cost Comparison

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| Idle Cost | $160/month | $10/month | $150/month (94%) |
| Benchmark Job | N/A (not tracked) | $0.20 (12 min) | Optimized test |
| Production Job (medium) | $2.92 (30 min) | $0.50 (30 min) | $2.42/job (83%) |
| Performance | 30 min/job (baseline) | 12-67 min (optimized) | 40-50% faster |

---

## Next Steps

1. Monitor actual costs after scheduler re-enablement
2. Verify training job costs align with $0.20/job estimate
3. Adjust projections based on actual usage patterns
4. Set up budget alerts at thresholds

**Last Updated:** February 18, 2026  
**Document Version:** 1.0
