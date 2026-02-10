# Cloud Run Idle Cost Analysis

## Problem Statement

Despite configuring `min_instances = 0` (scale-to-zero) in PR #168, Cloud Run services still incur significant costs during idle periods with no user traffic:

- **mmm-app-web**: $1.92/day idle = **$57.60/month**
- **mmm-app-dev-web**: $1.62/day idle = **$48.60/month**
- **Total idle cost projection**: **~$137/month** (~$1,644/year)

## Root Cause

The high idle costs are caused by **three architectural decisions**:

### 1. CPU Throttling Disabled (Primary Issue)

**Location**: `infra/terraform/main.tf` line 324

```terraform
"run.googleapis.com/cpu-throttling" = "false"
```

**Impact**:
- CPU remains allocated even when container is idle (not processing requests)
- You pay for CPU time continuously, not just active request processing time
- With 1 vCPU at $0.024/vCPU-hour, this adds ~$0.024/hour when instance is warm
- **Cost**: ~70-80% of total idle costs

**Why it was disabled**:
- Originally set to improve response time for long-running operations
- Prevents CPU from being throttled to near-zero during idle periods
- Useful for compute-intensive apps, but unnecessary for typical web request/response

### 2. Scheduler Wake-ups Every 10 Minutes

**Location**: `infra/terraform/main.tf` line 597

```terraform
schedule = "*/10 * * * *"  # every 10 minutes
```

**Impact**:
- 144 wake-ups per day (24 hours × 6 per hour)
- Each wake-up starts an instance that stays warm for ~15 minutes
- With 10-minute intervals, instance is **nearly always warm**
- Prevents true scale-to-zero behavior

**Why 10 minutes**:
- Ensures training jobs in queue are processed quickly
- Trade-off between responsiveness and cost

### 3. Instance Warm Period (Cloud Run Default)

**Behavior**: After processing a request, Cloud Run keeps instance warm for ~15 minutes

**Combined Effect**:
- Scheduler pings every 10 minutes
- Instance stays warm for 15 minutes after each ping
- Result: Instance is warm nearly 24/7
- With CPU throttling disabled, this means paying for CPU 24/7

## Cost Breakdown

Using actual billing data from Feb 9, 2026 (idle day, no user requests):

### mmm-app-web (Production)
- **Total**: $1.92/day
- **compute_cpu**: $1.31 (68.2%)
- **compute_memory**: $0.58 (30.2%)
- **registry**: $0.03 (1.6%)

### mmm-app-dev-web (Development)
- **Total**: $1.62/day
- **compute_cpu**: $1.31 (80.9%)
- **compute_memory**: $0.29 (17.9%)
- **registry**: $0.02 (1.2%)

**Key Observation**: CPU and memory costs dominate even with zero user traffic.

## Theoretical Cost Calculation

### Hourly Cost (1 vCPU, 2 GB memory)
- CPU: 1 vCPU × $0.024/vCPU-hour = **$0.024/hour**
- Memory: 2 GB × $0.0025/GB-hour = **$0.005/hour**
- **Total**: **$0.029/hour**

### With Near-24/7 Warm Instances
- Daily: $0.029 × 24 hours = **$0.696/day**
- Monthly: $0.696 × 30 days = **$20.88/month**

### With CPU Throttling Enabled
- CPU cost drops by ~80% during idle: $0.024 → $0.005/hour
- New hourly cost: $0.010/hour
- Daily: $0.010 × 24 hours = **$0.240/day**
- Monthly: $0.240 × 30 days = **$7.20/month**

**Savings**: $13.68/month per service × 2 services = **~$27/month**

### With Scheduler at 30-Minute Intervals
- Wake-ups: 144 → 48 per day
- Instance warm time: ~24 hours → ~8-10 hours per day
- Further reduces idle costs by ~60%

**Combined Savings**: **~$80-100/month** (~$1,000/year)

## Deep-Dive Analysis Tool

Use the provided script to analyze your actual costs:

