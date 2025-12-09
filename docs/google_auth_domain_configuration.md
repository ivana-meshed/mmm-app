# Google Authentication Domain Configuration

## Overview

The MMM application uses Google OAuth for authentication and restricts access to users from specific email domains. This document explains how to configure one or more allowed domains.

## Configuration

### Environment Variable

The allowed domains are configured via the `ALLOWED_DOMAINS` environment variable, which accepts a **comma-separated list** of domain names.

**Examples:**

Single domain:
```bash
ALLOWED_DOMAINS=mesheddata.com
```

Multiple domains:
```bash
ALLOWED_DOMAINS=mesheddata.com,example.com,another-domain.com
```

### Setting the Environment Variable

#### Option 1: Via Terraform (Recommended for Production)

Add the environment variable to your Cloud Run service configuration in `infra/terraform/main.tf`:

```hcl
resource "google_cloud_run_service" "web_service" {
  # ... other configuration ...
  
  template {
    spec {
      containers {
        # ... other env vars ...
        
        env {
          name  = "ALLOWED_DOMAINS"
          value = "mesheddata.com,example.com"
        }
      }
    }
  }
}
```

Then apply the changes:
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

#### Option 2: Via GitHub Actions Workflow

Add the environment variable to your workflow file (`.github/workflows/ci.yml` or `.github/workflows/ci-dev.yml`):

```yaml
env:
  # ... other variables ...
  ALLOWED_DOMAINS: "mesheddata.com,example.com"
```

Or pass it as a Terraform variable:
```yaml
env:
  TF_VAR_allowed_domains: "mesheddata.com,example.com"
```

Then add it to `infra/terraform/variables.tf`:
```hcl
variable "allowed_domains" {
  type        = string
  default     = "mesheddata.com"
  description = "Comma-separated list of allowed email domains for authentication"
}
```

And use it in `main.tf`:
```hcl
env {
  name  = "ALLOWED_DOMAINS"
  value = var.allowed_domains
}
```

#### Option 3: Direct Cloud Run Update (Quick Testing)

For quick testing or temporary changes:

```bash
gcloud run services update mmm-app-web \
  --region=europe-west1 \
  --set-env-vars="ALLOWED_DOMAINS=mesheddata.com,example.com"
```

### Backward Compatibility

For backward compatibility, the application still supports the legacy `ALLOWED_DOMAIN` environment variable (singular, single domain). If both are set, domains from both variables are combined.

**Example:**
```bash
ALLOWED_DOMAIN=mesheddata.com
ALLOWED_DOMAINS=example.com,another-domain.com
```

This would allow users from all three domains: `mesheddata.com`, `example.com`, and `another-domain.com`.

## How It Works

1. The `ALLOWED_DOMAINS` environment variable is read and parsed in `app/config/settings.py`
2. The domains are split by commas, trimmed, and converted to lowercase
3. The `require_login_and_domain()` function in `app/app_shared.py` validates user emails against the allowed domains
4. If a user's email domain doesn't match any allowed domain, they are denied access

## User Experience

### Single Domain
When only one domain is configured, users see:
> Sign in with your @mesheddata.com Google account to continue.

If access is denied:
> This app is restricted to @mesheddata.com accounts.

### Multiple Domains
When multiple domains are configured, users see:
> Sign in with your Google account from one of these domains: @mesheddata.com, @example.com

If access is denied:
> This app is restricted to accounts from these domains: @mesheddata.com, @example.com

## Google OAuth Console Configuration

**Important:** Simply adding domains to the environment variable is not sufficient. You must also configure your Google OAuth consent screen to allow the domains.

### Steps to Allow Multiple Domains in Google OAuth:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project
3. Navigate to **APIs & Services** → **OAuth consent screen**
4. Under **Authorized domains**, add all domains you want to allow:
   - `mesheddata.com`
   - `example.com`
   - etc.
5. Save the changes

**Note:** For internal use only, you can set the OAuth consent screen to "Internal" which restricts access to users in your Google Workspace organization.

## Testing

After configuration, test with:

1. A user with an allowed domain (e.g., `user@mesheddata.com`) - should have access
2. A user with a newly added domain (e.g., `user@example.com`) - should have access
3. A user with a non-allowed domain (e.g., `user@blocked.com`) - should be denied

## Troubleshooting

### Users from new domain are denied access

**Check:**
1. Is `ALLOWED_DOMAINS` set correctly? Verify with:
   ```bash
   gcloud run services describe mmm-app-web --region=europe-west1 --format="value(spec.template.spec.containers[0].env)"
   ```
2. Are the domains added to Google OAuth consent screen?
3. Did the Cloud Run service restart after the environment variable change?

### Environment variable not taking effect

Force a new revision:
```bash
gcloud run services update mmm-app-web \
  --region=europe-west1 \
  --update-env-vars="ALLOWED_DOMAINS=mesheddata.com,example.com"
```

## Example: Complete Setup for Two Domains

### 1. Update Terraform Variables

`infra/terraform/variables.tf`:
```hcl
variable "allowed_domains" {
  type        = string
  default     = "mesheddata.com,newcompany.com"
  description = "Comma-separated list of allowed email domains"
}
```

### 2. Update Terraform Main Config

`infra/terraform/main.tf`:
```hcl
resource "google_cloud_run_service" "web_service" {
  # ... existing config ...
  
  template {
    spec {
      containers {
        # ... other env vars ...
        
        env {
          name  = "ALLOWED_DOMAINS"
          value = var.allowed_domains
        }
      }
    }
  }
}
```

### 3. Deploy

```bash
cd infra/terraform
terraform plan -var-file="envs/prod.tfvars"
terraform apply -var-file="envs/prod.tfvars"
```

### 4. Verify

```bash
gcloud run services describe mmm-app-web --region=europe-west1 --format="get(spec.template.spec.containers[0].env)"
```

You should see `ALLOWED_DOMAINS=mesheddata.com,newcompany.com` in the output.

## Security Considerations

1. **Domain Verification**: Only add domains you control and trust
2. **Google OAuth**: Ensure domains are properly configured in Google OAuth consent screen
3. **Regular Review**: Periodically review and audit allowed domains
4. **Least Privilege**: Only add domains that require access to the application

## See Also

- [Google OAuth 2.0 Documentation](https://developers.google.com/identity/protocols/oauth2)
- [Cloud Run Environment Variables](https://cloud.google.com/run/docs/configuring/environment-variables)
- Main configuration: `app/config/settings.py`
- Authentication logic: `app/app_shared.py` → `require_login_and_domain()`
