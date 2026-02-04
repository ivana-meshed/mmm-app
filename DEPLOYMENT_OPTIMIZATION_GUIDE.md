# Deployment Optimization Guide: Reducing Cloud Run Costs

## Problem Statement

**Current State:**
- 150+ deployments/month (5/day)
- Deployment overhead: €72-90/month
- Web service cost: €115/month

**Target State:**
- 30 deployments/month (1/day)
- Deployment overhead: €15-20/month
- Web service cost: €45-55/month
- **Savings: €60-70/month (€720-840/year)**

---

## Strategy 1: Optimize CI/CD Triggers

### Current Deployment Triggers (Assumed)

**From ci-dev.yml:**
```yaml
on:
  push:
    branches: [ dev, feat-*, copilot/* ]
```

Every push to these branches triggers deployment = TOO FREQUENT

### Recommended Changes

#### Option A: Deploy Only on PR Merge (BEST)

```yaml
# .github/workflows/ci-dev.yml
on:
  push:
    branches: [ dev ]  # Only deploy when PR is merged to dev
  # Remove automatic deploy on feat-* and copilot/* branches
```

**Impact:**
- Deployments: 150/month → 20-30/month
- Savings: €75/month

#### Option B: Manual Deployment Approval

```yaml
on:
  workflow_dispatch:  # Manual trigger only
  push:
    branches: [ dev ]
    paths-ignore:      # Don't deploy for docs-only changes
      - '**.md'
      - 'docs/**'
```

**Impact:**
- Full control over deployments
- Deploy only when necessary
- Savings: €80/month

#### Option C: Time-Based Deployment Windows

```yaml
on:
  push:
    branches: [ dev ]
  schedule:
    - cron: '0 9 * * 1-5'  # Deploy Monday-Friday at 9 AM only
```

**Impact:**
- Max 5 deployments/week = 20/month
- Savings: €70/month

---

## Strategy 2: Implement Revision Management

### Configure Maximum Revisions

**In Terraform (main.tf):**

```terraform
resource "google_cloud_run_v2_service" "web" {
  # ... existing configuration ...
  
  template {
    # ... existing template ...
    
    # Add revision management
    revision = "${var.service_name}-${var.image_tag}"
    
    # Configure traffic to minimize overlap
    max_instance_request_concurrency = 80
  }
  
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
  
  # Limit number of revisions
  lifecycle {
    create_before_destroy = true
  }
}
```

**Manual Cleanup Script:**

```bash
#!/bin/bash
# scripts/cleanup_cloud_run_revisions.sh

PROJECT_ID="datawarehouse-422511"
REGION="europe-west1"
SERVICE_NAME="mmm-app-web"
KEEP_LAST_N=5

echo "Cleaning up old Cloud Run revisions for $SERVICE_NAME"

# Get all revisions
revisions=$(gcloud run revisions list \
  --service=$SERVICE_NAME \
  --region=$REGION \
  --format="value(name)" \
  --sort-by="~metadata.creationTimestamp" \
  --limit=100)

# Skip first N (keep them)
revisions_to_delete=$(echo "$revisions" | tail -n +$((KEEP_LAST_N + 1)))

# Delete old revisions
for revision in $revisions_to_delete; do
  echo "Deleting revision: $revision"
  gcloud run revisions delete $revision \
    --service=$SERVICE_NAME \
    --region=$REGION \
    --quiet
done

echo "Cleanup complete. Kept $KEEP_LAST_N most recent revisions."
```

**Impact:**
- Reduces clutter
- Prevents accidental traffic to old revisions
- Savings: €10-15/month

---

## Strategy 3: Optimize Traffic Migration

### Current Behavior (Default)

Cloud Run gradually migrates traffic:
- 0% → 25% → 50% → 75% → 100%
- Old revision stays up until fully drained
- Can take 15-30 minutes

### Optimize with Faster Migration

**Option A: Immediate Traffic Cutover**

```bash
# After deployment, immediately route 100% traffic to new revision
gcloud run services update-traffic mmm-app-web \
  --region=europe-west1 \
  --to-latest
```

**Option B: Configure in Terraform**

```terraform
traffic {
  type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
  percent = 100
  # Immediate cutover, no gradual migration
}
```

