# Licensing Guide for MMM Trainer

This document explains the licensing model for the MMM Trainer application and provides guidance for both license administrators and licensees.

## Table of Contents

1. [Licensing Model Overview](#licensing-model-overview)
2. [Obtaining a License](#obtaining-a-license)
3. [License Grant Process](#license-grant-process)
4. [Distribution Instructions](#distribution-instructions)
5. [Customer Onboarding](#customer-onboarding)
6. [Compliance and Audit](#compliance-and-audit)
7. [FAQ](#faq)

## Licensing Model Overview

The MMM Trainer is **proprietary software** licensed under a custom commercial license. The key characteristics are:

### What Licensees CAN Do:
- ✅ Install and run the software on their own infrastructure
- ✅ Modify the software for their internal use
- ✅ Process their own data with the application
- ✅ Deploy to their own GCP projects
- ✅ Customize configurations and parameters

### What Licensees CANNOT Do:
- ❌ Redistribute the software to third parties
- ❌ Offer the software as a service to others
- ❌ Share the source code with unauthorized parties
- ❌ Create competing products based on this software
- ❌ Transfer their license to another organization

## Obtaining a License

To obtain a license for your organization:

1. **Contact Meshed Data:**
   - Email: fethu@mesheddata.com
   - Subject: "MMM Trainer License Request"

2. **Provide Information:**
   - Organization name and details
   - Intended use case
   - Number of installations needed
   - Deployment environment (GCP project details)
   - Technical contact information

3. **Review Terms:**
   - Review the LICENSE file in this repository
   - Discuss any specific requirements or customizations
   - Clarify support and maintenance expectations

4. **Sign Agreement:**
   - Execute a written license agreement
   - Receive authorization documentation
   - Get access to the software distribution

## License Grant Process

### For License Administrators (Meshed Data Consulting)

When granting a license to a customer:

#### Step 1: Create License Agreement

Create a signed agreement including:
- Customer organization name
- Authorized contact person(s)
- Number of installations/deployments allowed
- License duration (perpetual or time-limited)
- Support terms (if applicable)
- Any custom restrictions or permissions

#### Step 2: Prepare Clean Distribution

Use the distribution script to create a clean copy without git history:

```bash
./scripts/prepare_distribution.sh <customer-name> <version>
```

This creates a clean distribution package in `dist/<customer-name>-<version>/`

#### Step 3: Create Authorization Certificate

Create a file `LICENSE_AUTHORIZATION.txt`:

```text
LICENSE AUTHORIZATION CERTIFICATE

Meshed Data Consulting hereby authorizes:

Organization: [Customer Name]
Contact: [Customer Contact Email]
Date: [Date]
License ID: [Unique ID]

To install and use the MMM Trainer software version [version] under the
terms of the Proprietary Software License Agreement.

Number of Installations: [Number]
Valid Until: [Date or "Perpetual"]

Additional Terms:
- [Any specific terms]

Authorized by:
[Your Name]
[Your Title]
Meshed Data
Date: [Date]

Signature: ___________________________
```

#### Step 4: Package and Deliver

1. Include the LICENSE_AUTHORIZATION.txt in the distribution
2. Package the distribution as a secure archive
3. Deliver via secure channel (encrypted email, secure file transfer)
4. Provide deployment documentation
5. Schedule onboarding session if needed

#### Step 5: Record License

Maintain a license registry with:
- License ID
- Customer name
- Date issued
- Version delivered
- Expiration (if applicable)
- Number of installations
- Contact information

## Distribution Instructions

### Creating Clean Distributions Without Git History

To provide a clean copy of the repository without commit history:

#### Method 1: Using the Distribution Script (Recommended)

```bash
# Run the automated distribution script
./scripts/prepare_distribution.sh <customer-name> <version>

# This creates: dist/<customer-name>-<version>/mmm-app/
```

The script automatically:
- Creates a fresh copy without git history
- Removes development artifacts
- Includes only necessary files
- Adds customer-specific LICENSE_AUTHORIZATION.txt
- Creates documentation package

#### Method 2: Manual Distribution Process

If you need to create a distribution manually:

```bash
# 1. Create a clean export of the current state
git archive --format=tar HEAD | tar -x -C /tmp/mmm-app-export

# 2. Create distribution directory
mkdir -p dist/customer-name-v1.0.0

# 3. Copy files (excluding development artifacts)
rsync -av --exclude='.git' \
          --exclude='.github' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='.pytest_cache' \
          --exclude='node_modules' \
          --exclude='.venv' \
          --exclude='.env' \
          /tmp/mmm-app-export/ \
          dist/customer-name-v1.0.0/mmm-app/

# 4. Add authorization certificate
cp LICENSE_AUTHORIZATION.txt dist/customer-name-v1.0.0/mmm-app/

# 5. Create archive
cd dist
tar -czf customer-name-v1.0.0.tar.gz customer-name-v1.0.0/
```

#### Method 3: Fresh Git Repository for Customer

For customers who want git version control without history:

```bash
# Create clean copy
rsync -av --exclude='.git' /path/to/mmm-app/ /tmp/customer-repo/

# Initialize new git repository
cd /tmp/customer-repo
git init
git add .
git commit -m "Initial deployment - MMM Trainer v1.0.0"

# Package
cd ..
tar -czf customer-name-v1.0.0-with-git.tar.gz customer-repo/
```

### What to Include in Distribution

**Essential Files:**
- LICENSE
- LICENSE_AUTHORIZATION.txt (customer-specific)
- README.md
- ARCHITECTURE.md
- DEVELOPMENT.md
- All application code (`app/`, `r/`, `docker/`, `infra/`)
- Documentation (`docs/`)
- Requirements files
- Makefile
- Configuration examples

**Exclude from Distribution:**
- `.git/` directory (commit history)
- `.github/workflows/config.yml` (your company-specific workflow settings)
- Development environment files (`.env`, `.venv/`)
- Build artifacts (`__pycache__/`, `*.pyc`)
- Test artifacts (`.pytest_cache/`)
- Your internal documentation
- Customer-specific configurations from other deployments

**Include in Distribution:**
- `.github/workflows/` directory (CI/CD workflows for customer use)
- `.github/workflows/config.example.txt` (template for customer configuration)
- Customers will need to create their own `config.yml` with their settings

## Customer Onboarding

### Initial Setup Checklist

Provide customers with this onboarding checklist:

- [ ] Review LICENSE file and LICENSE_AUTHORIZATION.txt
- [ ] Read README.md for project overview
- [ ] Read docs/REQUIREMENTS.md for prerequisites
- [ ] Follow docs/DEPLOYMENT_GUIDE.md for deployment
- [ ] Set up GCP project and enable required APIs
- [ ] Configure Snowflake connection
- [ ] Deploy infrastructure with Terraform
- [ ] Test deployment with sample data
- [ ] Configure authentication and access control
- [ ] Review security best practices
- [ ] Set up monitoring and alerting

### Support and Maintenance

Define support terms in the license agreement:
- Bug fix support
- Security updates
- Version upgrades
- Technical assistance
- SLA (if applicable)

## Compliance and Audit

### License Compliance Monitoring

For license administrators:

1. **Regular Check-ins:**
   - Schedule periodic reviews with licensees
   - Verify compliance with license terms
   - Confirm number of installations hasn't exceeded authorization

2. **Audit Rights:**
   - Reserve the right to audit usage (include in license agreement)
   - Define audit procedures and notice requirements
   - Specify remediation for non-compliance

3. **Version Tracking:**
   - Maintain records of which version was delivered to each customer
   - Track security patches and updates
   - Notify customers of important updates

### For Licensees

To maintain compliance:

1. **Keep Records:**
   - Maintain copy of LICENSE_AUTHORIZATION.txt
   - Document all installations
   - Track who has access to the software

2. **Restrict Access:**
   - Limit access to authorized personnel only
   - Use appropriate access controls on your infrastructure
   - Ensure contractors sign NDAs if they access the software

3. **Report Changes:**
   - Notify Meshed Data Consulting if you need additional installations
   - Report any security incidents involving the software
   - Coordinate with Meshed Data Consulting before major modifications

## FAQ

### For License Administrators

**Q: How do I handle license renewals?**
A: For time-limited licenses, contact customers 60 days before expiration. Issue new LICENSE_AUTHORIZATION.txt with updated date.

**Q: Can I grant licenses to competitors?**
A: This is a business decision. Consider including non-compete clauses or market restrictions in specific license agreements.

**Q: What if a customer wants to transfer their license?**
A: License transfers require written approval. Evaluate the new licensee, update LICENSE_AUTHORIZATION.txt, and charge transfer fee if applicable.

**Q: How do I handle security vulnerabilities?**
A: Create security patches, notify all licensees promptly, provide updated code, and document the fix. Consider automated update mechanisms for future.

### For Licensees

**Q: Can I modify the source code?**
A: Yes, for internal use only. Modifications remain subject to the license terms and cannot be distributed.

**Q: What if I need more installations?**
A: Contact fethu@mesheddata.com to request an amendment to your license agreement.

**Q: Can I use this in a subsidiary or affiliated company?**
A: Only if explicitly authorized in your LICENSE_AUTHORIZATION.txt. Contact Meshed Data Consulting to extend the license.

**Q: What happens if my company is acquired?**
A: Notify Meshed Data Consulting immediately. License transfer requires approval and may require new agreement.

**Q: Can I show the software to potential partners?**
A: Only for evaluation purposes with prior approval from Meshed Data Consulting and appropriate NDAs in place.

**Q: What support is included?**
A: Support terms are specified in your license agreement. Contact your designated support channel for assistance.

**Q: Can I contribute improvements back to Meshed Data Consulting?**
A: Yes, you can propose improvements, but intellectual property terms must be negotiated separately.

## Contact Information

For licensing questions:
- Email: fethu@mesheddata.com
- Website: https://mesheddata.com

For technical support (licensed customers only):
- See your LICENSE_AUTHORIZATION.txt for support contact information

---

**Document Version:** 1.0  
**Last Updated:** January 2026  
**Applies to:** MMM Trainer v1.0.0+
