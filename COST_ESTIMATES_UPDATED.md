# MMM Trainer
# Cost Estimates & Technical Requirements
# Summary for Self-Hosted Deployment

**Cost & Requirements Summary**  
**February 2026 - Updated (Cloud Tasks migration)**

---

## 1. Cost Estimates

All costs are based on actual production measurements using Google Cloud Platform services in the europe-west1 region. Costs are billed per-second for compute resources.

**Current Status:**
- ✅ Optimized deployment active
- ~94% cost reduction achieved ($160 → ~$8.80 idle/month)
- All optimizations applied (scale-to-zero, CPU throttling, event-driven queue processing)

### 1.1 Training Job Performance

| Workload | Iterations × Trials | Duration | Cost/Job | Use Case |
|----------|---------------------|----------|----------|----------|
| Test Run | 200 × 3 = 600 | 0.8 min | $0.01 | Quick validation |
| Benchmark | 2,000 × 5 = 10K | **12 min** | **$0.20** | Standard testing |
| Production (Medium) | 10,000 × 5 = 50K | **30 min** | **$0.50** | **Typical production runs** |
| Production (Large) | 10,000 × 5 = 50K | **67 min** | **$1.10** | High-quality runs |
| Production (Extra Large) | 10,000 × 10 = 100K | 160-240 min | $2.67-$4.00 | Very large datasets |

**Table 1:** Training job durations and costs (8 vCPU, 32GB RAM configuration)

**Notes:**
- **Benchmark runs** (12 minutes) are ideal for experimentation and testing
- **Production Medium** (30 minutes) is typical for most production use cases - **use this for planning**
- **Production Large** (67 minutes) for complex models with high iterations
- Performance: 40-50% faster than pre-optimization baseline

### 1.2 Monthly Cost Scenarios

| Usage Level | Web Calls | Training Jobs | Idle Cost | **Production (Medium) Cost** | Production (Large) Cost |
|-------------|-----------|---------------|-----------|------------------------------|------------------------|
| **Idle** | 0-100 | 0-2 | **$8.80** | **$8.80** | $8.80 |
| **Light** | 100-500 | 10 | $8.80 | **$13.80** | $19.80 |
| **Moderate** | 500-2,000 | 50 | $8.80 | **$33.80** | $63.80 |
| **Heavy** | 2,000-5,000 | 100 | $8.80 | **$58.80** | $118.80 |
| **Very Heavy** | 5,000+ | 500 | $8.80 | **$258.80** | $558.80 |

**Table 2:** Monthly cost estimates by usage volume (updated February 2026)

**Key Changes from Previous Estimates:**
- **Cloud Scheduler replaced by event-driven Cloud Tasks** — queue tick only runs when there is actual work; no wake-ups when the queue is empty
- Previous idle cost was ~$9.30/month (included $0.50/month scheduler overhead from 30-minute polling)
- **Updated idle cost: ~$8.80/month** (Cloud Tasks overhead ~$0.000120/month at 100 jobs is negligible)
- All scenarios based on actual February 2026 billing data

### 1.3 Cost Breakdown

**Fixed Monthly Costs (~$8.80/month):**
- Web Services: $5.32 (minimal traffic, scale-to-zero)
- Cloud Tasks: ~$0.000120 (event-driven queue tick, $0.40/million tasks)
- GCS Storage: $0.14 (with lifecycle policies)
- Secret Manager: $0.36 (6 secrets × $0.06)
- Artifact Registry: $0.50 (container images)
- GitHub Actions: $0.21 (weekly cleanup automation)
- Base infrastructure: $2.23 (networking, etc.)
- **Total Fixed: ~$8.80/month**

**Variable Costs (per usage):**
- **Benchmark job:** $0.20 (12 min) - testing/development only
- **Production Medium job:** $0.50 (30 min) - typical production use
- **Production Large job:** $1.10 (67 min) - complex models
- **Per Hour Cost:** $0.98 (8 vCPU, 32GB RAM compute)
- Web service requests: ~$0.002 per request (negligible)
- Snowflake: Separate (depends on warehouse size and queries)

**Key Cost Drivers:**
- Training jobs account for 90-99% of total costs at scale
- Idle cost is ~$8.80/month (no scheduler; Cloud Tasks fire only when work exists)
- Faster machines (8 vCPU) have similar cost to 4 vCPU due to 2.5× speed improvement
- Storage costs are minimal with lifecycle policies
- Network egress charged only for downloads outside GCP region

