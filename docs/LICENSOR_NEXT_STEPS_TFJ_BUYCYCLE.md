# LICENSOR NEXT STEPS - TFJ Buycycle GmbH License Agreement

## License Agreement Details

- **Licensor**: Meshed Data Consulting
- **Licensee**: TFJ Buycycle GmbH
  - **Address**: Atelierstraße 12, 81671 München, Deutschland
  - **Register**: HRB 263786, Amtsgericht München
  - **Managing Directors**: Theodor Golditchuk, Jonas Jäger
  - **VAT ID**: DE341044568
  - **Contact Email**: info@buycycle.de
- **License ID**: LIC-TFJ-001
- **License Period**: February 2026 - February 2028 (2 years)
- **Software**: MMM Trainer v1.0.0

---

## Checklist: Pre-Distribution Tasks

### 1. Legal Review & Agreement Preparation ⚖️

- [ ] **Have attorney review** all licensing documents
  - [ ] Verify LICENSE file is compliant with German law
  - [ ] Review warranty disclaimers (BGB §309 compliance)
  - [ ] Confirm AGB requirements are met
  - [ ] Verify jurisdiction clause is appropriate

- [ ] **Customize LICENSE_AGREEMENT_TEMPLATE.txt**
  - [ ] Location: `docs/LICENSE_AGREEMENT_TEMPLATE.txt`
  - [ ] Fill in all `[PLACEHOLDERS]`
  - [ ] Licensor: Meshed Data Consulting (full legal details)
  - [ ] Licensee: TFJ Buycycle GmbH (full legal details)
  - [ ] License fee and payment terms
  - [ ] Number of installations authorized
  - [ ] Support terms and SLA
  - [ ] Specific restrictions or special provisions

- [ ] **Execute agreement**
  - [ ] Both parties review and negotiate
  - [ ] Both parties sign
  - [ ] Exchange signed copies
  - [ ] Store securely (outside git repository)

- [ ] **Receive payment** (or establish payment schedule)

### 2. Create Distribution Package 📦

- [ ] **Run distribution script**
  ```bash
  ./scripts/prepare_distribution.sh tfj-buycycle v1.0.0
  ```
  - Creates: `dist/tfj-buycycle-v1.0.0/`
  - Includes: Clean code, LICENSE_AUTHORIZATION.txt template, CHECKSUMS.txt
  - Archive: `dist/tfj-buycycle-v1.0.0.tar.gz`

- [ ] **Apply watermarks** (IMPORTANT for tracking)
  ```bash
  ./scripts/watermark_distribution.sh \
    "TFJ Buycycle GmbH" \
    "LIC-TFJ-001" \
    "2026-02-01" \
    "2028-02-01" \
    "dist/tfj-buycycle-v1.0.0/mmm-app"
  ```
  - Adds unique identifiers to all Python, R, and Bash files
  - Creates WATERMARK_MANIFEST.txt
  - Files watermarked: ~150+ source files

- [ ] **Finalize distribution** (AUTOMATED - regenerates checksums, completes authorization, creates final archive)
  ```bash
  ./scripts/finalize_distribution.sh \
    tfj-buycycle \
    "v1.0.0" \
    "TFJ Buycycle GmbH" \
    "LIC-TFJ-001" \
    "5" \
    "2028-02-01" \
    "info@buycycle.de" \
    "Theodor Golditchuk" \
    "Managing Director"
  ```
  - Automatically regenerates checksums (including watermarked files)
  - Completes LICENSE_AUTHORIZATION.txt with provided details
  - Creates final archive: `tfj-buycycle-v1.0.0-FINAL.tar.gz`
  - Generates archive checksum: `tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256`
  - Creates distribution manifest with all details
  
  **Note**: Adjust parameters:
  - Number of installations (e.g., "5")
  - Customer contact email
  - Your name and title for authorization

### 3. Quality Assurance ✅

- [ ] **Verify distribution contents**
  - [ ] Check LICENSE file is present
  - [ ] Verify LICENSE_AUTHORIZATION.txt is complete and signed
  - [ ] Confirm WATERMARK_MANIFEST.txt exists
  - [ ] Spot-check watermarks in 5-10 random source files
  - [ ] Verify CHECKSUMS.txt is current
  - [ ] Check README_DISTRIBUTION.txt is present

- [ ] **Test extraction**
  ```bash
  cd /tmp
  tar -xzf /path/to/dist/tfj-buycycle-v1.0.0-FINAL.tar.gz
  cd tfj-buycycle-v1.0.0/mmm-app
  sha256sum -c ../CHECKSUMS.txt
  ```

- [ ] **Verify GitHub workflows included**
  - [ ] `.github/workflows/ci.yml` present
  - [ ] `.github/workflows/ci-dev.yml` present
  - [ ] `.github/workflows/config.example.txt` present
  - [ ] `.github/workflows/README.md` present
  - [ ] `.github/workflows/config.yml` NOT present (your private config)

- [ ] **Verify finalization artifacts**
  - [ ] `tfj-buycycle-v1.0.0-FINAL.tar.gz` exists
  - [ ] `tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256` exists
  - [ ] `tfj-buycycle-v1.0.0-MANIFEST.txt` exists with complete details
  - [ ] LICENSE_AUTHORIZATION.txt is fully populated (no placeholders)

### 4. Update License Registry 📋

- [ ] **Record in license registry** (spreadsheet or database)
  - License ID: LIC-TFJ-001
  - Customer: TFJ Buycycle GmbH
  - Contact: [Primary contact email and phone]
  - Start Date: 2026-02-01
  - End Date: 2028-02-01
  - Installations: [Number authorized]
  - Payment Status: [Paid/Pending]
  - Distribution Date: [Date delivered]
  - Archive Checksum: [SHA-256 of .tar.gz]
  - Watermark ID: LIC-TFJ-001-[YYYYMMDD]
  - Support Tier: [Standard/Premium/Custom]
  - Notes: [Any special terms or restrictions]