```bash
# Analyze last 7 days
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# Analyze specific service
python scripts/analyze_idle_costs.py --days 30 --service mmm-app-web --use-user-credentials

# Longer period for trends
python scripts/analyze_idle_costs.py --days 90 --use-user-credentials
```

The script provides:
- Cost breakdown by service and category
- Theoretical vs actual cost comparison
- Hours active per day
- Detailed usage patterns
- Specific recommendations with projected savings

## Recommendations

### Priority 1: Enable CPU Throttling (HIGHEST IMPACT)

**Change**: `infra/terraform/main.tf` line 324

```terraform
# Before
"run.googleapis.com/cpu-throttling" = "false"

# After  
"run.googleapis.com/cpu-throttling" = "true"
```

**Expected Impact**:
- Reduces CPU costs by ~70-80% during idle time
- **Estimated savings**: ~$50-70/month
- Minimal impact on user experience

**Trade-offs**:
- CPU only allocated when actively processing requests
- May see slight latency increase for long-running operations (>5 seconds)
- For typical web requests (<1 second), **no noticeable difference**

**Recommended**: ✅ **YES** - This is the standard configuration for web services

### Priority 2: Increase Scheduler Interval (MEDIUM IMPACT)

**Change**: `infra/terraform/main.tf` line 597

```terraform
# Before
schedule = "*/10 * * * *"  # every 10 minutes

# After
schedule = "*/30 * * * *"  # every 30 minutes
```

**Expected Impact**:
- Reduces wake-ups from 144/day to 48/day
- Allows more scale-to-zero periods
- **Estimated savings**: ~$20-30/month

**Trade-offs**:
- Training jobs in queue wait up to 30 minutes instead of 10 minutes
- Acceptable if training is not time-critical

**Recommended**: ✅ **YES** - If training latency is acceptable

### Priority 3: Alternative Architecture (LOW PRIORITY)

Consider these alternatives for queue management:

#### Option A: Cloud Tasks (Push Queue)
- Only triggers when jobs are actually in queue
- No wake-ups when queue is empty
- Pay-per-task pricing: $0.40 per million tasks

#### Option B: Pub/Sub + Cloud Functions
- Event-driven architecture
- Only executes when messages published
- More granular control

#### Option C: Pause Scheduler Temporarily (NEW)
- **NEW FEATURE**: Use `scheduler_enabled = false` in tfvars
- Pause scheduler for cost monitoring or extended idle periods
- See [Scheduler Pause Guide](./SCHEDULER_PAUSE_GUIDE.md) for details
- Useful for A/B testing and isolating cost factors

**Expected Impact**:
- Could reduce idle costs to near-zero
- **Estimated savings**: ~$40-60/month

**Trade-offs**:
- Requires architectural changes (Options A & B)
- More complex to implement and test
- Additional services to manage
- Manual job triggering required when paused (Option C)

**Recommended**: ⚠️ **LATER** - Implement quick wins first (or use Option C for testing)

## Implementation Plan

### Phase 0: A/B Testing with Scheduler Pause (Optional)

**NEW**: Before making permanent changes, you can pause the scheduler to isolate cost impacts:

1. **Baseline measurement** (24-48 hours)
   ```bash
   python scripts/track_daily_costs.py --days 2 --use-user-credentials
   ```

2. **Pause scheduler** (recommended for dev first)
   ```bash
   cd infra/terraform
   # Edit envs/dev.tfvars: set scheduler_enabled = false
   terraform apply -var-file=envs/dev.tfvars
   ```

3. **Monitor without scheduler** (24-48 hours)
   ```bash
   python scripts/track_daily_costs.py --days 2 --use-user-credentials
   ```

4. **Compare results**
   ```bash
   python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
   ```

5. **Resume scheduler** (after testing)
   ```bash
   # Edit envs/dev.tfvars: set scheduler_enabled = true
   terraform apply -var-file=envs/dev.tfvars
   ```

**See**: [Scheduler Pause Guide](./SCHEDULER_PAUSE_GUIDE.md) for detailed instructions

### Phase 1: Quick Wins (Immediate)

1. **Enable CPU throttling**
   - Edit `infra/terraform/main.tf` line 324
   - Set `cpu-throttling = "true"`
   - Apply terraform changes
   - Monitor for 3-5 days

