# MMM Trainer Cost Summary for JIRA

---

## 📋 Copy-Paste for JIRA Ticket

```
h2. MMM Trainer - Monthly Cost Estimates

*Current Status:* ✅ Optimized (94% cost reduction from baseline)
*Detailed Documentation:* [COST_STATUS.md|https://github.com/ivana-meshed/mmm-app/blob/main/COST_STATUS.md]

h3. Monthly Cost by Usage Level

|| Scenario || Training Jobs/Month || Monthly Cost || Notes ||
| Idle | 0-2 jobs | ~$10 | Minimal activity, scheduler enabled |
| Light Usage | 10 production jobs | ~$15 | Medium jobs (~30 min each) |
| Moderate Usage | 100 production jobs | ~$60 | Medium jobs |
| Heavy Usage | 500 production jobs | ~$260 | Medium jobs |
| Benchmark Job | 1 small test job | ~$0.20 | 12-minute optimized test |

h3. Cost Breakdown

*Fixed Monthly Costs (Idle):* ~$10/month
* Web Services: $5.32 (scheduler + minimal traffic)
* Scheduler: $0.70 (automated every 10 min)
* Storage & Registry: $0.14 (with lifecycle policies)
* GitHub Actions: $0.21 (weekly cleanup)
* Base Infrastructure: $3.63 (networking, secrets)

*Variable Costs:*
* Benchmark Job (small): ~$0.20/job (12 minutes, testing/dev)
* Production Job (medium): ~$0.50/job (30 minutes, typical use)
* Production Job (large): ~$1.10/job (67 minutes, complex models)
* Per Hour: ~$0.98 (8 vCPU, 32GB RAM compute)

h3. Key Metrics

* *Baseline (before optimization):* $160/month
* *Current (idle):* $10/month
* *Savings:* $150/month (94% reduction)
* *Benchmark job:* $0.20 (12 min, testing)
* *Production job (typical):* $0.50 (30 min, medium)
* *Job performance:* 40-50% faster than baseline

h3. Optimizations Applied

✓ Scale-to-zero (no idle compute costs)
✓ CPU throttling (efficient resource usage)
✓ Scheduler automation (10-min intervals)
✓ Right-sized compute (8 vCPU optimized)
✓ Storage lifecycle policies
✓ Automated registry cleanup

h3. Cost Monitoring & Alerts

*Thresholds:*
* ⚠ Warning: $30/month
* 🚨 Alert: $60/month
* 🔥 Critical: $100/month

*Scripts:*
{code}
python scripts/track_daily_costs.py --days 30 --use-user-credentials
python scripts/analyze_idle_costs.py --days 30 --use-user-credentials
{code}
```

---

## 📝 Plain Text Version (for basic JIRA)

```
MMM TRAINER - MONTHLY COST ESTIMATES

Current Status: Optimized (94% cost reduction from baseline)
Detailed Documentation: https://github.com/ivana-meshed/mmm-app/blob/main/COST_STATUS.md

MONTHLY COST BY USAGE LEVEL:
- Idle (0-2 jobs): ~$10/month - Minimal activity, scheduler enabled
- Light (10 production jobs): ~$15/month - Medium jobs (~30 min each)
- Moderate (100 production jobs): ~$60/month - Medium jobs
- Heavy (500 production jobs): ~$260/month - Medium jobs
- Benchmark (1 small test): ~$0.20/job - 12-minute optimized test

COST BREAKDOWN:

Fixed Monthly Costs (Idle): ~$10/month
- Web Services: $5.32 (scheduler + minimal traffic)
- Scheduler: $0.70 (automated every 10 min)
- Storage & Registry: $0.14 (with lifecycle policies)
- GitHub Actions: $0.21 (weekly cleanup)
- Base Infrastructure: $3.63 (networking, secrets)

Variable Costs:
- Benchmark Job (small): ~$0.20/job (12 minutes, testing/development)
- Production Job (medium): ~$0.50/job (30 minutes, typical use)
- Production Job (large): ~$1.10/job (67 minutes, complex models)
- Per Hour: ~$0.98 (8 vCPU, 32GB RAM compute)

KEY METRICS:
- Baseline (before optimization): $160/month
- Current (idle): $10/month
- Savings: $150/month (94% reduction)
- Benchmark job: $0.20 (12 min, for testing)
- Production job (typical): $0.50 (30 min, medium size)
- Job performance: 40-50% faster than baseline

OPTIMIZATIONS APPLIED:
✓ Scale-to-zero (no idle compute costs)
✓ CPU throttling (efficient resource usage)
✓ Scheduler automation (10-min intervals)
✓ Right-sized compute (8 vCPU optimized)
✓ Storage lifecycle policies
✓ Automated registry cleanup

COST MONITORING:
Thresholds:
- Warning: $30/month
- Alert: $60/month
- Critical: $100/month

Scripts:
python scripts/track_daily_costs.py --days 30 --use-user-credentials
python scripts/analyze_idle_costs.py --days 30 --use-user-credentials
```

---

## 💡 Usage Tips

1. **For Atlassian JIRA:** Use the first version with JIRA wiki markup
2. **For basic text fields:** Use the plain text version
3. **For GitHub issues:** Link to JIRA_COST_SUMMARY.md directly

**Files in this repository:**
- `JIRA_COST_SUMMARY.md` - Detailed markdown version
- `JIRA_COST_TICKET.md` - This file with JIRA-formatted versions
- `COST_STATUS.md` - Complete technical documentation (27KB+)

---

**Last Updated:** February 18, 2026
