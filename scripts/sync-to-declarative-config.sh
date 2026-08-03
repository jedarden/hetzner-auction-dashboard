#!/bin/bash
# GitOps Sync Script for hetzner-auction-dashboard Pipeline Deployment
# This script copies the Kubernetes manifest to declarative-config for ArgoCD deployment
#
# Usage: ./scripts/sync-to-declarative-config.sh
# Prerequisites: Access to jedarden/declarative-config repository

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Hetzner Auction Dashboard GitOps Sync ===${NC}"
echo "This script will deploy the pipeline to iad-ci cluster via GitOps"
echo ""

# Configuration
SOURCE_REPO="hetzner-auction-dashboard"
SOURCE_MANIFEST="k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml"
TARGET_REPO_BASE="$HOME/declarative-config"
TARGET_PATH="k8s/pipeline/hetzner-auction-dashboard-pipeline.yaml"
CLUSTER="iad-ci"

# Check if source manifest exists
if [ ! -f "$SOURCE_MANIFEST" ]; then
    echo -e "${RED}Error: Source manifest not found: $SOURCE_MANIFEST${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Source manifest found: $SOURCE_MANIFEST${NC}"

# Check if declarative-config repository exists
if [ ! -d "$TARGET_REPO_BASE" ]; then
    echo -e "${YELLOW}Warning: declarative-config repository not found at $TARGET_REPO_BASE${NC}"
    echo "Please clone the repository first:"
    echo "  git clone git@git.ardenone.com:jedarden/declarative-config.git ~/declarative-config"
    exit 1
fi

echo -e "${GREEN}✓ Target repository found: $TARGET_REPO_BASE${NC}"

# Create target directory if it doesn't exist
TARGET_DIR="$TARGET_REPO_BASE/k8s/pipeline"
mkdir -p "$TARGET_DIR"
echo -e "${GREEN}✓ Target directory ready: $TARGET_DIR${NC}"

# Copy manifest to declarative-config
cp "$SOURCE_MANIFEST" "$TARGET_REPO_BASE/$TARGET_PATH"
echo -e "${GREEN}✓ Manifest copied to declarative-config${NC}"

# Change to declarative-config directory
cd "$TARGET_REPO_BASE"

# Check if there are changes to commit
if git diff --quiet "$TARGET_PATH"; then
    echo -e "${YELLOW}No changes detected. Manifest is already up to date.${NC}"
    exit 0
fi

# Add and commit changes
git add "$TARGET_PATH"
echo -e "${GREEN}✓ Changes staged for commit${NC}"

# Create commit message
COMMIT_MSG="Add hetzner-auction-dashboard pipeline deployment

Cluster: iad-ci
Namespace: hetzner-auction-dashboard
Image: registry.ardenone.com/hetzner-auction-pipeline:0.1.0

Deployed via GitOps (had-307)
Ref: notes/had-307-cluster-deployment.md"

git commit -m "$COMMIT_MSG"
echo -e "${GREEN}✓ Changes committed locally${NC}"

# Ask before pushing
echo ""
read -p "Push to declarative-config? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    git push
    echo -e "${GREEN}✓ Changes pushed to declarative-config${NC}"
    echo ""
    echo "ArgoCD will automatically sync these changes to the $CLUSTER cluster."
    echo "Monitor the deployment with:"
    echo "  kubectl get pods -n hetzner-auction-dashboard"
    echo "  kubectl logs -n hetzner-auction-dashboard deployment/hetzner-auction-pipeline"
else
    echo -e "${YELLOW}Push skipped. Commit exists locally. Push manually when ready:${NC}"
    echo "  cd $TARGET_REPO_BASE && git push"
fi

echo ""
echo -e "${GREEN}=== GitOps Sync Complete ===${NC}"
echo "Next steps:"
echo "1. Monitor ArgoCD sync status"
echo "2. Verify pod startup: kubectl get pods -n hetzner-auction-dashboard"
echo "3. Check logs for 10-minute cycles: kubectl logs -n hetzner-auction-dashboard deployment/hetzner-auction-pipeline"
echo "4. Verify 3 consecutive scheduled runs (30 minutes total)"
echo ""
echo "See notes/had-307-cluster-deployment.md for full deployment details"