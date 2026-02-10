# Cost Optimization Summary - MMM Trainer

## Quick Reference

**Current Monthly Cost**: $137.31  
**Optimized Cost**: ~$25-45/month  
**Potential Annual Savings**: ~$1,100-1,344

---

## 🎯 Quick Start

### Track Daily Costs
```bash
python scripts/track_daily_costs.py --days 7 --use-user-credentials
```

### Analyze Idle Costs
```bash
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
```

### Pause Scheduler (for testing)
```bash
cd infra/terraform
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=false"
```

---

## 📊 Cost Breakdown (Current State)

| Service | Daily Cost | Monthly Cost | % of Total |
|---------|-----------|--------------|------------|
| mmm-app-web | $2.44 | $73.00 | 53% |
| mmm-app-dev-web | $2.07 | $62.00 | 45% |
| mmm-app-training | $0.03 | $1.00 | 1% |
| mmm-app-dev-training | $0.03 | $1.00 | 1% |
| **Total** | **$4.58** | **$137.31** | **100%** |

### Cost by Category

| Category | Cost | % of Total |
|----------|------|------------|
| Compute CPU | $1.67/day | 68-81% |
| Compute Memory | $0.74/day | 15-20% |
| Registry | $0.06/day | 1-3% |
| Storage | $0.12/day | 3-5% |

---

## 🔍 Root Causes

### 1. CPU Throttling Disabled (70-80% of idle costs)

**Location**: `infra/terraform/main.tf` line 324
```terraform
"run.googleapis.com/cpu-throttling" = "false"  # ❌ PROBLEM
```

**Impact**: 
- CPU remains allocated 24/7 even when container is idle
- Cost: 1 vCPU × $0.024/vCPU-hour × 24 hours = $0.576/day
- Annual cost: ~$840-960/year unnecessary

**Solution**:
```terraform
"run.googleapis.com/cpu-throttling" = "true"  # ✅ FIX
```

**Expected Savings**: $50-70/month (~$600-840/year)

---

### 2. Scheduler Every 10 Minutes (20-30% of idle costs)

**Location**: `infra/terraform/main.tf` line 597
```terraform
schedule = "*/10 * * * *"  # Runs 144 times/day
```

**Impact**:
- 144 wake-ups per day keeps instances warm nearly 24/7
- Instance stays warm ~15 minutes after each wake-up
- Prevents true scale-to-zero

**Solutions**:

**Option A**: Increase interval (recommended for testing)
```terraform
schedule = "*/30 * * * *"  # 48 times/day instead of 144
```

**Option B**: Pause temporarily (for cost monitoring)
```terraform
scheduler_enabled = false  # In prod.tfvars or dev.tfvars
```

**Expected Savings**: $20-30/month (~$240-360/year)

---

## 🚀 Optimization Plan

### Phase 1: Measure Baseline (Day 1-2)

```bash
# Run daily for 2 days to establish baseline
python scripts/track_daily_costs.py --days 2 --use-user-credentials
```

**Expected Baseline**: ~$4.58/day

---

### Phase 2: Test Scheduler Pause (Day 3-4) [Optional]

```bash
# Pause scheduler
cd infra/terraform
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=false"

# Monitor for 2 days
python scripts/track_daily_costs.py --days 2 --use-user-credentials
```

**Expected Cost**: ~$0.40-0.60/day (90% reduction)

```bash
# Resume scheduler
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=true"
```

---

### Phase 3: Enable CPU Throttling (Day 5-9)

**Test in Dev First**:
```bash
# Edit infra/terraform/main.tf line 324
"run.googleapis.com/cpu-throttling" = "true"

cd infra/terraform
terraform apply -var-file=envs/dev.tfvars
```

**Monitor Dev**:
```bash
# Check dev costs for 3-5 days
python scripts/track_daily_costs.py --days 5 --use-user-credentials
```

**Expected Dev Cost**: ~$0.60-0.80/day (70% reduction)

