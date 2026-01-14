#!/bin/bash

#
# MMM Trainer - Distribution Watermarking Script
#
# This script adds unique watermark comments to source files in a distribution
# to enable tracking if code is redistributed without authorization.
#
# Usage: ./watermark_distribution.sh <customer-name> <license-id> <start-date> <end-date> <distribution-path>
# Example: ./watermark_distribution.sh "TFJ Buycycle GmbH" "LIC-TFJ-001" "2026-02-01" "2028-02-01" "dist/tfj-buycycle-v1.0.0/mmm-app"
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
if [ "$#" -ne 5 ]; then
    print_error "Usage: $0 <customer-name> <license-id> <start-date> <end-date> <distribution-path>"
    echo "Example: $0 \"TFJ Buycycle GmbH\" \"LIC-TFJ-001\" \"2026-02-01\" \"2028-02-01\" \"dist/tfj-buycycle-v1.0.0/mmm-app\""
    exit 1
fi

CUSTOMER_NAME="$1"
LICENSE_ID="$2"
START_DATE="$3"
END_DATE="$4"
DIST_PATH="$5"

# Validate distribution path exists
if [ ! -d "$DIST_PATH" ]; then
    print_error "Distribution path does not exist: $DIST_PATH"
    exit 1
fi

print_info "Watermarking distribution for: ${CUSTOMER_NAME}"
print_info "License ID: ${LICENSE_ID}"
print_info "License Period: ${START_DATE} to ${END_DATE}"
print_info "Distribution Path: ${DIST_PATH}"
echo ""

# Watermark identifier (unique per customer)
WATERMARK_ID="${LICENSE_ID}-$(date +%Y%m%d)"

# Counter for files watermarked
FILES_WATERMARKED=0

# Function to add watermark to Python files
watermark_python() {
    local file="$1"
    local temp_file="${file}.tmp"
    
    # Check if already watermarked
    if grep -q "Licensed to: ${CUSTOMER_NAME}" "$file" 2>/dev/null; then
        return
    fi
    
    # Create watermark comment
    cat > "$temp_file" << EOF
# Licensed to: ${CUSTOMER_NAME}
# License ID: ${LICENSE_ID}
# Valid: ${START_DATE} to ${END_DATE}
# Watermark: ${WATERMARK_ID}

EOF
    
    # Append original file
    cat "$file" >> "$temp_file"
    
    # Replace original
    mv "$temp_file" "$file"
    
    FILES_WATERMARKED=$((FILES_WATERMARKED + 1))
}

# Function to add watermark to R files
watermark_r() {
    local file="$1"
    local temp_file="${file}.tmp"
    
    # Check if already watermarked
    if grep -q "Licensed to: ${CUSTOMER_NAME}" "$file" 2>/dev/null; then
        return
    fi
    
    # Create watermark comment
    cat > "$temp_file" << EOF
# Licensed to: ${CUSTOMER_NAME}
# License ID: ${LICENSE_ID}
# Valid: ${START_DATE} to ${END_DATE}
# Watermark: ${WATERMARK_ID}

EOF
    
    # Append original file
    cat "$file" >> "$temp_file"
    
    # Replace original
    mv "$temp_file" "$file"
    
    FILES_WATERMARKED=$((FILES_WATERMARKED + 1))
}

# Function to add watermark to Bash files
watermark_bash() {
    local file="$1"
    local temp_file="${file}.tmp"
    
    # Check if already watermarked
    if grep -q "Licensed to: ${CUSTOMER_NAME}" "$file" 2>/dev/null; then
        return
    fi
    
    # Get first line (shebang)
    head -n 1 "$file" > "$temp_file"
    
    # Add watermark after shebang
    cat >> "$temp_file" << EOF

# Licensed to: ${CUSTOMER_NAME}
# License ID: ${LICENSE_ID}
# Valid: ${START_DATE} to ${END_DATE}
# Watermark: ${WATERMARK_ID}

EOF
    
    # Append rest of file (skip first line)
    tail -n +2 "$file" >> "$temp_file"
    
    # Replace original
    mv "$temp_file" "$file"
    
    FILES_WATERMARKED=$((FILES_WATERMARKED + 1))
}

# Watermark Python files
print_info "Watermarking Python files..."
while IFS= read -r -d '' file; do
    watermark_python "$file"
done < <(find "$DIST_PATH/app" -name "*.py" -type f -print0 2>/dev/null)

# Watermark R files
print_info "Watermarking R files..."
while IFS= read -r -d '' file; do
    watermark_r "$file"
done < <(find "$DIST_PATH/r" -name "*.R" -type f -print0 2>/dev/null)

# Watermark Bash scripts
print_info "Watermarking Bash scripts..."
while IFS= read -r -d '' file; do
    if head -n 1 "$file" | grep -q "^#!.*bash"; then
        watermark_bash "$file"
    fi
done < <(find "$DIST_PATH/scripts" -name "*.sh" -type f -print0 2>/dev/null)

# Create watermark manifest
MANIFEST_FILE="${DIST_PATH}/WATERMARK_MANIFEST.txt"
print_info "Creating watermark manifest..."
cat > "$MANIFEST_FILE" << EOF
WATERMARK MANIFEST
==================

Licensed to: ${CUSTOMER_NAME}
License ID: ${LICENSE_ID}
License Period: ${START_DATE} to ${END_DATE}
Watermark ID: ${WATERMARK_ID}
Date Watermarked: $(date +%Y-%m-%d)

This distribution has been watermarked with unique identifiers in source files.
Any unauthorized redistribution can be traced back to this license.

Files Watermarked: ${FILES_WATERMARKED}

Watermark Format:
  Python files: Comments at start of file
  R files: Comments at start of file
  Bash scripts: Comments after shebang

IMPORTANT: This watermark does not affect functionality.
All code will execute normally.

For support or licensing questions, contact:
  Meshed Data Consulting
  fethu@mesheddata.com
EOF

print_info "============================================"
print_info "Watermarking completed successfully!"
print_info "============================================"
echo ""
print_info "Files watermarked: ${FILES_WATERMARKED}"
print_info "Manifest created: ${MANIFEST_FILE}"
echo ""
print_warning "NEXT STEPS:"
echo "1. Verify watermarks are present (spot check files)"
echo "2. Regenerate CHECKSUMS.txt to include watermarked files"
echo "3. Complete LICENSE_AUTHORIZATION.txt"
echo "4. Create final distribution archive"
echo "5. Securely deliver to customer"
echo ""
