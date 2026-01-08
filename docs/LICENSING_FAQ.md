# Licensing FAQ - MMM Trainer

This document answers frequently asked questions about the MMM Trainer proprietary software license.

## Table of Contents

- [General Questions](#general-questions)
- [For Potential Customers](#for-potential-customers)
- [For Current Licensees](#for-current-licensees)
- [For License Administrators](#for-license-administrators)
- [Technical Questions](#technical-questions)
- [Legal and Compliance](#legal-and-compliance)

---

## General Questions

### What kind of license is this?

The MMM Trainer uses a proprietary, commercial software license. This is NOT open source. The software is owned by Meshed Data and is licensed (not sold) to customers under specific terms that prohibit redistribution.

### Why not use an open source license?

Open source licenses (like MIT, Apache, GPL) allow anyone to use, modify, and redistribute the software freely. The proprietary model allows us to:
- Control who can use the software
- Maintain the value of our intellectual property
- Provide better support to authorized customers
- Prevent competitors from using our work
- Ensure quality and security through controlled distribution

### Can I see the license terms before committing?

Yes, the complete license text is in the [LICENSE](../LICENSE) file in this repository. Review it thoroughly before requesting authorization.

---

## For Potential Customers

### How do I obtain a license?

1. Review the LICENSE file to understand the terms
2. Contact licensing@mesheddata.com with:
   - Your organization details
   - Intended use case
   - Number of installations needed
   - Your GCP project information
3. Discuss terms and pricing
4. Sign the license agreement
5. Receive your LICENSE_AUTHORIZATION.txt and software distribution

### What does a license cost?

Pricing is determined based on:
- Number of installations/deployments
- Size of organization
- Support requirements
- License duration (annual vs. perpetual)
- Custom requirements

Contact licensing@mesheddata.com for a quote.

### Can I try it before buying?

Evaluation licenses may be available. Contact licensing@mesheddata.com to discuss trial options. Trials typically include:
- Time-limited license (e.g., 30-60 days)
- Technical support during evaluation
- Limited number of installations
- No redistribution during trial period

### What happens after I get a license?

1. You receive a distribution package containing:
   - Clean copy of the software (without git history)
   - LICENSE_AUTHORIZATION.txt specific to your organization
   - Complete documentation
   - Deployment guides
2. You can install on your infrastructure following our deployment guide
3. You receive support as specified in your agreement
4. You can modify the software for internal use
5. You must comply with all license restrictions

### Can I deploy to multiple GCP projects?

Each deployment/installation must be authorized in your LICENSE_AUTHORIZATION.txt. If you need multiple installations:
- Specify this when requesting the license
- Each project counts as one installation
- Multi-project licenses are available

### What if I need more installations later?

Contact licensing@mesheddata.com to amend your license. This typically involves:
- Updating your LICENSE_AUTHORIZATION.txt
- Additional license fee (if applicable)
- New authorization certificate

---

## For Current Licensees

### Can I modify the source code?

**Yes**, you can modify the software for your internal use. However:
- Modifications must remain confidential
- Modified versions are still subject to the license terms
- You cannot distribute modifications to third parties
- You cannot use modifications to create competing products

### Can I share the software with our subsidiary?

Only if explicitly authorized in your LICENSE_AUTHORIZATION.txt. If not authorized:
1. Contact licensing@mesheddata.com
2. Request extension to cover subsidiary
3. Provide subsidiary details
4. Receive updated authorization

### Can I show the software to potential business partners?

Only with prior approval from Meshed Data:
1. Contact licensing@mesheddata.com
2. Explain the business purpose
3. Have partner sign appropriate NDA
4. Get written approval
5. Limit access to evaluation purposes only

### What if our company is acquired or merges?

**Immediately notify Meshed Data** when:
- Your company is acquired
- Your company merges with another
- Ownership structure changes significantly

The license may need to be:
- Transferred to the new entity
- Reissued under new terms
- Terminated and replaced

### Can I use this with contractors or consultants?

Yes, but you must:
- Ensure they sign NDAs covering the software
- Restrict their access to only what's necessary
- Ensure they understand the proprietary nature
- Not allow them to copy or redistribute the software
- Revoke access when engagement ends
- Take responsibility for their compliance

### What support is included?

Support terms vary by license agreement. Common support options include:
- Email support for technical issues
- Security updates and patches
- Bug fixes
- Version upgrades (may require additional fee)
- Documentation updates

Check your LICENSE_AUTHORIZATION.txt for specific support terms.

### Can I contribute improvements back to Meshed Data?

You can propose improvements, but:
- Intellectual property rights must be negotiated
- Typically requires a separate contribution agreement
- You may receive credit or compensation
- Meshed Data retains rights to incorporate changes

Contact licensing@mesheddata.com to discuss contribution terms.

### What happens if my license expires?

For time-limited licenses:
- You'll receive renewal notice 60 days before expiration
- You must cease use if not renewed
- Grace periods may be available
- Data extraction support may be provided

### Can I transfer my license to another company?

License transfers require:
1. Written request to Meshed Data
2. Details about the new licensee
3. Approval from Meshed Data
4. Transfer fee (if applicable)
5. New LICENSE_AUTHORIZATION.txt issued

Transfers are not automatic and may be denied.

---

## For License Administrators

### How do I track which customers have licenses?

Maintain a license registry spreadsheet or database with:
- License ID (unique identifier)
- Customer organization name
- Contact person and email
- Date issued
- Version provided
- Number of installations authorized
- Expiration date (if applicable)
- Support tier
- Renewal date
- Special terms or restrictions

### How do I create a distribution for a customer?

Use the automated script:

```bash
./scripts/prepare_distribution.sh customer-name v1.0.0
```

This creates a clean distribution in `dist/customer-name-v1.0.0/` with:
- Software without git history
- LICENSE_AUTHORIZATION.txt template
- Checksums for verification
- Distribution documentation

Then:
1. Complete the LICENSE_AUTHORIZATION.txt
2. Review and sign
3. Test the package
4. Securely deliver to customer

See [LICENSING.md](LICENSING.md) for detailed procedures.

### Should I include the .github workflows in distributions?

**Yes.** As of the latest version, GitHub workflows ARE included in customer distributions to enable them to use CI/CD. However:
- The `config.example.txt` file provides a template for configuration
- Your actual `config.yml` (with company-specific settings) is excluded
- Customers must create their own `config.yml` with their GCP project settings
- Customers must configure their own GitHub Secrets
- See `.github/workflows/config.example.txt` for instructions

### How do I handle security vulnerabilities?

1. **Assess Impact:**
   - Determine severity and affected versions
   - Identify which customers are affected
   
2. **Develop Fix:**
   - Create patch or security update
   - Test thoroughly
   
3. **Notify Customers:**
   - Contact all affected licensees promptly
   - Provide vulnerability details (appropriate level)
   - Specify urgency and timeline
   
4. **Distribute Update:**
   - Create new distribution with fix
   - Provide upgrade instructions
   - Offer upgrade support
   
5. **Verify:**
   - Confirm customers have applied update
   - Document the vulnerability and fix

### How do I handle license violations?

If you discover a licensee violating terms:

1. **Document the Violation:**
   - Gather evidence
   - Document specific terms violated
   - Note when violation occurred

2. **Initial Contact:**
   - Contact licensee in writing
   - Explain the violation
   - Request immediate corrective action
   - Set deadline for compliance

3. **Escalation:**
   - If not resolved, send formal notice
   - May include cease-and-desist
   - Consider license termination
   - Consult legal counsel

4. **Resolution:**
   - Document corrective actions
   - May require additional fees
   - May require amended license terms
   - Consider audit rights

### Can I grant different terms to different customers?

Yes, the base LICENSE file provides standard terms, but you can:
- Add custom terms in LICENSE_AUTHORIZATION.txt
- Create amended license agreements for specific customers
- Vary pricing based on customer needs
- Offer different support tiers

Ensure all variations are properly documented.

### How do I version control licenses?

- Keep master LICENSE file in git repository
- Use tags for version releases (v1.0.0, v1.1.0, etc.)
- Maintain change log for license terms
- Store signed customer agreements separately (not in git)
- Reference specific versions in LICENSE_AUTHORIZATION.txt

---

## Technical Questions

### Does the license affect how the software runs?

No, the license is a legal agreement, not a technical control. The software does not include:
- License checking code
- Activation keys
- Phone-home features
- Usage tracking (beyond normal application logging)

Compliance is based on trust and legal agreement.

### Can customers deploy to any cloud provider?

The software is designed for Google Cloud Platform (GCP). The license permits installation on licensee's own infrastructure, which could include:
- Customer's GCP projects
- Other cloud providers (with modifications)
- On-premises infrastructure (with significant modifications)

However, technical support may be limited to GCP deployments.

### Can customers modify the infrastructure code?

Yes, customers can modify:
- Terraform configurations for their environment
- Docker configurations
- Deployment scripts
- Application configuration

They cannot:
- Redistribute modifications
- Create reusable infrastructure modules for others
- Offer modified version as a service

### Do customers get access to updates?

Terms vary by agreement. Common models:
- **Perpetual license, limited updates:** Major version only, no upgrades
- **Perpetual license with support:** Security updates and bug fixes
- **Annual license:** All updates during license period
- **Enterprise license:** All updates, priority support

Check specific LICENSE_AUTHORIZATION.txt for update terms.

---

## Legal and Compliance

### What jurisdiction governs the license?

The LICENSE file specifies the governing jurisdiction (currently marked as [YOUR JURISDICTION]). Update this based on your company's location and legal advice.

### Do I need a lawyer to review the license?

**Yes, strongly recommended.** Before using this license:
- Have your legal counsel review and approve
- Customize for your jurisdiction
- Ensure compliance with local laws
- Consider specific industry regulations

### Can customers sue us?

The LICENSE includes limitation of liability and warranty disclaimer, but:
- These may not be enforceable in all jurisdictions
- They don't prevent all lawsuits
- Professional liability insurance is recommended
- Legal counsel should review all agreements

### What if a customer goes bankrupt?

Include bankruptcy provisions in your license agreements:
- Clarify whether license survives bankruptcy
- Address potential transfer in bankruptcy proceedings
- Consider termination rights
- Consult legal counsel for jurisdiction-specific rules

### How do we enforce the non-redistribution clause?

Enforcement options include:
1. **Contractual remedies:** Damages, specific performance
2. **Copyright enforcement:** DMCA takedowns, copyright infringement claims
3. **Trade secret protection:** If software includes trade secrets
4. **Injunctive relief:** Court orders to stop distribution

Strong agreements and monitoring help prevent violations.

### Do we need to register copyright?

In most jurisdictions, copyright is automatic upon creation. However:
- Registration may be required to sue in some jurisdictions (e.g., US)
- Registration provides additional legal benefits
- Consult with intellectual property attorney
- Consider registration for major versions

### What about international customers?

International licensing considerations:
- Export control laws (check if software is subject to restrictions)
- Different copyright laws in different countries
- Currency and payment processing
- Support across time zones
- Language barriers for documentation
- May need localized license agreements

Consult international business attorney for cross-border licensing.

---

## Contact

For licensing questions not covered here:
- Email: licensing@mesheddata.com
- Website: https://mesheddata.com

For legal consultation:
- Consult your own legal counsel
- This FAQ is not legal advice

---

**Document Version:** 1.0  
**Last Updated:** January 2026  
**Maintained by:** Meshed Data