**Cost Monitoring:**
- Run `python scripts/track_daily_costs.py --days 30` for detailed breakdown
- Run `python scripts/analyze_idle_costs.py --days 30` for idle cost analysis

---

## 2. Minimum Technical Requirements

### 2.1 Required Skills and Knowledge

#### 2.1.1 Essential Skills

**Google Cloud Platform:**
- Basic understanding of Cloud Run, GCS, and IAM
- Ability to navigate GCP Console
- Understanding of billing and cost management

**Infrastructure as Code:**
- Basic Terraform knowledge for infrastructure changes
- Ability to read and modify Terraform configuration files

**Version Control:**
- Git and GitHub workflows
- Understanding of CI/CD concepts

**Container Technology:**
- Basic Docker concepts
- Understanding of container registries

#### 2.1.2 Recommended Skills

**Programming Languages:**
- Python (for Streamlit application modifications)
- R (for Robyn MMM customizations)

**Data Warehouse:**
- Snowflake query optimization
- SQL for data extraction

**Monitoring and Debugging:**
- Cloud Logging for troubleshooting
- Performance monitoring and optimization

### 2.2 Required Tools

| Tool | Version | Purpose |
|------|---------|---------|
| Google Cloud SDK | Latest | GCP CLI operations |
| Terraform | ≥1.5.0 | Infrastructure management |
| Docker | Latest | Container testing (optional) |
| Git | Latest | Version control |
| Python | ≥3.11 | Local development (optional) |

**Table 3:** Required development tools

### 2.3 Access Requirements

#### 2.3.1 Google Cloud Platform

**For Monitoring:**
- Viewer role

**For Deployments:**
- Editor or specific roles:
  - Cloud Run Admin
  - Storage Admin
  - Secret Manager Admin
  - Service Account Admin
  - Cloud Tasks Admin (for queue management)

**For Debugging:**
- Logs Viewer
- Monitoring Viewer

#### 2.3.2 GitHub Repository

**For Development:**
- Write access

**For Releases:**
- Maintain or Admin access

**For Secrets Management:**
- Admin access

#### 2.3.3 Snowflake

- Read access to source data tables
- Access to a dedicated warehouse for queries
- Appropriate role (not ACCOUNTADMIN in production)

---

## 3. Cost Optimization Status

**Baseline (Pre-optimization):** $160/month  
**Previous (with Cloud Scheduler):** ~$9.30/month idle  
**Current (Cloud Tasks, event-driven):** ~$8.80/month idle  
**Total Savings vs Baseline:** $151.20/month (~94.5% reduction)

**Optimizations Applied:**
1. ✅ Scale-to-zero (min_instances=0) - No idle compute costs
2. ✅ CPU throttling enabled - Efficient resource usage
3. ✅ **Event-driven queue processing (Cloud Tasks)** - Zero idle scheduling cost; tasks fire only when work exists
4. ✅ Resource optimization - 8 vCPU (2.5× faster than 4 vCPU baseline)
5. ✅ Storage lifecycle policies - Automatic cost reduction
6. ✅ Registry cleanup - Weekly automated cleanup via GitHub Actions

**Queue Processing Status:**
- ✅ **Cloud Tasks (event-driven)** — replaces Cloud Scheduler
- Cost: ~$0.000120/month at 100 jobs/month ($0.40/million tasks)
- Behaviour:
  - Job enqueued + queue started → immediate task fires
  - Job running → polling task every 300 s (configurable via `QUEUE_TICK_INTERVAL_SECONDS`)
  - Queue empty / paused → **no task created** → container scales to zero
- Previous Cloud Scheduler cost ($0.50/month, 30-min polling): eliminated

---

## 4. Additional Documentation

- **Detailed Cost Status:** See `COST_STATUS.md` in repository
- **Final Cost Documentation:** See `COST_DOCUMENTATION_FINAL.md`
- **Cost Tracking Scripts:** `scripts/track_daily_costs.py`, `scripts/analyze_idle_costs.py`
- **Architecture:** See `ARCHITECTURE.md`
- **Development & Testing Guide:** See `DEVELOPMENT.md`
