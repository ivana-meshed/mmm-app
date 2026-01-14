#!/bin/bash

#
# MMM Trainer - Distribution Finalizer
#
# This script automates the final steps of distribution preparation:
#   - Regenerates checksums after watermarking
#   - Completes LICENSE_AUTHORIZATION.txt with provided details
#   - Creates final archive with checksum
#
# Usage: ./finalize_distribution.sh <customer-slug> <version> <full-customer-name> <license-id> <installations> <valid-until> <contact-email> <authorized-by> <title>
# Example: ./finalize_distribution.sh tfj-buycycle "v1.0.0" "TFJ Buycycle GmbH" "LIC-TFJ-001" "5" "2028-02-01" "info@buycycle.de" "Theodor Golditchuk" "Managing Director"
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check arguments
if [ "$#" -ne 9 ]; then
    print_error "Usage: $0 <customer-slug> <version> <full-customer-name> <license-id> <installations> <valid-until> <contact-email> <authorized-by> <title>"
    echo ""
    echo "Arguments:"
    echo "  customer-slug     : Slug for directory name (e.g., \"tfj-buycycle\" - must match prepare_distribution.sh)"
    echo "  version           : Version number (e.g., \"v1.0.0\")"
    echo "  full-customer-name: Full legal name of customer (e.g., \"TFJ Buycycle GmbH\")"
    echo "  license-id        : Unique license identifier (e.g., \"LIC-TFJ-001\")"
    echo "  installations     : Number of installations allowed (e.g., \"5\")"
    echo "  valid-until       : Expiration date or \"Perpetual\" (e.g., \"2028-02-01\")"
    echo "  contact-email     : Customer contact email (e.g., \"info@buycycle.de\")"
    echo "  authorized-by     : Name of person authorizing (e.g., \"Theodor Golditchuk\")"
    echo "  title             : Title of authorizing person (e.g., \"Managing Director\")"
    echo ""
    echo "Example:"
    echo "  $0 tfj-buycycle \"v1.0.0\" \"TFJ Buycycle GmbH\" \"LIC-TFJ-001\" \"5\" \"2028-02-01\" \"info@buycycle.de\" \"Theodor Golditchuk\" \"Managing Director\""
    exit 1
fi

CUSTOMER_SLUG="$1"
VERSION="$2"
CUSTOMER_NAME="$3"
LICENSE_ID="$4"
INSTALLATIONS="$5"
VALID_UNTIL="$6"
CONTACT_EMAIL="$7"
AUTHORIZED_BY="$8"
TITLE="$9"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
DIST_DIR="${REPO_ROOT}/dist/${CUSTOMER_SLUG}-${VERSION}"
APP_DIR="${DIST_DIR}/mmm-app"

# Validate distribution exists
if [ ! -d "$DIST_DIR" ]; then
    print_error "Distribution directory does not exist: $DIST_DIR"
    print_error "Run prepare_distribution.sh and watermark_distribution.sh first"
    exit 1
fi

print_info "Finalizing distribution for: ${CUSTOMER_NAME}"
print_info "Version: ${VERSION}"
print_info "License ID: ${LICENSE_ID}"
echo ""

# Step 1: Regenerate checksums
print_info "Step 1: Regenerating checksums (including watermarked files)..."
cd "${DIST_DIR}"
find mmm-app -type f -exec sha256sum {} \; > CHECKSUMS.txt
CHECKSUM_COUNT=$(wc -l < CHECKSUMS.txt)
print_info "✓ Generated checksums for ${CHECKSUM_COUNT} files"
echo ""

# Step 2: Complete LICENSE_AUTHORIZATION.txt
print_info "Step 2: Completing LICENSE_AUTHORIZATION.txt..."

LICENSE_AUTH_FILE="${APP_DIR}/LICENSE_AUTHORIZATION.txt"

if [ ! -f "$LICENSE_AUTH_FILE" ]; then
    print_error "LICENSE_AUTHORIZATION.txt not found in ${APP_DIR}"
    exit 1
fi

cat > "${LICENSE_AUTH_FILE}" << EOF
LICENSE AUTHORIZATION CERTIFICATE

Meshed Data Consulting hereby authorizes:

Organization: ${CUSTOMER_NAME}
Contact: ${CONTACT_EMAIL}
Date: $(date +%Y-%m-%d)
License ID: ${LICENSE_ID}

To install and use the MMM Trainer software version ${VERSION} under the
terms of the Proprietary Software License Agreement.

Number of Installations: ${INSTALLATIONS}
Valid Until: ${VALID_UNTIL}

Additional Terms:
- This license is non-transferable and non-sublicensable
- Redistribution of this software is strictly prohibited
- Customer must restrict access to authorized personnel only
- Customer must comply with all terms in the LICENSE file

Authorized by:
${AUTHORIZED_BY}
${TITLE}
Meshed Data Consulting
Date: $(date +%Y-%m-%d)

Support Contact: fethu@mesheddata.com

Signature: [To be signed by authorized representative]

---
This certificate authorizes the use of MMM Trainer software by ${CUSTOMER_NAME}
under the terms specified above. This certificate must be retained with the
software and presented upon request for compliance verification.
EOF

print_info "✓ LICENSE_AUTHORIZATION.txt completed with provided details"
echo ""

# Step 3: Create final archive
print_info "Step 3: Creating final distribution archive..."
cd "${REPO_ROOT}/dist"
ARCHIVE_NAME="${CUSTOMER_SLUG}-${VERSION}-FINAL.tar.gz"

# Remove old archive if exists
if [ -f "${ARCHIVE_NAME}" ]; then
    print_warning "Removing existing archive: ${ARCHIVE_NAME}"
    rm -f "${ARCHIVE_NAME}"
