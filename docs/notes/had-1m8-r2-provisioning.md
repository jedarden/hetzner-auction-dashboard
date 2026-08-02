---
name: had-1m8-r2-provisioning
description: R2 bucket and API token provisioning for hetzner-auction-dashboard
metadata:
  type: project
---

# R2 Bucket + API Token Provisioning

## Overview

Provisioned Cloudflare R2 bucket and OpenBao-backed ExternalSecret for hetzner-auction-dashboard pipeline, following established patterns from other services (cloudflare-pages-externalsecret.yml, etc.).

## R2 Bucket

**Name:** `hetzner-auction-data`  
**Location:** ENAM (Eastern North America)  
**Endpoint:** `https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com/hetzner-auction-data`

### Terraform Configuration

Added to `/home/coding/declarative-config/terraform/cloudflare/storage.tf`:
```hcl
resource "cloudflare_r2_bucket" "hetzner_auction_data" {
  account_id = var.cloudflare_account_id
  name       = "hetzner-auction-data"
  location   = "ENAM"
}
```

Output added to `/home/coding/declarative-config/terraform/cloudflare/outputs.tf`:
```hcl
output "r2_hetzner_auction_data_endpoint" {
  description = "R2 bucket endpoint for hetzner-auction-data"
  value       = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com/hetzner-auction-data"
}
```

## OpenBao Secret Path

**Path:** `secret/rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token`

### Secret Properties

The OpenBao secret should contain the following properties:

| Property | Description | Example |
|----------|-------------|---------|
| `R2_API_TOKEN` | Bucket-scoped API token for hetzner-auction-data | `cloudflare-api-token-...` |
| `R2_ACCOUNT_ID` | Cloudflare account ID | `abc123def456...` |
| `R2_BUCKET_NAME` | R2 bucket name | `hetzner-auction-data` |

### API Token Permissions

The R2 API token must be **bucket-scoped to `hetzner-auction-data` only** with the following permissions:

- **PUT** — Write temp objects during temp-key-then-swap lifecycle
- **COPY** — Atomic swap from temp key to live key
- **DELETE** — Remove old snapshot after swap
- **GET** — Verification reads before/after swap

**Required:** Bucket-scoped token, NOT account-wide. This limits blast radius if leaked.

## ExternalSecret Configuration

**File:** `/home/coding/declarative-config/k8s/iad-ci/hetzner-auction-dashboard/r2-externalsecret.yml`

**Target Kubernetes Secret:** `hetzner-auction-r2-secret` (namespace: `hetzner-auction-dashboard`)

**Secret Keys:**
- `R2_API_TOKEN` — The bucket-scoped API token
- `R2_ACCOUNT_ID` — Cloudflare account ID for endpoint construction
- `R2_BUCKET_NAME` — Bucket name (also available via config)

**Refresh Interval:** 1 hour

## ArgoCD Integration

**Application:** `hetzner-auction-dashboard`  
**Namespace:** `hetzner-auction-dashboard`  
**Source Path:** `k8s/iad-ci/hetzner-auction-dashboard/`

Auto-sync enabled with prune and self-heal.

## CORS Configuration (Required for DuckDB-WASM)

The R2 bucket **must** have CORS configured to allow DuckDB-WASM httpfs range requests from the Cloudflare Pages domain. This is typically configured via Cloudflare API or Terraform.

**Required CORS rules:**
- Allow origins: Cloudflare Pages domain (e.g., `*.pages.dev` or custom domain)
- Allow methods: GET, HEAD, OPTIONS
- Allow headers: Range, Content-Range, Content-Type
- Expose headers: Content-Range, Accept-Ranges, Content-Length

## Next Steps

1. **Apply Terraform changes** to create the R2 bucket:
   ```bash
   cd /home/coding/declarative-config/terraform/cloudflare
   terraform apply
   ```

2. **Generate R2 API token** in Cloudflare dashboard:
   - Go to Cloudflare Dashboard → R2 → hetzner-auction-data → R2 API Tokens
   - Create bucket-scoped token with PUT, COPY, DELETE, GET permissions
   - Note the token, account ID, and bucket name

3. **Store in OpenBao** at `secret/rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token`:
   ```bash
   # Via OpenBao CLI or UI
   vault kv put secret/rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token \
     R2_API_TOKEN="<token>" \
     R2_ACCOUNT_ID="<account-id>" \
     R2_BUCKET_NAME="hetzner-auction-data"
   ```

4. **Configure CORS** on the R2 bucket for DuckDB-WASM httpfs access

5. **Apply Kubernetes manifests** via ArgoCD (auto-sync) or manually:
   ```bash
   kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig \
     apply -f /home/coding/declarative-config/k8s/iad-ci/hetzner-auction-dashboard/
   ```

## Files Created/Modified

### Created
- `/home/coding/declarative-config/k8s/iad-ci/hetzner-auction-dashboard/r2-externalsecret.yml`
- `/home/coding/declarative-config/k8s/iad-ci/hetzner-auction-dashboard/namespace.yml`
- `/home/coding/declarative-config/k8s/iad-ci/hetzner-auction-dashboard-application.yml`
- `/home/coding/hetzner-auction-dashboard/docs/notes/had-1m8-r2-provisioning.md` (this file)

### Modified
- `/home/coding/declarative-config/terraform/cloudflare/storage.tf` (added R2 bucket)
- `/home/coding/declarative-config/terraform/cloudflare/outputs.tf` (added output)

## Related Documentation

- **Plan:** `/home/coding/hetzner-auction-dashboard/docs/plan/plan.md`
- **ADR-1:** R2 over self-hosted Garage/SeaweedFS decision
- **Pipeline Run Lifecycle:** temp-key-then-swap pattern details