**Apply to Prod**:
```bash
cd infra/terraform
terraform apply -var-file=envs/prod.tfvars
```

---

### Phase 4: Optimize Scheduler (Day 10-14) [Optional]

```bash
# Edit infra/terraform/main.tf line 597
schedule = "*/30 * * * *"  # Change from */10 to */30

cd infra/terraform
terraform apply -var-file=envs/prod.tfvars
```

**Monitor**:
```bash
python scripts/track_daily_costs.py --days 5 --use-user-credentials
```

**Expected Cost**: ~$0.80-1.20/day (75-80% reduction total)

---

## 📈 Expected Results

| Optimization | Daily Cost | Monthly Cost | Savings |
|--------------|-----------|--------------|---------|
| **Baseline** (current) | $4.58 | $137.31 | - |
| After CPU throttling | $1.50-2.50 | $45-76 | $60-92/mo |
| After both (CPU + scheduler) | $0.80-1.50 | $25-45 | $92-112/mo |

**Annual Savings**: ~$1,100-1,344

---

## 🛠️ Troubleshooting

### Permission Errors

If you see "Permission denied" or "bigquery.jobs.create" errors:

```bash
# Option 1: Use user credentials flag
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Option 2: Unset service account if set
unset GOOGLE_APPLICATION_CREDENTIALS
python scripts/track_daily_costs.py --days 7

# Option 3: Grant BigQuery permissions
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/bigquery.user"
```

See `scripts/PERMISSION_FIX.md` for detailed troubleshooting.

---

### IAM Propagation Delays

**After granting permissions, wait 2-5 minutes** before trying again. IAM permissions take time to propagate.

```bash
# Verify permissions are active
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:YOUR_EMAIL"
```

---

### No Costs Showing

If you only see registry/storage costs:

```bash
# Run with debug flag
python scripts/track_daily_costs.py --days 7 --use-user-credentials --debug
```

This will show what services and SKUs are in the billing data.

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| `scripts/COST_TRACKING_README.md` | Complete guide for cost tracking |
| `scripts/COST_TRACKING_EXAMPLE.md` | Example outputs |
| `scripts/PERMISSION_FIX.md` | Permission troubleshooting |
| `docs/IDLE_COST_ANALYSIS.md` | Technical deep-dive |
| `docs/IDLE_COST_EXECUTIVE_SUMMARY.md` | High-level overview |
| `docs/SCHEDULER_PAUSE_GUIDE.md` | Scheduler pause guide |
| `docs/SCHEDULER_PAUSE_QUICKSTART.md` | Quick-start guide |

---

## ✅ Safety Checklist

Before applying optimizations:

- [ ] Baseline costs measured (2+ days)
- [ ] Scripts working without errors
- [ ] Permissions configured correctly
- [ ] Changes tested in dev environment first
- [ ] Monitoring plan in place
- [ ] Rollback plan documented

---

## 🎯 Success Metrics

Track these metrics daily/weekly:

1. **Total daily cost**: Target $0.80-1.50/day
2. **Instance hours active**: Target 8-12 hours/day
3. **CPU cost percentage**: Target <30% of total
4. **User experience**: No degradation in performance

```bash
# Daily tracking
python scripts/track_daily_costs.py --days 1 --use-user-credentials

# Weekly analysis
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
```

---

## 🤝 Support

**Questions?** Check the comprehensive documentation in:
- `scripts/` directory for tool documentation
- `docs/` directory for analysis and guides

**Issues?** Use the debug flag to diagnose:
```bash
python scripts/track_daily_costs.py --days 7 --use-user-credentials --debug
```

---

## 🎉 Quick Wins

**Immediate (no infra changes)**:
- ✅ Start tracking costs daily
- ✅ Identify cost trends
- ✅ Export to CSV for analysis

**Low Risk (1 hour + monitoring)**:
- ✅ Enable CPU throttling in dev
- ✅ Monitor for 3-5 days
- ✅ Apply to prod

**Expected ROI**: ~$1,100-1,300/year savings with zero user impact!
