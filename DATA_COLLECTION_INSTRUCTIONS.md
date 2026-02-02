# Data Collection Instructions for Cost Analysis

Dear User,

I've completed a comprehensive cost analysis for both your development and production environments. The analysis is now ready, but to refine it with **actual usage data** instead of estimates, I need your help collecting some information.

## 📁 What I've Created

1. **[docs/COST_ANALYSIS_DEV_PROD.md](docs/COST_ANALYSIS_DEV_PROD.md)** (29,000+ words)
   - Complete cost breakdown for dev and prod
   - Monthly projections for various usage levels
   - Shared vs dedicated resource analysis
   - Optimization recommendations

2. **[Cost estimate - Dev and Prod.csv](Cost%20estimate%20-%20Dev%20and%20Prod.csv)**
   - Detailed cost spreadsheet
   - Environment-specific estimates
   - Multiple usage scenarios

3. **[COST_ANALYSIS_QUICK_START.md](COST_ANALYSIS_QUICK_START.md)**
   - Quick guide to understanding the analysis
   - Next steps and key findings summary

4. **[scripts/collect_cost_data.sh](scripts/collect_cost_data.sh)**
   - Automated data collection script
   - Gathers GCS, Artifact Registry, Cloud Run data

## 🎯 Current Status

The analysis is complete with **estimated costs based on your infrastructure configuration**. Here's what I found:

### Key Configuration (Both Environments)
- **Training Jobs:** 8 vCPU, 32GB RAM (identical in dev and prod)
- **Web Services:** 2 vCPU, 4GB RAM, min_instances=0
- **Shared Project:** datawarehouse-422511
- **Shared Resources:** GCS bucket, Artifact Registry, Secret Manager

### Estimated Monthly Costs

| Scenario | Dev Only | Prod Only | Both Active |
|----------|----------|-----------|-------------|
| **Idle** | $1.79 | $1.79 | $1.79 |
| **Light (100 calls/month)** | $6.59 | $17.69-24.25 | $21.89-28.45 |
| **Moderate (500 calls/month)** | $26.78 | $82.27-115.11 | $103.26-136.10 |
| **Heavy (1000 calls/month)** | $51.77 | $162.75-228.43 | $204.73-270.41 |

**Key Insight:** Training jobs are 95%+ of costs. Dev typically uses benchmark workloads (12 min, $0.20/job) while prod uses production workloads (80-120 min, $1.33-$2.00/job).

## 📊 Data I Need From You

To refine these estimates with **actual usage data**, please provide the following:

### 1. Run the Data Collection Script

```bash
# From the repository root directory
cd /home/runner/work/mmm-app/mmm-app
./scripts/collect_cost_data.sh
```

This will create a `cost-analysis-data/` directory with several text files containing:
- GCS storage usage
- Artifact Registry image sizes
- Cloud Run job execution history
- Cloud Logging estimates
- Secret Manager details

**Action:** Send me the entire `cost-analysis-data/` directory or paste the contents of each file.

### 2. Actual GCP Billing Data

**In GCP Console:**
1. Go to **Billing → Reports**
2. Select project: `datawarehouse-422511`
3. Set time range: **Last 30 days** (or last full month)
4. Group by: **Service**
5. Take a screenshot or export to CSV

**What I need:**
- Total monthly cost for the project
- Breakdown by service (Cloud Run, Cloud Storage, etc.)
- If possible, filter by service name to separate dev vs prod

### 3. Training Job Usage Patterns

Please answer these questions:

**Development Environment (mmm-app-dev):**
- How many training jobs do you run per month in dev?
- What workload do you typically use? (benchmark 2K×5 or production 10K×5?)
- Average job duration you observe?

**Production Environment (mmm-app):**
- How many training jobs do you run per month in prod?
- What workload do you typically use? (production 10K×5?)
- Average job duration you observe?

### 4. Snowflake Usage

**Run this query in Snowflake:**

```sql
-- Query history for last 30 days
SELECT 
  DATE_TRUNC('day', START_TIME) as date,
  WAREHOUSE_NAME,
  COUNT(*) as query_count,
  SUM(TOTAL_ELAPSED_TIME)/1000 as total_seconds,
  SUM(CREDITS_USED_CLOUD_SERVICES) as credits_used
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
  END_TIME_RANGE_START=>DATEADD('day', -30, CURRENT_TIMESTAMP()),
  END_TIME_RANGE_END=>CURRENT_TIMESTAMP()
))
WHERE WAREHOUSE_NAME = 'SMALL_WH'
  AND USER_NAME = 'IPENC'
GROUP BY date, WAREHOUSE_NAME
ORDER BY date DESC;
```

