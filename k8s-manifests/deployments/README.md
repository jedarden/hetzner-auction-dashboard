# Hetzner Auction Dashboard Pipeline - Kubernetes Manifests

This directory contains Kubernetes manifests for deploying the Hetzner Auction Dashboard pipeline.

## Location

These manifests should be stored in the `jedarden/declarative-config` repository:

```
jedarden/declarative-config/
└── k8s/
    └── pipeline/
        └── hetzner-auction-dashboard-pipeline.yaml
```

## GitOps Deployment

These manifests are synced by ArgoCD (GitOps) - **never a live kubectl mutation**.

## Components

### Deployment (`hetzner-auction-dashboard-pipeline.yaml`)

- **Namespace**: `hetzner-auction-dashboard`
- **Replicas**: `1` (house rule: no Job/CronJob)
- **Image**: `registry.ardenone.com/hetzner-auction-pipeline:0.1.0`
- **Refresh Loop**: 10-minute internal loop (no CronJob)

## Architecture

See: `docs/plan/plan.md` - "Pipeline Run Lifecycle" and "Concurrency Model"

### Pipeline Flow

1. **Fetch** - Hetzner auction data every 10 minutes
2. **Match** - CPU strings against benchmark data
3. **Compute** - Derived cost metrics
4. **Write** - Parquet snapshot to temp R2 key
5. **Verify** - Confirm artifact is valid
6. **Swap** - Atomic temp-key-then-swap to live key

### Safety Guarantees

- **Single writer** (`replicas: 1`)
- **Temp-key-then-swap** - Last swap wins safely
- **Verify-before-publish** - Never touches live key if verification fails
- **Graceful degradation** - Failed runs keep serving last snapshot

## Configuration

### Environment Variables

Set via ConfigMap `pipeline-config`:

```yaml
PIPELINE_LOG_LEVEL: "INFO"
R2_ENDPOINT_URL: "https://r2.cloudflarestorage.com"
PARQUET_SNAPSHOT_KEY: "current_snapshot.parquet"
UNMATCHED_REPORT_KEY: "unmatched-cpus.json"
```

### Secrets (OpenBau-backed)

R2 credentials via ExternalSecret:

```yaml
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
```

## Deployment Steps

1. **Push to declarative-config**:
   ```bash
   cd jedarden/declarative-config
   cp /path/to/hetzner-auction-dashboard/k8s-manifests/deployments/*.yaml k8s/pipeline/
   git add k8s/pipeline/
   git commit -m "Add Hetzner auction dashboard pipeline deployment"
   git push
   ```

2. **ArgoCD sync**:
   - ArgoCD detects changes in declarative-config
   - Auto-syncs to cluster
   - Pipeline Deployment created/updated

3. **Verify**:
   ```bash
   kubectl get pods -n hetzner-auction-dashboard
   kubectl logs -n hetzner-auction-dashboard deployment/hetzner-auction-pipeline
   ```

## House Rules

From `docs/plan/plan.md`:

- ✅ **Deployment** (long-lived with internal refresh loop)
- ❌ **No Job/CronJob** (house rule)
- ✅ **GitOps only** (declarative-config + ArgoCD)
- ❌ **No live kubectl mutations**

## Monitoring

### Health Checks

- **Liveness**: Process check (exec probe)
- **Readiness**: Process check (exec probe)

### Logging

Pipeline logs show:
- Cycle start/complete timestamps
- Listings fetched, matched, unmatched counts
- R2 publish confirmations
- Error details if cycle fails

### Metrics (Optional)

ServiceMonitor can be enabled for Prometheus scraping:

```yaml
# Uncomment ServiceMonitor section in deployment manifest
```

## Troubleshooting

### Pipeline not updating

1. Check pod is running:
   ```bash
   kubectl get pods -n hetzner-auction-dashboard
   ```

2. Check logs for errors:
   ```bash
   kubectl logs -n hetzner-auction-dashboard deployment/hetzner-auction-pipeline
   ```

3. Verify R2 credentials:
   ```bash
   kubectl get secret -n hetzner-auction-dashboard r2-credentials
   ```

### R2 publish failures

- Check R2 bucket exists and is writable
- Verify ExternalSecret is syncing from OpenBao
- Check network egress to Cloudflare R2

### Empty benchmark-map

- Verify benchmark-map volume is mounted
- Check init container ran successfully
- Verify benchmark-map data exists in image

## References

- **Plan**: `docs/plan/plan.md`
- **Architecture**: "Pipeline Run Lifecycle", "Concurrency Model"
- **ADR-1**: Cloudflare R2 over self-hosted object storage
- **ADR-4**: Short max-age Cache-Control over CDN purge

## Security Notes

- Runs as non-root user (UID 1000)
- R2 credentials via ExternalSecret (OpenBau-backed)
- No privilege escalation
- Read-only root filesystem (where possible)
- Minimal container image (python:3.11-slim)
