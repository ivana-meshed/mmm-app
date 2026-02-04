# Cost Analysis Quick Start Guide

This guide helps you understand and use the comprehensive cost analysis documents for the MMM Trainer application.

## 📚 Documentation Overview

### Main Cost Analysis Document
**[docs/COST_ANALYSIS_DEV_PROD.md](docs/COST_ANALYSIS_DEV_PROD.md)** - Your primary resource

This comprehensive 29,000+ word document includes:
- Executive summary with key findings
- Complete environment comparison (dev vs prod)
- Detailed breakdown of all 10+ cost components
- Monthly cost projections for various usage scenarios
- Shared vs dedicated resource analysis
- Cost allocation methodologies
- Data collection instructions
- Optimization recommendations

### Cost Spreadsheet
**[Cost estimate - Dev and Prod.csv](Cost%20estimate%20-%20Dev%20and%20Prod.csv)**

Detailed CSV with:
- Separate sections for Shared, Dev, and Prod resources
- Cost estimates for 5 usage levels (idle, 100, 500, 1000, 5000 calls/month)
- Environment-specific breakdowns
- Combined cost scenarios
- Cost attribution percentages

### Supporting Documents
- **[COST_OPTIMIZATION.md](COST_OPTIMIZATION.md)** - Single-environment cost guide with verified production data
- **[docs/COST_OPTIMIZATIONS_SUMMARY.md](docs/COST_OPTIMIZATIONS_SUMMARY.md)** - Implementation summary

## 🎯 Key Findings Summary

### Current Configuration
Both dev and prod environments use:
- **Training Jobs:** 8 vCPU, 32GB RAM
- **Web Services:** 2 vCPU, 4GB RAM, min_instances=0
- **Shared Resources:** Same GCP project, bucket, registry, secrets

### Cost Highlights

**Idle Cost (both environments):** ~$1.79/month
- Shared infrastructure split between environments

**Light Usage (100 calls/month per environment):**
- Dev only: ~$6.59/month (benchmark workloads)
- Prod only: ~$17.69-24.25/month (production workloads)
- Both active: ~$21.89-28.45/month

**Moderate Usage (500 calls/month per environment):**
- Dev only: ~$26.78/month
- Prod only: ~$82.27-115.11/month
- Both active: ~$103.26-136.10/month

**Heavy Usage (1000 calls/month per environment):**
- Dev only: ~$51.77/month
- Prod only: ~$162.75-228.43/month
- Both active: ~$204.73-270.41/month

### Key Insight
**Training jobs account for 95%+ of variable costs** in both environments. The biggest cost driver is:
- Benchmark workloads (dev): 12 min @ $0.20/job
- Production workloads (prod): 80-120 min @ $1.33-$2.00/job

## 📊 Next Steps: Get Actual Data

### Step 1: Run Data Collection Script

```bash
# From repository root
./scripts/collect_cost_data.sh
```

This creates a `cost-analysis-data/` directory with:
- GCS storage usage by environment
- Artifact Registry image sizes
- Cloud Run job execution history
- Cloud Logging volume estimates
- Secret Manager details
- Cloud Scheduler job status

### Step 2: Collect Additional Data

**Snowflake Usage:**
```sql
-- Run in Snowflake to get query history
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

**GitHub Actions:**
```bash
# Check recent workflow runs
gh run list --repo ivana-meshed/mmm-app --workflow=ci.yml --limit=50
gh run list --repo ivana-meshed/mmm-app --workflow=ci-dev.yml --limit=50
```

### Step 3: Analyze Actual Costs

**In GCP Console:**
1. Go to **Billing → Reports**
2. Filter by project: `datawarehouse-422511`
3. Group by: Service
4. Time range: Last 30 days
5. Export to CSV or analyze trends

**Enable Detailed Billing Export (Recommended):**
```bash
# Create BigQuery dataset
bq mk --dataset --location=europe-west1 billing_export

