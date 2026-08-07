# Hetzner Auction Dashboard Pipeline - Dockerfile
# Multi-stage build for minimal image size
#
# This container runs the continuous 10-minute refresh loop that:
# - Fetches Hetzner auction data
# - Joins with CPU benchmark scores
# - Publishes Parquet snapshot to R2
#
# Architecture: docs/plan/plan.md "Pipeline Run Lifecycle"

# Stage 1: Builder
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy requirements and install Python dependencies
COPY pipeline/requirements.txt .
COPY pipeline/pyproject.toml .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime image with Node.js
FROM python:3.11-slim

# Set labels
LABEL maintainer="jedarden <jedarden@example.com>"
LABEL description="Hetzner Auction Dashboard Pipeline - 10-minute refresh loop"
LABEL version="0.1.0"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIPELINE_LOG_LEVEL=INFO

# Install runtime dependencies including Node.js, git, and curl (for npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x using NodeSource setup script
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Verify installations
RUN node --version && npm --version && git --version

# Create non-root user for security
RUN groupadd -r pipeline && useradd -r -g pipeline pipeline

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy pipeline source code
COPY pipeline/src /app/src

# Copy benchmark-map data (read-only reference data)
COPY benchmark-map/ /app/benchmark-map/

# Change ownership to non-root user
RUN chown -R pipeline:pipeline /app

# Switch to non-root user
USER pipeline

# Health check - verify process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Run the pipeline with 10-minute refresh loop
# ENTRYPOINT ["python", "-m", "pipeline.main"]
CMD ["python", "-m", "pipeline.main"]
