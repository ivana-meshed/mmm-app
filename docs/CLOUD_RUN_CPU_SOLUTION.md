# Cloud Run CPU Core Allocation - Solution

## Problem Analysis

Cloud Run Jobs configured with 4 vCPU are only providing 2 actual cores for parallel processing due to cgroups quota limitations. This results in:
- Paying for 4 vCPU but only using 2 cores
- 50% waste in compute resources
- Training jobs taking 2x longer than they should

## Root Cause Investigation

After reviewing the Terraform configuration and Cloud Run Jobs documentation, the issue is likely caused by one or more of the following:

1. **Missing Execution Environment Setting**: Cloud Run has two execution environments:
   - **gen1** (first generation): May have more restrictive CPU allocation
   - **gen2** (second generation): Provides better CPU and memory allocation

2. **CPU Always Allocated Not Enforced**: By default, Cloud Run may throttle CPU allocation based on workload patterns

3. **Resource Tier Limitations**: Some vCPU tiers may have platform-imposed quota limits that don't match the requested vCPU count

## Recommended Solution

### Option 1: Configure Execution Environment (RECOMMENDED)

Add explicit execution environment configuration to the Cloud Run Job:

```hcl
resource "google_cloud_run_v2_job" "training_job" {
  name     = "${var.service_name}-training"
  location = var.region

  template {
    template {
      service_account = google_service_account.training_job_sa.email
      timeout         = "21600s"
      max_retries     = 1
      
      # Add execution environment specification
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

      containers {
        name  = "training-container"
        image = var.training_image

        resources {
          limits = {
            cpu    = var.training_cpu
            memory = var.training_memory
          }
          
          # Ensure CPU is always allocated (not throttled)
          cpu_idle = true
        }

        # ... rest of configuration
      }
    }
  }
}
```

**Expected Outcome**: Gen2 execution environment provides better CPU quota enforcement, should provide closer to requested vCPU count in actual cores.

### Option 2: Test Higher vCPU Tiers

Some vCPU tiers may have better core allocation. Test progression:

| Configuration | Expected Cores | Cost per Hour | Test Priority |
|---------------|----------------|---------------|---------------|
| 4 vCPU / 16GB | 2-4 cores | ~$0.58 | Current |
| 8 vCPU / 32GB | 4-8 cores | ~$1.17 | High |
| 16 vCPU / 64GB | 8-16 cores | ~$2.33 | Medium |
| 32 vCPU / 128GB | 16-32 cores | ~$4.67 | Low |

**Rationale**: Higher vCPU tiers may bypass platform quotas that affect lower tiers.

### Option 3: Migrate to GKE Autopilot (ALTERNATIVE)

If Cloud Run Jobs continue to have quota limitations, migrate training jobs to GKE Autopilot:

**Pros**:
- Predictable CPU allocation (request = actual cores)
- More control over resource scheduling
- Similar serverless experience with Autopilot
- Competitive pricing (~$0.05/vCPU-hour)

**Cons**:
- More complex setup and maintenance
- Need to manage Kubernetes resources
- Less integrated with Cloud Run ecosystem

**Implementation Effort**: ~2-3 days for initial setup and testing

### Option 4: Use Cloud Batch (ALTERNATIVE)

Cloud Batch is designed for batch processing workloads:

**Pros**:
- Better CPU allocation for batch jobs
- Built-in job scheduling and management
- Competitive pricing
- No cold start issues

**Cons**:
- Different API and workflow
- Need to refactor job submission logic
- Less mature than Cloud Run

**Implementation Effort**: ~1-2 days for migration

## Implementation Plan

### Phase 1: Test Execution Environment Setting (1-2 hours)

1. Update Terraform configuration with `execution_environment = "EXECUTION_ENVIRONMENT_GEN2"`
2. Deploy to dev environment
3. Run test training job
4. Check logs for core allocation:
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_job AND resource.labels.job_name=mmm-app-dev-training" \
     --limit=50 | grep "Final cores for training"
   ```
5. If successful: Deploy to prod

**Success Criteria**: 
- `parallelly::availableCores()` reports 3-4 cores (75-100% of requested)
- Training performance improves by 50-100%

### Phase 2: Test Higher vCPU Tier (if Phase 1 doesn't work)

1. Update Terraform to use 8 vCPU / 32GB
2. Deploy to dev environment
3. Run test training job
4. Check core allocation and training time
5. Calculate cost vs. performance improvement

**Success Criteria**:
- Get 4+ cores available
- Cost increase justified by performance improvement
- Total cost per training job remains acceptable

### Phase 3: Evaluate Alternatives (if Phase 1 & 2 don't work)

Research and prototype:
1. GKE Autopilot with training jobs
2. Cloud Batch API integration
3. Compute Engine with managed instance groups

## Testing and Validation

### Dev Environment Testing

```bash
# Deploy changes
cd infra/terraform
terraform apply -var-file=envs/dev.tfvars

# Trigger test job via UI or API
# Monitor job execution
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1

# Check logs for core detection
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"CORE DETECTION ANALYSIS\"" \
  --limit=50 --format=json | jq -r '.[].textPayload'
```

### Production Validation

After successful dev testing:

```bash
# Deploy to prod
terraform apply -var-file=envs/prod.tfvars

# Monitor first 3-5 jobs
# Compare with baseline performance
# Verify cost impact
```

## Cost Analysis

### Current State (4 vCPU, 2 cores)
- **Cost**: $0.58/hour = ~$2.92 per 30-min job
- **Performance**: 2 cores
- **Cost per core**: $1.46/core

### Expected with Gen2 (4 vCPU, 4 cores)
- **Cost**: $0.58/hour = ~$2.92 per 30-min job
- **Performance**: 4 cores
- **Cost per core**: $0.73/core (50% improvement)
- **Training time**: 50% faster

### With 8 vCPU (if needed)
- **Cost**: $1.17/hour = ~$5.85 per 30-min job
- **Performance**: 6-8 cores (estimated)
- **Cost per core**: $0.73-$0.98/core
- **Training time**: 75% faster than current

## Rollback Plan

If changes cause issues:

```bash
# Revert Terraform changes
cd infra/terraform
git checkout HEAD~1 main.tf
terraform apply -var-file=envs/prod.tfvars
```

Or manually revert specific changes in the Terraform configuration.

## Success Metrics

Track these metrics before and after changes:

1. **Core Allocation**:
   - `parallelly::availableCores()` value from logs
   - Target: 3-4 cores (75-100% of requested)

2. **Training Performance**:
   - Time to complete training job
   - Target: 25-50% improvement

3. **Cost Efficiency**:
   - Cost per training job
   - Cost per core-hour
   - Target: Same or better cost per core

4. **Reliability**:
   - Job success rate
   - No increase in failures

## References

- [Cloud Run Execution Environments](https://cloud.google.com/run/docs/about-execution-environments)
- [Cloud Run CPU Allocation](https://cloud.google.com/run/docs/configuring/cpu-allocation)
- [Cloud Run Resource Limits](https://cloud.google.com/run/docs/configuring/memory-limits)
- [GKE Autopilot Pricing](https://cloud.google.com/kubernetes-engine/pricing#autopilot_mode)
- [Cloud Batch Documentation](https://cloud.google.com/batch/docs)

## Decision Log

| Date | Decision | Rationale | Outcome |
|------|----------|-----------|---------|
| 2025-12-17 | Test execution_environment=GEN2 | Low risk, high potential impact | TBD |
| TBD | Consider 8 vCPU tier | If Gen2 doesn't provide 4 cores | TBD |
| TBD | Evaluate GKE Autopilot | If Cloud Run limitations persist | TBD |