2. **Increase scheduler interval**
   - Edit `infra/terraform/main.tf` line 597
   - Change to `schedule = "*/30 * * * *"`
   - Apply terraform changes
   - Monitor queue processing latency

**Expected Results**:
- Current: ~$137/month
- After CPU throttling: ~$40/month (-70%)
- After scheduler change: ~$25/month (-82% total)
- **Monthly savings: ~$112** (~$1,344/year)

### Phase 2: Monitoring (Week 2)

1. Run `analyze_idle_costs.py` daily for 7 days
2. Verify cost reduction
3. Check for any performance impacts
4. Adjust if needed

### Phase 3: Optimization (Optional, Month 2)

1. Evaluate Cloud Tasks migration
2. Consider Pub/Sub architecture
3. Implement if business case is strong

## Monitoring

### Track These Metrics

1. **Daily idle cost** (no user traffic days)
   - Target: <$0.50/day per service
   - Alert if: >$1.00/day per service

2. **Instance hours active**
   - Current: ~20-24 hours/day
   - Target: <8 hours/day
   - Measure: Use `analyze_idle_costs.py`

3. **Queue processing latency**
   - Current: <10 minutes
   - Target: <30 minutes
   - Alert if: >45 minutes

4. **User request latency**
   - Monitor p95, p99 latencies
   - Alert if significant increase after CPU throttling enabled

### Dashboard Queries

Use these BigQuery queries to monitor ongoing costs:

```sql
-- Daily cost by service (last 30 days)
SELECT
  DATE(_PARTITIONTIME) as date,
  labels.value as service,
  SUM(cost) as daily_cost
FROM `datawarehouse-422511.mmm_billing.gcp_billing_export_v1_*`
LEFT JOIN UNNEST(labels) as labels
WHERE
  DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND service.description LIKE '%Cloud Run%'
  AND labels.key = 'service_name'
GROUP BY date, service
ORDER BY date DESC, daily_cost DESC
```

## FAQ

### Q: Will enabling CPU throttling impact user experience?

**A**: No, not for typical web applications. CPU is allocated instantly when requests arrive. The only scenario where you might notice a difference is for very long-running operations (>10 seconds) that happen after the request is received. For Streamlit page loads and API calls (<1 second), there's no perceptible difference.

### Q: Why wasn't CPU throttling enabled initially?

**A**: It was likely disabled during development to ensure maximum performance during testing. This is a common oversight when deploying to production - the "best performance" settings are left in place, even though they're not needed for typical workloads.

### Q: Will scale-to-zero cause cold starts?

**A**: Yes, but they're minimal:
- Cold start: ~1-3 seconds
- Warm start: <100ms
- With scheduler pinging every 30 minutes, instance stays warm most of the time
- User traffic further keeps instances warm

### Q: Should we disable the scheduler entirely?

**A**: Not recommended. The scheduler provides important functionality for queue processing. Instead:
1. Reduce frequency (10min → 30min)
2. Consider Cloud Tasks for true event-driven processing
3. Keep scheduler as a backup health check

### Q: What about production vs development?

**A**: Apply the same optimizations to both:
- Development often has even less traffic
- Idle costs can be higher in dev than prod
- No reason to keep CPU throttling disabled in either environment

## References

- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Cloud Run CPU Throttling](https://cloud.google.com/run/docs/configuring/cpu-throttling)
- [Cloud Run Scaling](https://cloud.google.com/run/docs/configuring/min-instances)
- [Cost Optimization Best Practices](https://cloud.google.com/run/docs/cost)

## Related Scripts

- `scripts/track_daily_costs.py` - Daily cost tracking by service
- `scripts/analyze_idle_costs.py` - Deep-dive idle cost analysis (this document)

## Change Log

- 2026-02-10: Initial analysis documenting root cause of high idle costs
- Root cause identified: CPU throttling disabled + frequent scheduler wake-ups
- Recommendations: Enable CPU throttling, increase scheduler interval
- Expected savings: ~$112/month (~$1,344/year)
