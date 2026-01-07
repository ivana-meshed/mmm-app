#!/bin/bash

#
# MMM Trainer - Distribution Package Creator
#
# This script creates a clean distribution of the MMM Trainer software
# without git history, suitable for delivery to licensed customers.
#
# Usage: ./prepare_distribution.sh <customer-name> <version>
# Example: ./prepare_distribution.sh acme-corp v1.0.0
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
if [ "$#" -ne 2 ]; then
    print_error "Usage: $0 <customer-name> <version>"
    echo "Example: $0 acme-corp v1.0.0"
    exit 1
fi

CUSTOMER_NAME="$1"
VERSION="$2"
DIST_DIR="dist/${CUSTOMER_NAME}-${VERSION}"
APP_DIR="${DIST_DIR}/mmm-app"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"

print_info "Creating distribution package for ${CUSTOMER_NAME} (${VERSION})"

# Create distribution directory
print_info "Creating distribution directory: ${DIST_DIR}"
mkdir -p "${APP_DIR}"

# Export current state without git history
print_info "Exporting repository content..."
cd "${REPO_ROOT}"

# Create temporary export
TEMP_EXPORT="/tmp/mmm-app-export-$$"
mkdir -p "${TEMP_EXPORT}"
git archive --format=tar HEAD | tar -x -C "${TEMP_EXPORT}"

# Copy files with exclusions
print_info "Copying files (excluding development artifacts)..."
rsync -av \
    --exclude='.git' \
    --exclude='.github/workflows/config.yml' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.pytest_cache' \
    --exclude='node_modules' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='*.log' \
    --exclude='.DS_Store' \
    --exclude='dist' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='*.egg-info' \
    "${TEMP_EXPORT}/" \
    "${APP_DIR}/"

# Note: .github/workflows are NOW INCLUDED for customer use
# Only config.yml (company-specific settings) is excluded

# Clean up temp export
rm -rf "${TEMP_EXPORT}"

# Create LICENSE_AUTHORIZATION template
print_info "Creating LICENSE_AUTHORIZATION template..."
cat > "${APP_DIR}/LICENSE_AUTHORIZATION.txt" << EOF
LICENSE AUTHORIZATION CERTIFICATE

Meshed Data hereby authorizes:

Organization: ${CUSTOMER_NAME}
Contact: [TO BE COMPLETED]
Date: $(date +%Y-%m-%d)
License ID: [TO BE COMPLETED]

To install and use the MMM Trainer software version ${VERSION} under the
terms of the Proprietary Software License Agreement.

Number of Installations: [TO BE COMPLETED]
Valid Until: [TO BE COMPLETED or "Perpetual"]

Additional Terms:
- [TO BE COMPLETED]

Authorized by:
[TO BE COMPLETED]
[TO BE COMPLETED - Title]
Meshed Data
Date: $(date +%Y-%m-%d)

Signature: ___________________________
EOF

# Create distribution README
print_info "Creating distribution documentation..."
cat > "${DIST_DIR}/README_DISTRIBUTION.txt" << EOF
MMM TRAINER DISTRIBUTION PACKAGE

Customer: ${CUSTOMER_NAME}
Version: ${VERSION}
Date: $(date +%Y-%m-%d)

CONTENTS:
---------
mmm-app/                    - Application source code and infrastructure
LICENSE_AUTHORIZATION.txt   - Your license authorization certificate

IMPORTANT:
----------
1. READ the LICENSE_AUTHORIZATION.txt file - this is your authorization to
   use the software.

2. READ the LICENSE file in mmm-app/ - this is the license agreement that
   governs your use of the software.

3. FOLLOW the deployment guide in mmm-app/docs/DEPLOYMENT_GUIDE.md

4. ENSURE you comply with all license restrictions, particularly:
   - DO NOT redistribute this software to any third party
   - DO NOT make this software available as a service to others
   - RESTRICT access to authorized personnel only

GETTING STARTED:
----------------
1. Review mmm-app/README.md for project overview
2. Review mmm-app/docs/REQUIREMENTS.md for prerequisites
3. Follow mmm-app/docs/DEPLOYMENT_GUIDE.md for step-by-step deployment
4. Review mmm-app/ARCHITECTURE.md to understand the system

SUPPORT:
--------
For technical support and licensing questions, contact the support channel
specified in your LICENSE_AUTHORIZATION.txt file.

COMPLIANCE:
-----------
By using this software, you agree to comply with all terms in the LICENSE
file. Violation of license terms may result in immediate termination of
your license and legal action.

---
Prepared by: Meshed Data
Distribution ID: ${CUSTOMER_NAME}-${VERSION}
Date: $(date +%Y-%m-%d)
EOF

# Create package checksum
print_info "Creating checksums..."
cd "${DIST_DIR}"
find mmm-app -type f -exec sha256sum {} \; > CHECKSUMS.txt

# Create archive
print_info "Creating distribution archive..."
cd "${REPO_ROOT}/dist"
ARCHIVE_NAME="${CUSTOMER_NAME}-${VERSION}.tar.gz"
tar -czf "${ARCHIVE_NAME}" "${CUSTOMER_NAME}-${VERSION}/"

# Calculate archive checksum
ARCHIVE_CHECKSUM=$(sha256sum "${ARCHIVE_NAME}" | cut -d' ' -f1)

print_info "============================================"
print_info "Distribution package created successfully!"
print_info "============================================"
echo ""
print_info "Location: ${REPO_ROOT}/dist/${CUSTOMER_NAME}-${VERSION}/"
print_info "Archive:  ${REPO_ROOT}/dist/${ARCHIVE_NAME}"
print_info "Checksum: ${ARCHIVE_CHECKSUM}"
echo ""
print_warning "NEXT STEPS:"
echo "1. Complete the LICENSE_AUTHORIZATION.txt file"
echo "2. Sign the authorization certificate"
echo "3. Review all files in the distribution"
echo "4. Test the distribution package"
echo "5. Securely deliver to customer"
echo ""
print_warning "REMEMBER:"
echo "- Keep records of this distribution"
echo "- Update your license registry"
echo "- Provide customer with support contact information"
echo ""