fi

tar -czf "${ARCHIVE_NAME}" "${CUSTOMER_SLUG}-${VERSION}/"
print_info "✓ Created archive: ${ARCHIVE_NAME}"

# Generate archive checksum
ARCHIVE_CHECKSUM=$(sha256sum "${ARCHIVE_NAME}" | cut -d' ' -f1)
echo "${ARCHIVE_CHECKSUM}  ${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"
print_info "✓ Generated archive checksum: ${ARCHIVE_NAME}.sha256"
echo ""

# Create distribution manifest
print_info "Creating distribution manifest..."
MANIFEST_FILE="${REPO_ROOT}/dist/${CUSTOMER_SLUG}-${VERSION}-MANIFEST.txt"
cat > "${MANIFEST_FILE}" << EOF
DISTRIBUTION MANIFEST
=====================

Customer: ${CUSTOMER_NAME}
Version: ${VERSION}
License ID: ${LICENSE_ID}
Date Prepared: $(date +%Y-%m-%d)
Prepared By: Meshed Data Consulting

LICENSE DETAILS:
----------------
License ID: ${LICENSE_ID}
Installations: ${INSTALLATIONS}
Valid Until: ${VALID_UNTIL}
Customer Contact: ${CONTACT_EMAIL}

DISTRIBUTION CONTENTS:
---------------------
Archive: ${ARCHIVE_NAME}
Archive Size: $(du -h "${ARCHIVE_NAME}" | cut -f1)
Archive Checksum (SHA256): ${ARCHIVE_CHECKSUM}

Package Contents:
- mmm-app/                     Complete application source code
- LICENSE_AUTHORIZATION.txt    Signed authorization certificate
- CHECKSUMS.txt                File integrity checksums
- README_DISTRIBUTION.txt      Distribution instructions
- WATERMARK_MANIFEST.txt       Watermark details

VERIFICATION:
-------------
To verify archive integrity:
  sha256sum -c ${ARCHIVE_NAME}.sha256

To verify package contents:
  tar -xzf ${ARCHIVE_NAME}
  cd ${CUSTOMER_SLUG}-${VERSION}
  sha256sum -c CHECKSUMS.txt

DELIVERY:
---------
Recommended delivery methods:
1. Secure file transfer (SFTP/SCP)
2. Encrypted cloud storage with access control
3. Physical media for sensitive deployments

CUSTOMER SUPPORT:
-----------------
Technical Support: fethu@mesheddata.com
Documentation: See docs/ directory in distribution
Deployment Guide: docs/CUSTOMER_DEPLOYMENT_GUIDE_TFJ_BUYCYCLE.md

COMPLIANCE:
-----------
This distribution is authorized for use by ${CUSTOMER_NAME} only.
Redistribution is strictly prohibited under the license terms.
This distribution has been watermarked for tracking purposes.

LICENSE TRACKING:
-----------------
License Registry ID: ${LICENSE_ID}
Distribution ID: ${LICENSE_ID}-$(date +%Y%m%d)
Watermark ID: ${LICENSE_ID}-$(date +%Y%m%d)

AUTHORIZED BY:
--------------
Name: ${AUTHORIZED_BY}
Title: ${TITLE}
Organization: Meshed Data Consulting
Date: $(date +%Y-%m-%d)

---
Generated by: finalize_distribution.sh
EOF

print_info "✓ Created distribution manifest: ${MANIFEST_FILE}"
echo ""

# Summary
print_info "============================================"
print_info "Distribution finalization completed!"
print_info "============================================"
echo ""
print_info "DISTRIBUTION SUMMARY:"
echo ""
echo "Customer:        ${CUSTOMER_NAME}"
echo "Version:         ${VERSION}"
echo "License ID:      ${LICENSE_ID}"
echo "Installations:   ${INSTALLATIONS}"
echo "Valid Until:     ${VALID_UNTIL}"
echo ""
print_info "FILES CREATED:"
echo ""
echo "1. Archive:      ${REPO_ROOT}/dist/${ARCHIVE_NAME}"
echo "   Size:         $(du -h "${REPO_ROOT}/dist/${ARCHIVE_NAME}" | cut -f1)"
echo "   Checksum:     ${ARCHIVE_CHECKSUM}"
echo ""
echo "2. Checksum:     ${REPO_ROOT}/dist/${ARCHIVE_NAME}.sha256"
echo "3. Manifest:     ${MANIFEST_FILE}"
echo ""
print_info "VERIFICATION:"
echo ""
echo "Customer can verify package integrity:"
echo "  sha256sum -c ${ARCHIVE_NAME}.sha256"
echo ""
print_warning "NEXT STEPS:"
echo ""
echo "1. REVIEW the final archive contents:"
echo "   tar -tzf ${ARCHIVE_NAME} | less"
echo ""
echo "2. SIGN the LICENSE_AUTHORIZATION.txt (optional but recommended):"
echo "   - Extract archive"
echo "   - Print LICENSE_AUTHORIZATION.txt"
echo "   - Sign physically"
echo "   - Scan and replace in archive (if required)"
echo ""
echo "3. DELIVER securely to customer:"
echo "   - Use encrypted transfer method"
echo "   - Provide checksum file separately"
echo "   - Include MANIFEST for reference"
echo ""
echo "4. UPDATE license registry:"
echo "   - Record distribution date"
echo "   - Store manifest file"
echo "   - Set up monitoring/compliance check"
echo ""
echo "5. NOTIFY customer:"
echo "   - Send delivery notification"
echo "   - Provide verification instructions"
echo "   - Share deployment guide link"
echo "   - Confirm support contact"
echo ""
print_info "Distribution is ready for secure delivery!"
echo ""
