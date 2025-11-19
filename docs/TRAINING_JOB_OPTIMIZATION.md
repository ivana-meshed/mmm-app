# Training Job Cost Optimization Guide

## Problem

**Your training jobs are the #1 cost driver**, consuming 50-70% of total infrastructure costs at scale.

### Current Costs (Before Optimization)

| Configuration | Cost per Job | 500 Jobs/Month | Annual Cost |
|--------------|--------------|----------------|-------------|
| 8 vCPU / 32 GB | $60.90 | $30,450 | $365,400 |

**At 5,000 calls/month (500 jobs), training jobs cost $30,450/month!**

## Solution: Right-Size Your Training Jobs

### Implemented: 4 vCPU / 16 GB (50% savings)

**New configuration:**
- CPU: 4 vCPU (was 8)
- Memory: 16 GB (was 32 GB)
- Cost per job: $30.50 (was $60.90)

**Savings:**
- Per job: $30.40 (50% reduction)
- 500 jobs/month: **$15,225/month** saved
- Annual: **$182,700** saved

**Trade-off:**
- Jobs may take 1.5-2x longer
- For most MMM training workloads, this is acceptable
- R/Robyn can run efficiently on 4 cores

### Alternative: 2 vCPU / 8 GB (75% savings)

For maximum savings (if jobs are quick):

**Configuration:**
- CPU: 2 vCPU
- Memory: 8 GB
- Cost per job: $15.22

**Savings:**
- Per job: $45.68 (75% reduction)
- 500 jobs/month: **$22,840/month** saved
- Annual: **$274,080** saved

**Trade-off:**
- Jobs will take 2-4x longer
- Only suitable if current jobs complete in < 30 minutes

## How It Works

### Terraform Variables (Already Implemented)

In `infra/terraform/variables.tf`:

```hcl
variable "training_cpu" {
  description = "CPU limit for training job"
  default     = "4.0"  # Changed from 8.0
}

variable "training_memory" {
  description = "Memory limit for training job"
  default     = "16Gi"  # Changed from 32Gi
}

variable "training_max_cores" {
  description = "Maximum cores for R/Robyn"
  default     = "4"  # Changed from 8
}
```

### Production Configuration

In `infra/terraform/envs/prod.tfvars`:

```hcl
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "4"
```

### Dev Configuration (More Aggressive)

In `infra/terraform/envs/dev.tfvars`:

```hcl
training_cpu       = "2.0"  # Even smaller for dev
training_memory    = "8Gi"
training_max_cores = "2"
```

## Deployment

### Apply to Production

```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

### Apply to Dev

```bash
cd infra/terraform
terraform apply -var-file="envs/dev.tfvars"
```

## Monitoring & Validation

### Check Job Completion Time

After deployment, monitor training job duration:

1. **Before optimization:**
   - Check Cloud Run job execution logs
   - Note typical completion time (e.g., 45 minutes)

2. **After optimization:**
   - Monitor first few jobs
   - New completion time (e.g., 60-75 minutes with 4 vCPU)
   - Verify results are still good quality

3. **If jobs take too long:**
   - Increase to 6 vCPU / 24 GB for middle ground
   - Or revert to 8 vCPU / 32 GB

### Monitor Costs

Track in GCP Billing Console:

```bash
# View Cloud Run costs
gcloud billing accounts list
gcloud beta billing budgets list --billing-account=<ACCOUNT_ID>
```

Look for:
- Cloud Run job execution costs dropping by ~50%
- Total monthly costs reducing by $15,225+

## Cost Comparison Table

| Config | vCPU | Memory | Cost/Job | 100 Jobs | 500 Jobs | 5000 Jobs (annually) |
|--------|------|--------|----------|----------|----------|---------------------|
| Original | 8 | 32 GB | $60.90 | $6,090 | $30,450 | $365,400 |
| Recommended | 4 | 16 GB | $30.50 | $3,050 | $15,225 | $182,700 |
| Aggressive | 2 | 8 GB | $15.22 | $1,522 | $7,610 | $91,320 |

## When to Adjust

### Increase CPU/Memory if:
- Jobs are timing out (> 6 hours)
- Jobs are failing with OOM (out of memory) errors
- Model quality is degrading
- You need faster results

### Decrease CPU/Memory if:
- Jobs complete in < 30 minutes
- CPU utilization is consistently < 50%
- You want maximum cost savings

### How to Adjust

Edit `infra/terraform/envs/prod.tfvars`:

```hcl
# For middle ground (6 vCPU):
training_cpu       = "6.0"
training_memory    = "24Gi"
training_max_cores = "6"

# For maximum savings (2 vCPU):
training_cpu       = "2.0"
training_memory    = "8Gi"
training_max_cores = "2"

# To revert to original (8 vCPU):
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"
```

Then apply:
```bash
terraform apply -var-file="envs/prod.tfvars"
```

## Best Practices

1. **Start Conservative**: Use 4 vCPU/16GB (already configured)
2. **Monitor First**: Run 10-20 jobs and check completion time
3. **Iterate**: Adjust based on actual performance
4. **Test in Dev First**: Try aggressive settings (2 vCPU) in dev before prod
5. **Document Findings**: Track optimal settings for your workload

## Expected Results

### Success Criteria

After switching to 4 vCPU/16GB:
- ✅ Jobs complete in reasonable time (< 2 hours)
- ✅ Model quality unchanged
- ✅ Costs reduced by ~50%
- ✅ No OOM errors
- ✅ No timeouts

### Warning Signs

If you see:
- ⚠️ Jobs taking > 4 hours
- ⚠️ Frequent OOM errors
- ⚠️ Timeout failures
- ⚠️ Degraded model quality

**Action:** Increase to 6 vCPU/24GB or revert to 8 vCPU/32GB

## FAQ

### Q: Will this affect model quality?
**A:** No. The same training happens, just on fewer cores. R/Robyn parallelizes well and will use available cores efficiently.

### Q: How much longer will jobs take?
**A:** Approximately 1.5-2x longer with 4 vCPU (vs 8 vCPU). If jobs currently take 45 min, expect 60-90 min.

### Q: What if I need faster results?
**A:** Increase CPU back to 6 or 8 vCPUs. The cost savings are still there when you need them.

### Q: Can I have different sizes for different jobs?
**A:** Currently, all jobs use the same configuration. Future enhancement: pass CPU/memory as job parameters.

### Q: What about memory-intensive jobs?
**A:** If you get OOM errors, increase memory independently:
```hcl
training_cpu    = "4.0"  # Keep 4 vCPU
training_memory = "24Gi" # Increase memory to 24GB
```

## Summary

**Recommended Action:**
1. ✅ Already configured for 4 vCPU/16GB in prod
2. Deploy with `terraform apply`
3. Monitor first 10-20 jobs
4. Adjust if needed

**Expected Savings:**
- **$15,225/month** at 500 jobs/month
- **$182,700/year** annually
- **50% reduction** in training job costs

**Risk:**
- Low - can easily revert if needed
- Jobs will just take a bit longer
- Quality should be unaffected

## References

- Cost estimates: `Cost estimate.csv`
- Terraform config: `infra/terraform/main.tf`
- Production values: `infra/terraform/envs/prod.tfvars`
