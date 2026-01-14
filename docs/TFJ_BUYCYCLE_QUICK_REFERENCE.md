# Quick Reference: TFJ Buycycle GmbH License

## License Details
- **Licensee**: TFJ Buycycle GmbH
- **License ID**: LIC-TFJ-001
- **Period**: February 1, 2026 - February 1, 2028 (2 years)
- **Software**: MMM Trainer v1.0.0

---

## Your Next Steps (Meshed Data Consulting)

### 1. Create Distribution
```bash
./scripts/prepare_distribution.sh tfj-buycycle v1.0.0
```

### 2. Watermark Code (CRITICAL)
```bash
./scripts/watermark_distribution.sh \
  "TFJ Buycycle GmbH" \
  "LIC-TFJ-001" \
  "2026-02-01" \
  "2028-02-01" \
  "dist/tfj-buycycle-v1.0.0/mmm-app"
```
**Why**: Enables tracking if code is leaked/redistributed

### 3. Regenerate Checksums
```bash
cd dist/tfj-buycycle-v1.0.0
find mmm-app -type f -exec sha256sum {} \; > CHECKSUMS.txt
```

### 4. Complete License Certificate
Edit `dist/tfj-buycycle-v1.0.0/mmm-app/LICENSE_AUTHORIZATION.txt`:
- Fill in all `[TO BE COMPLETED]` fields
- Sign digitally or physically

### 5. Create Final Package
```bash
cd dist
tar -czf tfj-buycycle-v1.0.0-FINAL.tar.gz tfj-buycycle-v1.0.0/
sha256sum tfj-buycycle-v1.0.0-FINAL.tar.gz > tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256
```

### 6. Deliver Securely
- Use encrypted email or secure file transfer
- Include checksum file
- Include signed LICENSE_AUTHORIZATION.txt
- Include `docs/CUSTOMER_DEPLOYMENT_GUIDE_TFJ_BUYCYCLE.md`

**Full Checklist**: See `docs/LICENSOR_NEXT_STEPS_TFJ_BUYCYCLE.md`

---

## Customer's Deployment Process Summary

### Phase 1: Verify Package (30 min)
- Extract archive
- Verify checksums
- Review license documents

### Phase 2: GCP Setup (1-2 days)
- Create project
- Enable APIs (Cloud Run, Artifact Registry, Secret Manager, etc.)
- Create service accounts (web, training, deployer)
- Set up IAM roles
- Create GCS bucket

### Phase 3: Snowflake Setup (1 day)
- Create service user
- Grant database/schema permissions
- Generate key pair for authentication
- Test connection

### Phase 4: GitHub Setup (1 day)
- Create private repository
- Push code
- Set up Workload Identity Federation
- Configure GitHub Secrets (SF_PRIVATE_KEY, OAuth credentials)
- Configure workflows (copy config.example.txt to config.yml)

### Phase 5: Terraform (1 day)
- Configure backend (GCS for state)
- Update tfvars with project details
- Store secrets in Secret Manager

### Phase 6: Dev Deployment (1-2 days)
- Push to dev branch → triggers CI/CD
- Verify deployment
- Test Snowflake connection
- Map data columns
- Run first training job

### Phase 7: Production (1 day)
- Merge dev to main
- Deploy to production
- Configure user access
- Train team

**Timeline**: 1-2 weeks from delivery to production

**Full Guide**: See `docs/CUSTOMER_DEPLOYMENT_GUIDE_TFJ_BUYCYCLE.md`

---

## Watermarking Details

**What it does**:
- Adds unique header to every Python, R, and Bash file
- Format: `# Licensed to: TFJ Buycycle GmbH`
- Includes License ID, dates, unique watermark ID
- Creates WATERMARK_MANIFEST.txt

**Why it matters**:
- If code appears elsewhere, you know the source
- Proves unauthorized redistribution
- Legal evidence for license violation
- Industry-standard practice

**Does it affect functionality?**: NO - watermarks are comments only

---

## Support Contacts

**For Meshed Data Consulting:**
- Licensing: fethu@mesheddata.com
- Technical: support@mesheddata.com

**For TFJ Buycycle GmbH:**
- Support Email: support@mesheddata.com
- License ID: LIC-TFJ-001
- Response Time: 24 hours (business days)

---

## Key Files

| File | Purpose |
|------|---------|
| `scripts/watermark_distribution.sh` | Watermarking automation |
| `docs/LICENSOR_NEXT_STEPS_TFJ_BUYCYCLE.md` | Your complete checklist (9KB) |
| `docs/CUSTOMER_DEPLOYMENT_GUIDE_TFJ_BUYCYCLE.md` | Customer instructions (15KB) |
| `scripts/prepare_distribution.sh` | Distribution creation |

---

## Quick Commands

```bash
# Complete workflow
./scripts/prepare_distribution.sh tfj-buycycle v1.0.0
./scripts/watermark_distribution.sh "TFJ Buycycle GmbH" "LIC-TFJ-001" "2026-02-01" "2028-02-01" "dist/tfj-buycycle-v1.0.0/mmm-app"
cd dist/tfj-buycycle-v1.0.0 && find mmm-app -type f -exec sha256sum {} \; > CHECKSUMS.txt
cd .. && tar -czf tfj-buycycle-v1.0.0-FINAL.tar.gz tfj-buycycle-v1.0.0/
sha256sum tfj-buycycle-v1.0.0-FINAL.tar.gz > tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256
```
