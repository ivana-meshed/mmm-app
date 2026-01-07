# Complete Guide: Licensing Your Repository for Commercial Use

This guide provides step-by-step instructions for licensing your MMM Trainer repository to companies for installation on their infrastructure while preventing redistribution.

## Table of Contents

1. [Overview](#overview)
2. [What Has Been Implemented](#what-has-been-implemented)
3. [Step-by-Step Process for Licensing](#step-by-step-process-for-licensing)
4. [Creating Clean Distributions](#creating-clean-distributions)
5. [Managing Licensed Customers](#managing-licensed-customers)
6. [Legal Considerations](#legal-considerations)
7. [Best Practices](#best-practices)

---

## Overview

### Licensing Model

Your repository now implements a **proprietary licensing model** that:

✅ Allows companies to install and use the software on their infrastructure  
✅ Requires explicit written authorization from you (Meshed Data)  
✅ Prevents licensees from redistributing the software to others  
✅ Restricts use to internal business purposes only  
✅ Allows you to control who has access to your software  
✅ Protects your intellectual property  

### Key Documents Created

1. **LICENSE** - Proprietary license agreement (replaces open source license)
2. **docs/LICENSING.md** - Comprehensive licensing guide for administrators and customers
3. **docs/LICENSING_FAQ.md** - Frequently asked questions about licensing
4. **docs/LICENSE_AGREEMENT_TEMPLATE.txt** - Customizable legal agreement template
5. **docs/LICENSE_HEADER_TEMPLATE.md** - Source code header templates
6. **scripts/prepare_distribution.sh** - Automated distribution creation tool
7. **README.md** - Updated to reflect proprietary licensing model

---

## What Has Been Implemented

### 1. Proprietary LICENSE File

A comprehensive proprietary software license that:
- Grants limited, non-exclusive, non-transferable rights
- Requires written authorization from Meshed Data
- Explicitly prohibits redistribution
- Protects confidential and proprietary information
- Includes warranty disclaimers and liability limitations

**Location:** `LICENSE` (root of repository)

### 2. Licensing Documentation

Complete documentation including:
- **LICENSING.md**: Administrator and customer guide
- **LICENSING_FAQ.md**: Common questions and answers
- **LICENSE_AGREEMENT_TEMPLATE.txt**: Customizable legal agreement
- **LICENSE_HEADER_TEMPLATE.md**: Source code copyright headers

**Location:** `docs/` directory

### 3. Distribution Script

An automated bash script that creates clean distributions:
- Removes git history
- Excludes development artifacts
- Creates LICENSE_AUTHORIZATION certificate
- Generates checksums for verification
- Packages everything into a deliverable archive

**Location:** `scripts/prepare_distribution.sh`

### 4. Updated README

The README.md now clearly states:
- This is proprietary software (NOT open source)
- How to obtain a license
- Contact information for licensing inquiries
- Links to licensing documentation

---

## Step-by-Step Process for Licensing

### Phase 1: Prepare Your Repository

#### Step 1: Review and Customize Legal Documents

**IMPORTANT:** Have a lawyer review these documents before using them.

1. **Review the LICENSE file:**
   ```bash
   cat LICENSE
   ```
   - Update `[YOUR JURISDICTION]` in the Governing Law section
   - Update contact email if different from licensing@mesheddata.com
   - Have legal counsel approve the terms

2. **Customize the LICENSE_AGREEMENT_TEMPLATE.txt:**
   ```bash
   cat docs/LICENSE_AGREEMENT_TEMPLATE.txt
   ```
   - Fill in your company address and details
   - Adjust terms based on legal advice
   - Define your standard pricing and support tiers

3. **Review other documentation:**
   - Read through LICENSING.md for completeness
   - Customize LICENSING_FAQ.md with your specific policies
   - Update contact information throughout

#### Step 2: Add License Headers to Source Files (Optional but Recommended)

You can add copyright headers to your source files using the templates in `docs/LICENSE_HEADER_TEMPLATE.md`.

For Python files:
```python
# Copyright (c) 2024-2026 Meshed Data
# All Rights Reserved.
#
# This file is part of the MMM Trainer proprietary software.
# Unauthorized copying, modification, distribution, or use of this
# software, via any medium, is strictly prohibited.
#
# Licensed under the Proprietary Software License Agreement.
# See LICENSE file in the project root for license information.
```

You can add headers manually or create a script to add them automatically.

#### Step 3: Update Git History (Optional)

If you want a completely clean start:

**Option A: Keep existing history (easier)**
- Just commit the new license files
- History remains but is only in your private repository

**Option B: Clean history (more complex)**
- Create a new repository with only the latest code
- This is complex and should be done carefully
- Consider just using the distribution script for customers instead

**Recommendation:** Keep your private repository as-is with full history. Use the distribution script to create clean copies for customers.

#### Step 4: Set Up License Registry

Create a spreadsheet or database to track licenses:

| License ID | Customer Name | Contact Email | Date Issued | Version | Installations | Expiration | Support Tier | Status |
|------------|---------------|---------------|-------------|---------|---------------|------------|--------------|--------|
| LIC-001    | Acme Corp     | john@acme.com | 2026-01-15  | v1.0.0  | 2             | Perpetual  | Standard     | Active |

Store this securely and keep it updated.

---

### Phase 2: License to a Customer

#### Step 1: Receive License Request

When a company contacts you:

1. **Collect Information:**
   - Company legal name and address
   - Contact person name and email
   - Intended use case
   - Number of installations needed
   - Deployment details (GCP projects, regions, etc.)
   - Support requirements

2. **Assess Request:**
   - Verify the company is legitimate
   - Ensure use case aligns with your terms
   - Check for any conflicts of interest
   - Determine appropriate pricing

#### Step 2: Negotiate Terms

Discuss and agree on:
- Number of installations allowed
- License duration (perpetual vs. annual)
- Pricing
- Support level
- Payment terms
- Any special requirements

#### Step 3: Create License Agreement

1. **Start with template:**
   ```bash
   cp docs/LICENSE_AGREEMENT_TEMPLATE.txt agreements/customer-name-agreement.txt
   ```

2. **Fill in details:**
   - Customer legal name and address
   - License fee and payment terms
   - Number of installations
   - License duration
   - Support terms
   - Special provisions

3. **Have both parties review:**
   - Your legal counsel reviews
   - Customer legal counsel reviews
   - Negotiate any changes
   - Finalize terms

#### Step 4: Execute Agreement

1. **Sign the agreement:**
   - Both parties sign
   - Exchange signed copies
   - Store securely

2. **Receive payment:**
   - Invoice customer
   - Wait for payment (or sign and pay simultaneously)

3. **Record in license registry:**
   - Add entry with all details
   - Assign unique License ID

#### Step 5: Create Distribution Package

Use the automated script:

```bash
# Make sure you're in the repository root
cd /path/to/mmm-app

# Run the distribution script
./scripts/prepare_distribution.sh acme-corp v1.0.0

# This creates: dist/acme-corp-v1.0.0/
```

The script will:
- Create a clean copy without git history
- Remove development artifacts
- Create LICENSE_AUTHORIZATION.txt template
- Generate checksums
- Package everything into a .tar.gz archive

#### Step 6: Complete Authorization Certificate

Edit the LICENSE_AUTHORIZATION.txt file:

```bash
cd dist/acme-corp-v1.0.0/mmm-app
nano LICENSE_AUTHORIZATION.txt
```

Fill in all the `[TO BE COMPLETED]` fields:
- Customer contact email
- Unique License ID (from your registry)
- Number of installations
- Expiration date or "Perpetual"
- Any additional terms
- Your name and title
- Current date
- Sign (or digitally sign)

Example completed certificate:
```
LICENSE AUTHORIZATION CERTIFICATE

Meshed Data hereby authorizes:

Organization: Acme Corporation
Contact: john.doe@acmecorp.com
Date: 2026-01-15
License ID: LIC-001

To install and use the MMM Trainer software version v1.0.0 under the
terms of the Proprietary Software License Agreement.

Number of Installations: 2
Valid Until: Perpetual

Additional Terms:
- Standard email support included
- Security updates provided for 12 months
- May deploy to acme-prod and acme-dev GCP projects

Authorized by:
Jane Smith
CEO
Meshed Data
Date: 2026-01-15

Signature: [Signature or Digital Signature]
```

#### Step 7: Verify Distribution Package

Before sending to customer:

1. **Review contents:**
   ```bash
   cd dist/acme-corp-v1.0.0
   ls -la mmm-app/
   ```

2. **Verify important files:**
   - LICENSE file is present
   - LICENSE_AUTHORIZATION.txt is completed
   - README.md is included
   - All documentation is present
   - No .git directory
   - No development files (.env, .venv, etc.)

3. **Test if possible:**
   - Extract to a clean location
   - Try building Docker images
   - Verify documentation is complete

#### Step 8: Deliver to Customer

1. **Secure delivery method:**
   - Encrypted email attachment (for smaller packages)
   - Secure file transfer service (for larger packages)
   - Cloud storage with time-limited access link
   - Physical media for highly sensitive deployments

2. **Include in delivery:**
   - Distribution archive: `acme-corp-v1.0.0.tar.gz`
   - Archive checksum (from CHECKSUMS.txt or shown by script)
   - Installation instructions (refer to docs/DEPLOYMENT_GUIDE.md)
   - Your support contact information

3. **Email to customer:**
   ```
   Subject: MMM Trainer Software Distribution - License LIC-001

   Dear [Customer Contact],

   Thank you for licensing the MMM Trainer software. Attached/linked is 
   your authorized distribution package.

   Package Details:
   - Version: v1.0.0
   - License ID: LIC-001
   - File: acme-corp-v1.0.0.tar.gz
   - Checksum (SHA-256): [checksum]

   Please verify the checksum after download.

   Getting Started:
   1. Extract the archive
   2. Review LICENSE and LICENSE_AUTHORIZATION.txt
   3. Follow the deployment guide in mmm-app/docs/DEPLOYMENT_GUIDE.md
   4. Contact us if you need assistance: [support contact]

   Your license authorizes [X] installations and includes [support level].

   Best regards,
   [Your Name]
   Meshed Data
   ```

#### Step 9: Customer Onboarding

1. **Schedule onboarding call (optional but recommended):**
   - Review license terms
   - Walk through deployment guide
   - Answer questions
   - Provide support contact information

2. **Provide ongoing support:**
   - Respond to support requests per agreement
   - Provide security updates as needed
   - Check in periodically on usage

3. **Monitor compliance:**
   - Verify customer is using as authorized
   - Ensure they haven't exceeded installation limits
   - Address any compliance issues promptly

---

## Creating Clean Distributions

### Using the Automated Script

The easiest way to create distributions:

```bash
# Syntax
./scripts/prepare_distribution.sh <customer-name> <version>

# Example
./scripts/prepare_distribution.sh acme-corp v1.0.0

# Output location
ls -la dist/acme-corp-v1.0.0/
```

### What the Script Does

1. **Exports code:** Uses `git archive` to get clean copy of current HEAD
2. **Copies files:** Uses rsync to copy files excluding:
   - .git directory (no history)
   - .github workflows (your CI/CD)
   - Build artifacts (__pycache__, .pyc, etc.)
   - Development files (.venv, .env, etc.)
   - Node_modules and other dependencies
3. **Creates LICENSE_AUTHORIZATION.txt:** Template file for you to complete
4. **Creates README_DISTRIBUTION.txt:** Instructions for customer
5. **Generates checksums:** CHECKSUMS.txt with file hashes
6. **Creates archive:** .tar.gz file ready for delivery

### Manual Distribution (If Needed)

If you need to create a distribution manually:

```bash
# Export current code
git archive --format=tar HEAD | tar -x -C /tmp/mmm-export

# Create distribution directory
mkdir -p dist/customer-v1.0.0/mmm-app

# Copy with exclusions
rsync -av --exclude='.git' --exclude='.github' \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' --exclude='.env' \
  /tmp/mmm-export/ dist/customer-v1.0.0/mmm-app/

# Add authorization certificate
cp LICENSE_AUTHORIZATION.txt dist/customer-v1.0.0/mmm-app/

# Create archive
cd dist
tar -czf customer-v1.0.0.tar.gz customer-v1.0.0/

# Cleanup
rm -rf /tmp/mmm-export
```

### Version Control for Customers (Optional)

If customers want git version control:

```bash
# Create clean copy
rsync -av --exclude='.git' mmm-app/ /tmp/customer-repo/

# Initialize fresh git repo
cd /tmp/customer-repo
git init
git add .
git commit -m "Initial deployment - MMM Trainer v1.0.0"

# Package
cd ..
tar -czf customer-v1.0.0-with-git.tar.gz customer-repo/
```

This gives customers git capabilities without your history.

---

## Managing Licensed Customers

### License Registry

Maintain a detailed record of all licenses:

**Minimum information to track:**
- License ID (unique)
- Customer name and legal entity
- Contact person and email
- Date issued
- Software version provided
- Number of installations authorized
- License duration/expiration
- Support tier and terms
- Payment amount and status
- Special terms or restrictions

**Where to store:**
- Secure spreadsheet (Excel, Google Sheets with restricted access)
- Database (if you have many customers)
- CRM system with custom fields
- License management software

### Regular Customer Management Tasks

**Monthly:**
- Review active licenses
- Follow up on any overdue renewals
- Check for support requests

**Quarterly:**
- Contact customers for satisfaction check
- Identify upsell opportunities (more installations, premium support)
- Review compliance (if audit rights exist)

**Annually:**
- Send renewal notices (60-90 days before expiration)
- Review and update pricing
- Offer upgrades to new versions

### Handling Common Scenarios

**Customer needs more installations:**
1. Review current license
2. Confirm additional installations needed
3. Create amendment to license
4. Invoice for additional installations
5. Send updated LICENSE_AUTHORIZATION.txt

**Customer requests support:**
1. Verify they have active license
2. Check support tier in their agreement
3. Provide support per terms
4. Document the support interaction

**Customer reports security issue:**
1. Verify and investigate immediately
2. Create patch if valid
3. Notify all affected customers
4. Provide updated distribution
5. Document incident

**Customer acquired or changes name:**
1. Get notification in writing
2. Verify new entity details
3. Determine if license transfer needed
4. Update license records
5. Issue new LICENSE_AUTHORIZATION.txt if needed

**License expires:**
1. Notify customer 60 days before
2. Offer renewal
3. Negotiate terms if needed
4. Issue new agreement and authorization
5. If not renewed, confirm software decommissioned

---

## Legal Considerations

### Important Disclaimers

⚠️ **This guide and templates are NOT legal advice.**

⚠️ **You MUST consult with a lawyer before:**
- Using these license templates
- Entering into agreements with customers
- Enforcing license terms
- Handling disputes

### Recommended Legal Steps

1. **Hire an attorney experienced in:**
   - Software licensing
   - Intellectual property
   - Contract law
   - Your jurisdiction's commercial law

2. **Have attorney review and customize:**
   - LICENSE file
   - LICENSE_AGREEMENT_TEMPLATE.txt
   - All licensing documentation
   - Your standard terms and conditions

3. **Ensure compliance with:**
   - Copyright laws in your jurisdiction
   - Export control laws (if licensing internationally)
   - Data protection regulations (GDPR, CCPA, etc.)
   - Industry-specific regulations

4. **Consider additional protection:**
   - Copyright registration
   - Trademark registration (for product name)
   - Trade secret protection
   - Patent protection (if applicable)

### Jurisdiction Considerations

**Update the LICENSE file:**
- Replace `[YOUR JURISDICTION]` with your actual jurisdiction
- Examples: "the State of California", "England and Wales", "Ontario, Canada"

**For international customers:**
- May need different license agreements
- Consider export control implications
- Understand enforceability in different jurisdictions
- May need local legal counsel

### Insurance

Consider obtaining:
- **Professional liability insurance:** Covers claims related to software defects
- **Errors and omissions insurance:** Covers mistakes in professional services
- **Cyber liability insurance:** Covers data breaches and cyber incidents

---

## Best Practices

### For License Management

1. **Keep detailed records:** Document everything related to each license
2. **Use consistent processes:** Follow the same steps for each customer
3. **Automate where possible:** Use scripts for distribution creation
4. **Regular audits:** Periodically review all active licenses
5. **Clear communication:** Set expectations clearly with customers

### For Customer Relations

1. **Be responsive:** Answer licensing inquiries promptly
2. **Be professional:** Maintain professional communication
3. **Be fair:** Apply terms consistently across customers
4. **Be flexible:** Consider reasonable customization requests
5. **Build relationships:** Treat customers as partners

### For Security

1. **Secure storage:** Protect license agreements and customer information
2. **Secure delivery:** Use encrypted channels for software distribution
3. **Access control:** Limit who can access licensing systems
4. **Monitor for violations:** Watch for unauthorized use or distribution
5. **Incident response:** Have a plan for security incidents

### For Compliance

1. **Document everything:** Keep records of all licensing decisions
2. **Audit rights:** Consider including audit rights in agreements
3. **Regular reviews:** Check customer compliance periodically
4. **Address violations:** Take action on violations promptly
5. **Legal counsel:** Consult lawyer for compliance issues

### For Growth

1. **Pricing strategy:** Develop clear, fair pricing
2. **Volume discounts:** Consider discounts for multi-installation licenses
3. **Partner programs:** Develop programs for resellers or integrators
4. **Marketing:** Promote your licensing program appropriately
5. **Feedback:** Collect and act on customer feedback

---

## Quick Reference Checklist

### Before First Customer

- [ ] Have lawyer review all legal documents
- [ ] Customize LICENSE file with your jurisdiction
- [ ] Update all contact information (licensing@mesheddata.com → yours)
- [ ] Set up license registry system
- [ ] Test distribution creation script
- [ ] Define pricing structure
- [ ] Define support tiers
- [ ] Set up payment processing
- [ ] Create sales/licensing process documentation

### For Each New Customer

- [ ] Collect customer information
- [ ] Assess and approve license request
- [ ] Negotiate terms and pricing
- [ ] Create customized license agreement
- [ ] Both parties sign agreement
- [ ] Receive payment
- [ ] Record in license registry with unique ID
- [ ] Create distribution package using script
- [ ] Complete LICENSE_AUTHORIZATION.txt
- [ ] Verify distribution contents
- [ ] Securely deliver to customer
- [ ] Provide onboarding (if applicable)
- [ ] Set up support channel

### Ongoing Management

- [ ] Monthly: Review active licenses and support requests
- [ ] Quarterly: Customer satisfaction checks
- [ ] Annually: Send renewal notices
- [ ] As needed: Handle amendments and changes
- [ ] As needed: Provide security updates
- [ ] Continuously: Monitor compliance
- [ ] Continuously: Update documentation

---

## Troubleshooting

### Distribution Script Issues

**Problem:** Script fails with "command not found"
- **Solution:** Ensure bash, git, rsync, tar are installed
- Check script has execute permissions: `chmod +x scripts/prepare_distribution.sh`

**Problem:** Script excludes files you want to include
- **Solution:** Edit the `--exclude` patterns in the script
- Review rsync documentation for pattern syntax

**Problem:** Need to include additional files
- **Solution:** Add files to the repository first, then run script
- Or manually copy files to dist/ after script runs

### Legal Issues

**Problem:** Customer disputes license terms
- **Solution:** Review with your attorney
- May need to negotiate amended terms
- Ensure all changes are documented in writing

**Problem:** License violation discovered
- **Solution:** Contact your attorney immediately
- Document the violation
- Follow enforcement procedures in your agreement

### Customer Management

**Problem:** Customer loses LICENSE_AUTHORIZATION.txt
- **Solution:** Verify their identity
- Send new copy from your records
- Consider digital signature for future

**Problem:** Customer needs emergency support outside agreement
- **Solution:** Provide support as goodwill if warranted
- Document the exception
- Consider upgrading their support tier

---

## Additional Resources

### Documentation in This Repository

- `LICENSE` - Your proprietary license terms
- `docs/LICENSING.md` - Comprehensive licensing guide
- `docs/LICENSING_FAQ.md` - Frequently asked questions
- `docs/LICENSE_AGREEMENT_TEMPLATE.txt` - Legal agreement template
- `docs/LICENSE_HEADER_TEMPLATE.md` - Source code copyright headers
- `scripts/prepare_distribution.sh` - Distribution creation tool

### External Resources

- **Software licensing guides:** Search for "commercial software licensing best practices"
- **Legal resources:** Consult local bar association for attorney referrals
- **License management software:** Consider tools like SafeNet, Flexera, etc. for larger operations

---

## Support and Questions

For questions about implementing this licensing system:

1. **Review the documentation:** Check LICENSING.md and LICENSING_FAQ.md
2. **Legal questions:** Consult your attorney
3. **Technical questions:** Review the scripts and documentation
4. **Business questions:** Consider consulting a business advisor

---

**Document Version:** 1.0  
**Last Updated:** January 2026  
**Created by:** Meshed Data  
**Purpose:** Guide for implementing proprietary software licensing
