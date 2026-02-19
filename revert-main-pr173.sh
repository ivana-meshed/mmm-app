#!/bin/bash
#
# Script to revert PR #173 from main branch
# 
# This script must be run by a repository maintainer with write access to main branch
#

set -e  # Exit on error

echo "=========================================="
echo "Revert PR #173 from Main Branch"
echo "=========================================="
echo ""

# Check if we're in the right directory
if [ ! -d ".git" ]; then
    echo "ERROR: This script must be run from the repository root"
    exit 1
fi

# Fetch latest changes
echo "Fetching latest changes from origin..."
git fetch origin main

# Checkout main branch
echo "Checking out main branch..."
git checkout main

# Pull latest changes
echo "Pulling latest changes..."
git pull origin main

# Verify we're on the right commit
CURRENT_COMMIT=$(git rev-parse HEAD)
EXPECTED_COMMIT="ec2fc29a7689d104a6192e04866b3b609a74798b"

echo ""
echo "Current HEAD: $CURRENT_COMMIT"
echo "Expected:     $EXPECTED_COMMIT"

if [ "$CURRENT_COMMIT" != "$EXPECTED_COMMIT" ]; then
    echo ""
    echo "WARNING: main branch is not at the expected commit!"
    echo "Expected: $EXPECTED_COMMIT (PR #173 merge commit)"
    echo "Actual:   $CURRENT_COMMIT"
    echo ""
    read -p "Do you want to continue anyway? (yes/no): " CONTINUE
    if [ "$CONTINUE" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
fi

# Revert the merge commit
echo ""
echo "Reverting PR #173 merge commit..."
echo "Running: git revert -m 1 ec2fc29a7689d104a6192e04866b3b609a74798b"
echo ""

git revert -m 1 ec2fc29a7689d104a6192e04866b3b609a74798b

# Check if revert was successful
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Revert commit created successfully!"
    echo ""
    echo "Files reverted:"
    git show --stat HEAD
    echo ""
    echo "=========================================="
    echo "Next steps:"
    echo "=========================================="
    echo "1. Review the revert commit above"
    echo "2. If everything looks good, push to main:"
    echo "   git push origin main"
    echo ""
    echo "3. Verify on GitHub that PR #173 changes are removed from main"
    echo ""
else
    echo ""
    echo "❌ Revert failed! Please check the error messages above."
    exit 1
fi
