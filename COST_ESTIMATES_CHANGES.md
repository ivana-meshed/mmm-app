# Cost Estimates PDF - Changes Summary

## Overview
This document summarizes the changes made to the Cost Estimates PDF (originally dated January 12, 2026) based on actual billing data and production measurements from February 2026.

---

## Key Changes

### 1. Training Job Performance (Table 1)

**ADDED: Production (Medium) - NEW PRIMARY RECOMMENDATION**
```
Production (Medium): 30 min, $0.50/job
Use Case: Typical production runs (most common)
```

**UPDATED: Benchmark**
- Duration: 12 min (was 12-18 min range)
- Cost: $0.20 (confirmed)
- Note: For testing/development only, NOT typical production

**UPDATED: Production (renamed to Production Large)**
- Duration: 67 min (was 80-120 min)
- Cost: $1.10 (was $1.33-$2.00)
- Performance: 30% faster than pre-optimization

**UPDATED: Large Production (renamed to Production Extra Large)**
- Duration: 160-240 min (unchanged)
- Cost: $2.67-$4.00 (unchanged)
- Note: For very large datasets only

**Rationale:**
- Original PDF used benchmark times for all planning
- Reality: Most production jobs are 30 min (medium), not 12 min (benchmark)
- Benchmark is for testing/dev, not production planning

---

### 2. Monthly Cost Scenarios (Table 2)

| Usage Level | Jobs | OLD Estimate | NEW Estimate | Difference |
|-------------|------|--------------|--------------|------------|
| Idle | 0-2 | $2/month | **$10/month** | +$8 (scheduler enabled) |
| Light | 10 | $4/month | **$15/month** | +$11 (production jobs) |
| Moderate | 50 | $12/month | **$35/month** | +$23 (production jobs) |
| Heavy | 100 | $22/month | **$60/month** | +$38 (production jobs) |
| Very Heavy | 500 | $102/month | **$260/month** | +$158 (production jobs) |

**Column Changes:**
- OLD: "Benchmark Cost" (assumed all jobs were benchmarks)
- NEW: "Production (Medium) Cost" (realistic production estimates)
- ADDED: "Production (Large) Cost" column for complex workloads
- ADDED: "Idle Cost" column showing baseline

**Rationale:**
- Original estimates assumed all training jobs were 12-min benchmarks ($0.20)
- Updated to use 30-min production medium jobs ($0.50) - more realistic
- Idle cost increased from $2 to $10 due to scheduler enablement ($0.70/month)

---

### 3. Fixed Monthly Costs

**OLD (~$2/month):**
```
- GCS storage: $0.50-$2.00
- Secret Manager: $0.36
- Cloud Scheduler: $0.30 (free tier)
- Artifact Registry: $0.50
```

**NEW (~$10/month):**
```
- Web Services: $5.32 (scheduler + minimal traffic)
- Cloud Scheduler: $0.70 (automated queue processing)
- GCS Storage: $0.14 (with lifecycle policies)
- Secret Manager: $0.36
- Artifact Registry: $0.50
- GitHub Actions: $0.21 (weekly cleanup)
- Base infrastructure: $3.63
Total: ~$10/month
```

**Rationale:**
- Original estimate was too low ($2/month)
- Actual measurements show $10/month with scheduler enabled
- Breakdown now includes all services based on actual billing data
- Scheduler cost is $0.70 (not free tier) for automated job processing

---

### 4. Variable Costs

**OLD:**
```
- Training jobs: $0.20 (benchmark) to $1.33-$2.00 (production)
- Web service: ~$0.002 per request
```

**NEW:**
```
- Benchmark job: $0.20 (12 min) - testing/dev only
- Production Medium job: $0.50 (30 min) - typical production use
- Production Large job: $1.10 (67 min) - complex models
- Per Hour: $0.98 (8 vCPU, 32GB RAM)
- Web service: ~$0.002 per request
```

**Rationale:**
- Added explicit job type categorization
- Clarified benchmark vs production distinction
- Added per-hour compute rate for transparency

---

### 5. New Sections Added

**Cost Optimization Status:**
- Baseline: $160/month
- Current: $10/month
- Savings: 94% reduction
- List of applied optimizations

**Cost Monitoring:**
- Scripts for tracking costs
- Commands for running cost analysis

**Additional Documentation:**
- Links to detailed documentation files
- JIRA-ready summaries
- Cost tracking scripts

---

## Summary of Major Updates

### Corrected Assumptions
1. ✅ **Job Duration:** Most production jobs are 30 min, not 12 min
2. ✅ **Fixed Costs:** $10/month (not $2) with scheduler enabled
3. ✅ **Job Types:** Clear distinction between benchmark, medium, and large
4. ✅ **Planning:** Use Production Medium ($0.50) for cost planning, not Benchmark ($0.20)

### Based On
- Actual billing data: February 14-18, 2026
- Production measurements with 8 vCPU, 32GB RAM
- Deployed configuration with all optimizations applied
- Scheduler enabled for automated job processing

### Impact on Planning
- **Light usage (10 jobs):** Budget $15, not $4 (+275%)
- **Moderate usage (50 jobs):** Budget $35, not $12 (+192%)
- **Heavy usage (100 jobs):** Budget $60, not $22 (+173%)
- **Very Heavy (500 jobs):** Budget $260, not $102 (+155%)

The increases reflect using realistic production job times (30 min) instead of benchmark times (12 min) for planning.

---

## Document Versions

- **Version 1.0** (January 12, 2026): Original estimates
- **Version 2.0** (February 18, 2026): Updated with actual production data

---

## Files in Repository

- `COST_ESTIMATES_UPDATED.md` - Full updated cost estimates document
- `COST_STATUS.md` - Detailed current cost status
- `JIRA_COST_SUMMARY.md` - JIRA-ready summary
- `scripts/track_daily_costs.py` - Cost tracking script
- `scripts/analyze_idle_costs.py` - Idle cost analysis script
