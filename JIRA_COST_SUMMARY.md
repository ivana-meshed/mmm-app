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
| **10 Jobs** | 10 production jobs | **~$12** | Light usage |
| **100 Jobs** | 100 production jobs | **~$30** | Moderate usage |
| **500 Jobs** | 500 production jobs | **~$110** | Heavy usage |
| **Benchmark** | 1 benchmark job | **~$0.20** | 12-minute benchmark run |

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
- **Per Job Cost:** ~$0.20/job (12-minute benchmark with 8 vCPU, 32GB RAM)
- **Per Hour Cost:** ~$0.98/hour (compute: CPU + memory)

---

## Detailed Scenario Breakdown

### Scenario 1: Idle (~$10/month)
- Minimal/no production training jobs
- Scheduler running for automation
- Development testing only
- **Current state** ✅

### Scenario 2: 10 Jobs/Month (~$12/month)
- **Training Jobs:** 10 × $0.20 = $2.00
- **Base Infrastructure:** $10
- **Total:** ~$12/month

### Scenario 3: 100 Jobs/Month (~$30/month)
- **Training Jobs:** 100 × $0.20 = $20.00
- **Base Infrastructure:** $10
- **Total:** ~$30/month

### Scenario 4: 500 Jobs/Month (~$110/month)
- **Training Jobs:** 500 × $0.20 = $100.00
- **Base Infrastructure:** $10
- **Total:** ~$110/month

### Benchmark Job Details
- **Duration:** ~12 minutes (optimized from 30 min)
- **Resources:** 8 vCPU, 32GB RAM
- **Cost per job:** ~$0.20
- **Performance:** 2.5× faster than original baseline

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
| Per Job | $2.92 (30 min) | $0.20 (12 min) | $2.72/job (93%) |
| Performance | 30 min/job | 12 min/job | 2.5× faster |

---

## Next Steps

1. Monitor actual costs after scheduler re-enablement
2. Verify training job costs align with $0.20/job estimate
3. Adjust projections based on actual usage patterns
4. Set up budget alerts at thresholds

**Last Updated:** February 18, 2026  
**Document Version:** 1.0
