# Training Job Cost Optimization Guide

## Problem

**Your training jobs are the #1 cost driver**, consuming ~95% of variable costs at scale.

### Cost Breakdown (Current Configuration)

| Scenario | Web Service | Training Jobs | Fixed | **Total** |
|----------|-------------|---------------|-------|-----------|
| 100 calls | $2.68 | $50.69 | $2.09 | **$55.46** |
| 500 calls | $13.40 | $253.43 | $2.09 | **$268.92** |
| 1,000 calls | $26.80 | $506.86 | $2.09 | **$535.75** |
| 5,000 calls | $134.00 | $2,534.30 | $2.09 | **$2,670.39** |

**Training jobs account for 90-95% of all variable costs!**

## Solution: Right-Size Your Training Jobs

### Current Implementation: 4 vCPU / 16 GB (50% savings vs original)

**Current configuration:**
- CPU: 4 vCPU (was 8)
- Memory: 16 GB (was 32 GB)
- Cost per job: ~$50.70 (CPU: $34.56 + Memory: $14.40 + GCS storage/egress: ~$1.50 + execution: $0.01)

**Savings vs original (8 vCPU/32 GB):**
- Per job: ~50% reduction
- At scale: Training is still the dominant cost driver (~95% of variable costs)

**Trade-off:**
- Jobs may take 1.5-2x longer
- For most MMM training workloads, this is acceptable
- R/Robyn can run efficiently on 4 cores

### Alternative: 2 vCPU / 8 GB (75% savings)

For maximum savings (if jobs are quick):

**Configuration:**
- CPU: 2 vCPU
- Memory: 8 GB
- Cost per job: ~$25 (CPU: $17.28 + Memory: $7.20 + GCS: ~$1.50 + execution: $0.01)

**Trade-off:**
- Jobs will take 2-4x longer
- Only suitable if current jobs complete in < 30 minutes
- Used in dev environment for testing

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
- Training job costs as primary cost driver

## Cost Comparison Table

| Config | vCPU | Memory | Cost/Job | 10 Jobs | 50 Jobs | 500 Jobs |
|--------|------|--------|----------|---------|---------|----------|
| Original | 8 | 32 GB | ~$100 | $1,000 | $5,000 | $50,000 |
| **Current** | 4 | 16 GB | ~$51 | $510 | $2,550 | $25,500 |
| Aggressive | 2 | 8 GB | ~$25 | $250 | $1,250 | $12,500 |

*Note: 1 training job per 10 web requests. Costs include CPU, memory, GCS storage/egress, and execution fees.*

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

**Current Cost Structure (at 5,000 calls/month):**
- Training Jobs: $2,534.30 (95% of variable costs)
- Web Service: $134.00 (5% of variable costs)
- Fixed Infrastructure: $2.09
- **Total: $2,670.39/month**

**Risk:**
- Low - can easily revert if needed
- Jobs will just take a bit longer
- Quality should be unaffected

## References

- Cost estimates: `Cost estimate.csv`
- Terraform config: `infra/terraform/main.tf`
- Production values: `infra/terraform/envs/prod.tfvars`
- Cost optimization guide: `COST_OPTIMIZATION.md`
