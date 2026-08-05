# R2 Bucket + API Token Provisioning Requirements

**Task:** had-1m8 - Provision R2 bucket + API token (OpenBao/ExternalSecret)

## Overview

Create Cloudflare R2 bucket and generate bucket-scoped API token stored as OpenBao-backed ExternalSecret following existing patterns. This is pure infrastructure work with no code dependencies.

## Background Context

This task supports the hetzner-auction-dashboard pipeline which publishes Parquet files to R2 for client-side consumption via DuckDB-WASM. The architecture requires:

1. **R2 Bucket** - Storage for Parquet snapshots and unmatched-cpu reports
2. **API Token** - Bucket-scoped token for pipeline to push data
3. **ExternalSecret** - OpenBao-backed secret storage following cluster patterns

## Previous Attempt Context

A previous implementation attempt was made but was undone because it was "based on a stale assumption." The changes to the `declarative-config` repository (terraform/cloudflare/storage.tf, outputs.tf, k8s manifests) were stashed rather than committed and need to be reviewed before any new implementation.

## Requirements from Architecture

From `docs/plan/plan.md`:

### Storage Requirements
- **Bucket Location:** Cloudflare R2 (chosen over self-hosted Garage/SeaweedFS)
- **Access Pattern:** Public HTTPS with CORS + HTTP range-request support
- **Publishing Pattern:** Temp-key-then-swap lifecycle for atomic updates
- **Files Stored:**
  - Current snapshot Parquet file (well-known live key)
  - `unmatched-cpus.json` report (well-known live key)
  - Future: Historical Parquet files (timestamped, append-only)

### API Token Requirements
- **Scope:** Bucket-level (not per-object) to allow both snapshot and report publishing
- **Permissions:** PUT, COPY, DELETE for temp-key-then-swap lifecycle
- **Storage:** OpenBao-backed ExternalSecret (never logged by pipeline)

### Security Requirements
- Token stored as ExternalSecret matching existing cluster patterns
- Never appears in pipeline logs
- Rotated on ad-hoc cadence matching environment standards
- No fixed rotation schedule needed for personal tool

## Implementation Approach

### Step 1: Cloudflare R2 Bucket Creation

Create R2 bucket with appropriate configuration:
- **Bucket Name:** TBD (follow naming conventions)
- **Location:** Choose optimal region for latency
- **Public Access:** Configure for public HTTPS reads (client access)
- **CORS Configuration:** Enable for DuckDB-WASM httpfs range requests
- **Lifecycle Rules:** Not needed for v1 (consider for v2 history retention)

### Step 2: API Token Generation

Create bucket-scoped R2 API token:
- **Token Permissions:** 
  - `Object Read` (for verification)
  - `Object Write` (for temp key writes)
  - `Object Delete` (for swap cleanup)
- **Scope:** Single bucket only
- **TTL:** Follow environment token rotation practices
- **Documentation:** Record token creation date for rotation tracking

### Step 3: ExternalSecret Configuration

Create ExternalSecret manifest following cluster patterns:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: hetzner-auction-dashboard-r2-credentials
  namespace:iad-ci  # Or appropriate namespace
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: openbao-secret-store  # Follow cluster naming
    kind: SecretStore
  target:
    name: r2-credentials-secret
    creationPolicy: Owner
  data:
    - secretKey: R2_ACCOUNT_ID
      remoteRef:
        key: kv/data/hetzner-auction-dashboard/r2
        property: account_id
    - secretKey: R2_ACCESS_KEY_ID
      remoteRef:
        key: kv/data/hetzner-auction-dashboard/r2
        property: access_key_id
    - secretKey: R2_SECRET_ACCESS_KEY
      remoteRef:
        key: kv/data/hetzner-auction-dashboard/r2
        property: secret_access_key
    - secretKey: R2_BUCKET_NAME
      remoteRef:
        key: kv/data/hetzner-auction-dashboard/r2
        property: bucket_name
```

### Step 4: OpenBao Secret Storage

Store credentials in OpenBao:
- **Path:** `kv/data/hetzner-auction-dashboard/r2`
- **Fields:**
  - `account_id`: Cloudflare Account ID
  - `access_key_id`: R2 Token Access Key ID
  - `secret_access_key`: R2 Token Secret Access Key
  - `bucket_name`: R2 Bucket Name

### Step 5: Pipeline Integration

Update pipeline deployment to use ExternalSecret:
- Reference `r2-credentials-secret` in pipeline Deployment
- Environment variables for R2 configuration
- Ensure token is never logged (use appropriate logging safeguards)

## File Locations in declarative-config

Based on previous attempt, infrastructure should be created in:

**Terraform Files:**
- `terraform/cloudflare/storage.tf` - R2 bucket resource
- `terraform/cloudflare/outputs.tf` - Bucket name and credentials output

**Kubernetes Manifests:**
- `k8s/iad-ci/hetzner-auction-dashboard/externalsecret.yaml` - ExternalSecret manifest
- `k8s/iad-ci/hetzner-auction-dashboard/deployment.yaml` - Pipeline deployment using secret

**OpenBao Configuration:**
- Store credentials via OpenBao API or cluster automation

## Validation Checklist

- [ ] R2 bucket created with appropriate public access
- [ ] CORS configured for DuckDB-WASM httpfs range requests
- [ ] Bucket-scoped API token generated with correct permissions
- [ ] Credentials stored in OpenBao at correct path
- [ ] ExternalSecret manifest created following cluster patterns
- [ ] Pipeline Deployment configured to use ExternalSecret
- [ ] Test write/verify/swap lifecycle works end-to-end
- [ ] Documentation updated with bucket/token details
- [ ] Token creation date recorded for rotation tracking

## Next Steps

1. **Review Stashed Changes:** Examine the stashed declarative-config changes to understand what was attempted before
2. **Consult Existing Patterns:** Review other ExternalSecret examples in the cluster
3. **Implement Infrastructure:** Create Terraform and Kubernetes manifests
4. **Test Connectivity:** Verify pipeline can successfully publish to R2
5. **Document:** Record implementation details for future maintenance

## Dependencies

- Access to `declarative-config` repository (jedarden/declarative-config)
- Cloudflare account with R2 enabled
- OpenBao access and cluster credentials
- Understanding of existing ExternalSecret patterns in the cluster
- ArgoCD for GitOps deployment

## Completion Criteria

This task is complete when:
- R2 bucket exists and is accessible via public HTTPS
- API token is stored in OpenBao
- ExternalSecret is deployed via ArgoCD
- Pipeline can successfully publish test data to R2
- All changes are committed to declarative-config and synced

---

**Status:** Ready for implementation once declarative-config access is available and stashed changes are reviewed.
**Created:** 2026-08-02
**Task:** had-1m8