### 5. Prepare Customer Handoff Package 📬

- [ ] **Verify final deliverables** (created by finalize_distribution.sh)
  - [ ] `tfj-buycycle-v1.0.0-FINAL.tar.gz` - Ready for delivery
  - [ ] `tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256` - Integrity checksum
  - [ ] `tfj-buycycle-v1.0.0-MANIFEST.txt` - Distribution details

- [ ] **Prepare delivery email** with:
  - Link to secure file transfer (or encrypted attachment)
  - Archive checksum for verification
  - Getting started instructions (see CUSTOMER_DEPLOYMENT_GUIDE.md)
  - Support contact information
  - License expiration reminder

- [ ] **Create support ticket/channel** for customer
  - Email: fethu@mesheddata.com
  - Slack/Teams channel (if applicable)
  - Calendar invite for onboarding call

### 6. Secure Delivery 🔒

- [ ] **Choose secure delivery method**
  - Option A: Encrypted email (GPG/PGP)
  - Option B: Secure file sharing (Tresorit, SpiderOak, etc.)
  - Option C: Private GitHub repository (with customer as collaborator)
  - Option D: USB drive via courier (for highly sensitive)

- [ ] **Send package to customer** with:
  - Distribution archive
  - Checksum file
  - Signed LICENSE_AUTHORIZATION.txt (separate PDF)
  - Getting started guide (CUSTOMER_DEPLOYMENT_GUIDE.md)
  - Support contact information

- [ ] **Confirm receipt** with customer

### 7. Customer Onboarding 🤝

- [ ] **Schedule onboarding call** (within 1 week of delivery)
  - Review license terms and restrictions
  - Walkthrough of distribution contents
  - Answer technical questions
  - Explain GitHub workflows setup
  - Discuss Workload Identity Federation requirements
  - Set support expectations

- [ ] **Provide documentation links**
  - README.md - Project overview
  - ARCHITECTURE.md - System architecture
  - DEVELOPMENT.md - Local development setup
  - DEPLOYMENT_GUIDE.md - Deployment walkthrough
  - .github/workflows/README.md - CI/CD setup

- [ ] **Share example configuration**
  - Snowflake connection examples
  - GCP project setup checklist
  - GitHub Secrets configuration

### 8. Post-Delivery Follow-up 📞

- [ ] **Week 1**: Check-in call
  - Confirm successful extraction
  - Verify checksums passed
  - Answer initial questions
  - Confirm GitHub Secrets configured

- [ ] **Week 2-4**: Technical support as needed
  - Deployment troubleshooting
  - Workflow configuration assistance
  - Snowflake connection issues
  - GCP permissions problems

- [ ] **Month 3**: Quarterly check-in
  - How is deployment going?
  - Any feature requests?
  - Performance issues?
  - Compliance verification (subtle)

- [ ] **Month 6, 12, 18**: Regular check-ins
  - Ongoing support
  - Renewal discussions (18 months)
  - Compliance verification

### 9. Monitoring & Compliance 🕵️

- [ ] **Set up monitoring**
  - Google Alerts for code snippets (unique strings)
  - GitHub code search for watermark IDs
  - Periodic manual searches

- [ ] **Review customer compliance**
  - Regular check-in calls
  - Verify number of installations
  - Confirm no redistribution
  - Request usage statistics (if agreed)

- [ ] **Document any issues**
  - License violations
  - Support requests
  - Feature requests
  - Bug reports

### 10. Renewal Planning (Month 18+) 🔄

- [ ] **18 months before expiration**: Initiate renewal conversation
- [ ] **12 months**: Formal renewal proposal
- [ ] **6 months**: Finalize renewal terms
- [ ] **3 months**: Execute new agreement
- [ ] **Expiration date**: Ensure continuity or graceful termination

---

## Quick Command Reference

```bash
# 1. Create distribution
./scripts/prepare_distribution.sh tfj-buycycle v1.0.0

# 2. Apply watermarks
./scripts/watermark_distribution.sh \
  "TFJ Buycycle GmbH" "LIC-TFJ-001" \
  "2026-02-01" "2028-02-01" \
  "dist/tfj-buycycle-v1.0.0/mmm-app"

# 3. Regenerate checksums
cd dist/tfj-buycycle-v1.0.0
find mmm-app -type f -exec sha256sum {} \; > CHECKSUMS.txt

# 4. Create final archive
cd ..
tar -czf tfj-buycycle-v1.0.0-FINAL.tar.gz tfj-buycycle-v1.0.0/
sha256sum tfj-buycycle-v1.0.0-FINAL.tar.gz > tfj-buycycle-v1.0.0-FINAL.tar.gz.sha256
```

---

## Important Files to Keep

**Store securely (NOT in git):**
- Signed LICENSE_AGREEMENT (PDF)
- Customer contact details
- Payment receipts
- Communication history
- Support tickets

**Store in git (if private repo):**
- Distribution scripts (already in repo)
- Documentation updates
- License registry (spreadsheet)

---

## Support Contacts

- **Your Email**: fethu@mesheddata.com
- **Technical Support**: support@mesheddata.com
- **Legal Questions**: [Your attorney contact]

---

## Success Criteria

✅ Distribution created and watermarked
✅ License agreement signed by both parties
✅ Payment received
✅ Package securely delivered to customer
✅ Customer confirms receipt
✅ Onboarding call completed
✅ Customer successfully deploys in dev environment
✅ License registry updated
✅ Support channel established

---

**Estimated Timeline**: 1-2 weeks from agreement signature to customer deployment in production.

**Next Review**: Month 3 check-in call (May 2026)
