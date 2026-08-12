#!/bin/bash
# Build script for Hetzner Auction Dashboard Pipeline container image
#
# Usage: ./build-image.sh [VERSION]
# Example: ./build-image.sh 0.1.0

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
IMAGE_NAME="${IMAGE_NAME:-registry.ardenone.com/hetzner-auction-pipeline}"
VERSION="${1:-0.1.10}"
FULL_TAG="${IMAGE_NAME}:${VERSION}"

echo "🐳 Building Hetzner Auction Dashboard Pipeline image"
echo "   Version: ${VERSION}"
echo "   Image: ${FULL_TAG}"
echo ""

# Change to project root
cd "${PROJECT_ROOT}"

# Verify Dockerfile exists
if [ ! -f "pipeline/Dockerfile" ]; then
    echo "❌ Error: Dockerfile not found at pipeline/Dockerfile"
    exit 1
fi

# Verify benchmark-map exists
if [ ! -d "benchmark-map" ]; then
    echo "❌ Error: benchmark-map directory not found"
    exit 1
fi

# Build the image
echo "Building Docker image..."
docker build \
    -f pipeline/Dockerfile \
    -t "${FULL_TAG}" \
    -t "${IMAGE_NAME}:latest" \
    .

echo ""
echo "✅ Build completed successfully!"
echo ""
echo "Image tags:"
echo "   ${FULL_TAG}"
echo "   ${IMAGE_NAME}:latest"
echo ""

# Ask if user wants to push
read -p "Push image to registry? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🚀 Pushing image to registry..."
    docker push "${FULL_TAG}"
    docker push "${IMAGE_NAME}:latest"
    echo "✅ Push completed!"
else
    echo "Skipping push. Image built locally only."
fi

echo ""
echo "To update the Kubernetes Deployment, edit:"
echo "   k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml"
echo ""
echo "Change the image tag to:"
echo "   ${FULL_TAG}"