**Impact:**
- Reduces deployment overlap time
- Faster deployments
- Savings: €15-20/month

**Risk:** Immediate cutover = no gradual rollout. Monitor for issues.

---

## Strategy 4: Implement Local Development

### Current Problem

Developers deploy to cloud to test changes = high deployment frequency

### Solution: Docker Compose Local Environment

**Create docker-compose.yml:**

```yaml
version: '3.8'
services:
  web:
    build:
      context: .
      dockerfile: docker/Dockerfile.web
    ports:
      - "8080:8080"
    environment:
      - PORT=8080
      - PROJECT_ID=datawarehouse-422511
      - GCS_BUCKET=mmm-app-output
      - TRAINING_JOB_NAME=mmm-app-training
    volumes:
      - ./app:/app/app
      - ~/.config/gcloud:/root/.config/gcloud
    command: streamlit run app/streamlit_app.py --server.port=8080
```

**Developer Workflow:**

```bash
# 1. Develop locally
docker-compose up

# 2. Test changes at http://localhost:8080

# 3. Deploy only when confident
git push origin dev
```

**Impact:**
- Reduces cloud deployments by 70-80%
- Faster iteration (no deploy wait time)
- Savings: €60/month

---

## Strategy 5: Staging Environment Optimization

### Current Issue

If mmm-app-dev is used for testing = frequent deployments

### Recommended Structure

**Environment Separation:**
```
Production (mmm-app):
  - Branch: main
  - Deploy frequency: 2-4/month
  - Deploy on: Release tags only

Staging (mmm-app-dev):
  - Branch: dev
  - Deploy frequency: 10-15/month  
  - Deploy on: PR merge to dev

Development (local):
  - Branch: feature branches
  - Deploy frequency: 0 (local only)
  - Deploy on: Never (use docker-compose)
```

**Impact:**
- Reduces dev deployments: 120/month → 15/month
- Reduces prod deployments: 30/month → 4/month
- Total: 150/month → 19/month
- Savings: €75/month

---

## Strategy 6: Implement Preview Deployments (Optional)

### For Feature Branch Testing

Instead of deploying to main dev environment, create temporary preview deployments:

**GitHub Actions:**

```yaml
name: Preview Deployment
on:
  pull_request:
    types: [opened, synchronize]
    
jobs:
  preview:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy preview
        run: |
          # Create temporary Cloud Run service
          SERVICE_NAME="mmm-app-preview-pr-${{ github.event.pull_request.number }}"
          
          gcloud run deploy $SERVICE_NAME \
            --image=$IMAGE \
            --region=europe-west1 \
            --allow-unauthenticated \
            --tag=pr-${{ github.event.pull_request.number }}
          
      - name: Comment PR with preview URL
        uses: actions/github-script@v6
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              body: 'Preview: https://pr-${{ github.event.pull_request.number }}---mmm-app-dev.run.app'
            })
```

**Cleanup:**

```yaml
on:
  pull_request:
    types: [closed]
    
jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - name: Delete preview service
        run: |
          gcloud run services delete mmm-app-preview-pr-${{ github.event.pull_request.number }} \
            --region=europe-west1 \
            --quiet
```

**Impact:**
- Keeps main dev environment stable
- Reduces dev deployment frequency
- Adds cost for preview deployments (but short-lived)
- Net savings: €20-30/month

---

## Implementation Plan

### Phase 1: Quick Wins (Week 1)

**Day 1-2: Optimize CI/CD Triggers**
- [ ] Review current workflow triggers
- [ ] Update ci-dev.yml to deploy only on PR merge
- [ ] Remove automatic deployment on feature branches
- [ ] Expected savings: €60/month

**Day 3-4: Implement Revision Cleanup**
- [ ] Create cleanup script
- [ ] Run manually to clean existing revisions
- [ ] Add to cron job or GitHub Actions
- [ ] Expected savings: €10/month

**Day 5: Configure Traffic Migration**
- [ ] Update Terraform with immediate cutover
- [ ] Test deployment behavior
- [ ] Expected savings: €10/month

**Week 1 Total: €80/month savings**

### Phase 2: Infrastructure (Week 2)

**Day 1-2: Set Up Local Development**
- [ ] Create docker-compose.yml
- [ ] Document local setup process
- [ ] Train team on local development