**What I need:**
- Total query count for the month
- Total credits used
- Any patterns (e.g., mostly weekdays, specific time ranges)

### 5. GitHub Actions Build Frequency

**Using GitHub CLI or web interface:**

```bash
# List recent prod builds (main branch)
gh run list --repo ivana-meshed/mmm-app --workflow=ci.yml --limit=50

# List recent dev builds (dev, feat-* branches)
gh run list --repo ivana-meshed/mmm-app --workflow=ci-dev.yml --limit=50
```

**Or check GitHub Actions tab in your repository.**

**What I need:**
- Number of dev builds per month (ci-dev.yml)
- Number of prod builds per month (ci.yml)
- Average build duration (if available)

### 6. Web Service Request Patterns

**Optional but helpful:**

```bash
# Check Cloud Run service metrics
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/request_count" 
    AND resource.labels.service_name=("mmm-app-web" OR "mmm-app-dev-web")' \
  --format=json \
  --start-time="2026-01-01T00:00:00Z" \
  --end-time="2026-02-01T00:00:00Z"
```

**What I need:**
- Approximate web requests per month (dev vs prod)
- Or just "light usage", "moderate usage", etc.

## 📧 How to Send the Data

You can provide the data in any of these ways:

1. **Paste directly in chat** - Just copy/paste the output of the data collection script and answers to the questions
2. **Upload files** - If you run the script, you can share the files from `cost-analysis-data/`
3. **Screenshots** - For GCP Console billing data and GitHub Actions
4. **Text summary** - If you prefer to summarize the key numbers manually

## 🎨 What I'll Do Next

Once I receive your data, I will:

1. ✅ Calculate actual monthly costs by environment
2. ✅ Update the cost analysis document with real numbers
3. ✅ Identify specific cost optimization opportunities based on your usage
4. ✅ Provide precise cost projections for your actual workload patterns
5. ✅ Update the cost spreadsheet with real-world data
6. ✅ Give you specific recommendations for your use case

## 📋 Quick Questions (If You Want Faster Results)

If you can't run all the scripts right now, answering these key questions will help me provide better estimates:

1. **What's your actual monthly GCP bill for this project?** $___
2. **How many training jobs do you run per month?** Dev: ___ | Prod: ___
3. **What's typical in dev?** [ ] Benchmark (12 min) [ ] Production (80-120 min)
4. **What's typical in prod?** [ ] Benchmark (12 min) [ ] Production (80-120 min)
5. **GCS bucket size?** Approximately ___ GB
6. **How many deployments per month?** Dev: ___ | Prod: ___

## 📚 In the Meantime

While you collect the data, you can:

1. **Review the analysis** - Read [docs/COST_ANALYSIS_DEV_PROD.md](docs/COST_ANALYSIS_DEV_PROD.md)
2. **Check the spreadsheet** - Open [Cost estimate - Dev and Prod.csv](Cost%20estimate%20-%20Dev%20and%20Prod.csv)
3. **Read the quick start** - See [COST_ANALYSIS_QUICK_START.md](COST_ANALYSIS_QUICK_START.md)
4. **Set up budget alerts** - Follow instructions in the cost analysis doc

## 🔍 Understanding the Current Estimates

The current estimates are based on:
- ✅ Your actual Terraform configuration (8 vCPU, 32GB RAM)
- ✅ GCP pricing for europe-west1 region
- ✅ Verified production performance data (Jan 9, 2026 benchmarks)
- ❓ **Assumed usage patterns** (this is what we need to refine!)

The estimates are **conservative and likely accurate** for typical usage, but actual costs may vary based on:
- Training job frequency
- Workload types (benchmark vs production)
- GCS storage growth
- Web service request volume
- CI/CD build frequency

## ❓ Questions?

If you have questions about:
- **What data to collect** → See the numbered sections above
- **How to run the scripts** → Check the code examples provided
- **What the costs mean** → Read the comprehensive analysis document
- **How to optimize** → See the optimization recommendations section

I'm here to help! Just provide whatever data you can, and I'll refine the analysis accordingly.

---

**Ready to proceed?** Start with the data collection script:

```bash
cd /home/runner/work/mmm-app/mmm-app
./scripts/collect_cost_data.sh
```

Then share the results with me, and I'll update the analysis with your actual usage patterns.

---

**Created:** 2026-02-02  
**Status:** Awaiting actual usage data  
**Next Step:** Run data collection script and provide results