# Then enable in GCP Console:
# Billing → Billing Export → BigQuery Export → Enable
```

### Step 4: Update Documentation

Once you have actual data:
1. Review collected data in `cost-analysis-data/`
2. Compare with estimates in `docs/COST_ANALYSIS_DEV_PROD.md`
3. Calculate actual monthly costs by environment
4. Update the "Key Findings" section with real numbers
5. Adjust projections based on observed patterns

## 🔍 Understanding Environment Differences

### Development Environment (mmm-app-dev)
- Service: `mmm-app-dev-web`
- Training Job: `mmm-app-dev-training`
- Queue: `default-dev`
- Typical workload: **Benchmark** (2K×5 iterations, 12 min, $0.20/job)
- Use case: Testing, validation, feature development

### Production Environment (mmm-app)
- Service: `mmm-app-web`
- Training Job: `mmm-app-training`
- Queue: `default`
- Typical workload: **Production** (10K×5 iterations, 80-120 min, $1.33-$2.00/job)
- Use case: Business operations, customer-facing models

### Shared Resources
- GCS Bucket: `mmm-app-output` (logical separation via prefixes)
- Artifact Registry: `mmm-repo` (images tagged by commit SHA)
- Secret Manager: All secrets (project-wide)
- Snowflake: `SMALL_WH` warehouse (shared queries)
- Service Accounts: Shared across environments

## 💡 Cost Optimization Recommendations

### Immediate Actions (This Week)
1. ✅ Run `scripts/collect_cost_data.sh` to gather metrics
2. ✅ Review actual costs in GCP Console → Billing
3. ✅ Set up budget alerts:
   - Dev: $100/month threshold
   - Prod: $500/month threshold
4. ✅ Enable BigQuery billing export for detailed tracking

### Short-term (Next 2 Weeks)
1. Analyze training job frequency by environment
2. Verify dev uses benchmark workloads (not production)
3. Review GCS storage growth patterns
4. Consider dev-specific lifecycle policies (30-day deletion)

### Medium-term (Next Month)
1. Consider reducing dev training resources to 4 vCPU/16GB (50% cost savings)
2. Implement result compression for production jobs (50% storage/egress savings)
3. Optimize Artifact Registry cleanup policies
4. Review memory usage to identify right-sizing opportunities

### Long-term (Next Quarter)
1. Evaluate separate GCP projects for dev/prod (if cost allocation is critical)
2. Regular quarterly cost reviews
3. Monitor for Cloud Run preemptible/spot instance availability

## 📋 Quick Reference: Cost Attribution

### How to Track Costs by Environment

**Cloud Run:**
- Services are named differently: `mmm-app-web` vs `mmm-app-dev-web`
- Jobs are named differently: `mmm-app-training` vs `mmm-app-dev-training`
- Filter GCP billing by service name

**GCS Storage:**
- Monitor by prefix patterns:
  - `/training-configs/default/` → Production
  - `/training-configs/default-dev/` → Development
- Use `gsutil du -sh gs://mmm-app-output/training-configs/default*/`

**Cloud Logging:**
- Filter by resource labels:
  - `resource.labels.service_name="mmm-app-web"` → Production
  - `resource.labels.service_name="mmm-app-dev-web"` → Development

**Shared Resources:**
- Split costs 50/50 for: Artifact Registry, Secret Manager base costs
- Track by usage for: Snowflake queries, Cloud Logging volume

## 🆘 Support

### Questions About Costs?
1. Check **[docs/COST_ANALYSIS_DEV_PROD.md](docs/COST_ANALYSIS_DEV_PROD.md)** first
2. Review actual billing data in GCP Console
3. Compare with estimates in the CSV spreadsheet

### Need to Update Estimates?
1. Collect actual data using provided scripts
2. Update markdown document with real numbers
3. Adjust CSV formulas if needed
4. Document assumptions and methodology

### Contact
For questions about this cost analysis:
- Review the comprehensive documentation
- Check GCP billing console for actual costs
- Consult the data collection instructions

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-02  
**Next Review:** Quarterly (after collecting actual usage data)