**Day 3-5: Optimize Environment Strategy**
- [ ] Define clear environment purposes
- [ ] Update deployment policies
- [ ] Communicate to team

**Week 2 Total: Additional €20/month savings**

### Phase 3: Monitoring & Validation (Weeks 3-4)

**Week 3:**
- [ ] Monitor deployment frequency
- [ ] Track daily Cloud Run costs
- [ ] Validate savings

**Week 4:**
- [ ] Compare month-over-month costs
- [ ] Adjust policies if needed
- [ ] Document lessons learned

---

## Monitoring and Validation

### Deployment Frequency Tracking

**Create monitoring script:**

```bash
#!/bin/bash
# scripts/track_deployment_frequency.sh

echo "Deployment Frequency Analysis"
echo "=============================="

for SERVICE in "mmm-app-web" "mmm-app-dev-web"; do
  echo ""
  echo "Service: $SERVICE"
  
  # Last 30 days
  revisions=$(gcloud run revisions list \
    --service=$SERVICE \
    --region=europe-west1 \
    --format="value(metadata.creationTimestamp)" \
    --filter="metadata.creationTimestamp>=$(date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
    --limit=1000)
  
  count=$(echo "$revisions" | wc -l)
  per_day=$(echo "scale=1; $count / 30" | bc)
  
  echo "  Last 30 days: $count deployments"
  echo "  Average: $per_day per day"
done
```

### Cost Tracking

**Use enhanced cost script:**

```bash
# Run weekly
./scripts/get_cloud_run_costs.sh

# Look for trends:
# - Deployment frequency decreasing?
# - Daily costs decreasing?
# - Web service % of total decreasing?
```

### Success Metrics

**Target After Optimization:**
- Deployments/month: ≤30 (currently 150)
- Web service cost: ≤€60/month (currently €115)
- Total Cloud Run: ≤€85/month (currently €137)
- Deployment overhead: ≤€15/month (currently €75)

---

## Rollback Procedures

If issues occur after optimization:

### Rollback CI/CD Changes

```bash
# Revert workflow file
git checkout HEAD~1 -- .github/workflows/ci-dev.yml
git commit -m "Rollback: Revert CI/CD optimization"
git push
```

### Restore Original Configuration

```bash
# Re-enable automatic deployments
# Edit ci-dev.yml to add back feat-* and copilot/* triggers
```

### Emergency Deployment

```bash
# Manual deployment if needed
gcloud run deploy mmm-app-dev-web \
  --image=europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:latest \
  --region=europe-west1
```

---

## Additional Considerations

### Team Communication

**Before implementing:**
- [ ] Communicate changes to team
- [ ] Document new deployment process
- [ ] Provide training on local development
- [ ] Set expectations for deployment frequency

### Documentation Updates

- [ ] Update README.md with deployment process
- [ ] Document local development setup
- [ ] Create runbook for deployments
- [ ] Update CONTRIBUTING.md if exists

### Gradual Rollout

**Don't change everything at once:**
1. Week 1: Optimize dev environment only
2. Week 2: Monitor and validate
3. Week 3: Apply learnings to prod
4. Week 4: Fine-tune and document

---

## Cost Savings Summary

| Optimization | Implementation | Savings/Month | Risk |
|--------------|----------------|---------------|------|
| **Optimize CI/CD triggers** | 1 day | €60 | Low |
| **Revision cleanup** | 2 days | €10 | Low |
| **Traffic migration** | 1 day | €10 | Medium |
| **Local development** | 3 days | €20 | Low |
| **Environment strategy** | Ongoing | €20 | Low |
| **Total** | 1 week | **€120** | **Low** |

**Combined with previous optimizations:**
- Deployment optimization: €120/month
- Resource optimization: €60/month (1 vCPU, 2GB)
- Warmup removal: €4/month
- **Total: €184/month (€2,208/year)**

**Final projected cost:**
- Current: €137/month
- After all optimizations: €45-50/month
- **Savings: 63-67%**

---

## Next Steps

1. Review this guide with the team
2. Choose optimization strategies to implement
3. Start with Phase 1 (quick wins)
4. Monitor and validate results
5. Iterate based on findings

**Ready to reduce deployment costs by €120/month? Let's begin!**
