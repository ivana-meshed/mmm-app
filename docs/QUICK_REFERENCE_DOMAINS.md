# How to Configure Additional Domains - Quick Reference

## For Production (via Terraform)

### Step 1: Edit the tfvars file
Edit `infra/terraform/envs/prod.tfvars` and add/uncomment:

```hcl
allowed_domains = "mesheddata.com,yournewdomain.com"
```

### Step 2: Apply Terraform changes
```bash
cd infra/terraform
terraform plan -var-file="envs/prod.tfvars"
terraform apply -var-file="envs/prod.tfvars"
```

### Step 3: Verify
```bash
gcloud run services describe mmm-app-web \
  --region=europe-west1 \
  --format="value(spec.template.spec.containers[0].env[?(@.name=='ALLOWED_DOMAINS')].value)"
```

Expected output: `mesheddata.com,yournewdomain.com`

---

## For Development (via Terraform)

### Step 1: Edit dev tfvars
Edit `infra/terraform/envs/dev.tfvars` and add/uncomment:

```hcl
allowed_domains = "mesheddata.com,yournewdomain.com"
```

### Step 2: Apply Terraform changes
```bash
cd infra/terraform
terraform workspace select dev
terraform plan -var-file="envs/dev.tfvars"
terraform apply -var-file="envs/dev.tfvars"
```

---

## Alternative: GitHub Actions Workflow

If you prefer to manage this via GitHub Actions, you can add it as a workflow environment variable.

Edit `.github/workflows/ci.yml` or `.github/workflows/ci-dev.yml`:

```yaml
jobs:
  ci:
    runs-on: ubuntu-latest
    env:
      # ... existing env vars ...
      TF_VAR_allowed_domains: "mesheddata.com,yournewdomain.com"
```

Then commit and push to trigger the workflow.

---

## Alternative: Direct Cloud Run Update (Quick Test Only)

For quick testing (changes will be overwritten by next deployment):

```bash
gcloud run services update mmm-app-web \
  --region=europe-west1 \
  --set-env-vars="ALLOWED_DOMAINS=mesheddata.com,yournewdomain.com"
```

---

## Important: Google OAuth Console Configuration

**You must also configure the Google OAuth consent screen!**

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to **APIs & Services** → **OAuth consent screen**
3. Under **Authorized domains**, add your new domain(s)
4. Click **Save**

Without this step, users from the new domain will not be able to authenticate even if the application allows it.

---

## Testing

After deployment, test with users from:
1. ✅ `user@mesheddata.com` (existing domain)
2. ✅ `user@yournewdomain.com` (new domain)
3. ❌ `user@unauthorized.com` (should be blocked)

---

## Rollback

If you need to revert to single domain:

```hcl
# In tfvars file:
allowed_domains = "mesheddata.com"
```

Or remove the line to use the default.

---

## Common Issues

### "Access denied" for new domain users
**Check:**
- Is the domain added to Google OAuth consent screen?
- Did Terraform apply successfully?
- Is the Cloud Run service using the latest revision?

### Environment variable not found
**Solution:**
```bash
# Force a new revision
gcloud run services update mmm-app-web --region=europe-west1
```

---

For detailed documentation, see:
- [docs/google_auth_domain_configuration.md](./google_auth_domain_configuration.md)
- [README.md](../README.md#google-authentication)
