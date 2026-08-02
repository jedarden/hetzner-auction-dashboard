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

## Manual Provisioning Steps

### Step 1: Apply Terraform to Create R2 Bucket

The R2 bucket resource is already defined in Terraform. Apply it:

```bash
cd /home/coding/declarative-config/terraform/cloudflare
# Ensure terraform.tfvars exists with cloudflare_api_token and cloudflare_account_id
terraform init
terraform apply
```

Verify the bucket was created:
```bash
terraform output r2_hetzner_auction_data_endpoint
# Should output: https://<account-id>.r2.cloudflarestorage.com/hetzner-auction-data
```

### Step 2: Create R2 API Token in Cloudflare Dashboard

1. Navigate to: https://dash.cloudflare.com/profile/api-tokens
2. Click **"Create Token"** → **"Create Custom Token"**
3. Configure permissions:
   - **Permissions → Account → Cloudflare R2:** ✅ **Edit**
   - **Account Resources:**
     - ✅ **Include** → **Specific account** → *[Your Account ID]*
     - ✅ **Account Resources** → **All R2 buckets** (or specify `hetzner-auction-data`)
4. Set **TTL** to **Forever** (or appropriate duration)
5. Click **"Continue to summary"** → **"Create Token"**
6. **Copy the token immediately** - it won't be shown again!

**Note:** Your Cloudflare Account ID is visible in the dashboard URL or right sidebar.

### Step 3: Store Credentials in OpenBao

Access OpenBao on rs-manager cluster and store the secret:

```bash
# Port-forward to OpenBao
kubectl --kubeconfig=/home/coding/.kube/rs-manager.kubeconfig \
  port-forward -n openbao svc/openbao 8200:8200

# In another terminal, login to OpenBao and write the secret
export BAO_ADDR="http://localhost:8200"
bao kv put secret/rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token \
  R2_API_TOKEN="your-token-here" \
  R2_ACCOUNT_ID="your-account-id" \
  R2_BUCKET_NAME="hetzner-auction-data"
```

Verify the secret was written:
```bash
bao kv get secret/rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token
```

### Step 4: Configure CORS on R2 Bucket

**Required for DuckDB-WASM httpfs access.** Configure CORS via Cloudflare API or dashboard:

**Option 1 - Cloudflare API:**
```bash
# Get your account ID and API token from Cloudflare Dashboard
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/hetzner-auction-data/cors" \
  -H "Authorization: Bearer {api_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "AllowedOrigins": ["https://*.pages.dev", "https://your-custom-domain.com"],
    "AllowedMethods": ["GET", "HEAD", "OPTIONS"],
    "AllowedHeaders": ["Range", "Content-Range", "Content-Type"],
    "ExposeHeaders": ["Content-Range", "Accept-Ranges", "Content-Length"],
    "MaxAgeSeconds": 3600
  }'
```

**Option 2 - Cloudflare Dashboard:**
1. Navigate to: https://dash.cloudflare.com/{account_id}/r2/buckets
2. Click on **hetzner-auction-data**
3. Go to **Settings** → **CORS**
4. Add CORS rule with the above settings

### Step 5: Verify ExternalSecret Reconciliation

The ExternalSecret will automatically reconcile once OpenBao has the secret:

```bash
# Check ExternalSecret status
kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig \
  get externalsecret hetzner-auction-r2-externalsecret \
  -n hetzner-auction-dashboard

# Should show: Status: SecretSynced, Ready: true

# Check the resulting Kubernetes secret
kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig \
  get secret hetzner-auction-r2-secret \
  -n hetzner-auction-dashboard

# Verify secret keys exist (without outputting values)
kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig \
  get secret hetzner-auction-r2-secret \
  -n hetzner-auction-dashboard \
  -o jsonpath='{.data}' | jq 'keys'
```

Expected output: `["R2_ACCOUNT_ID", "R2_API_TOKEN", "R2_BUCKET_NAME"]`

## Completion Checklist

- [ ] Terraform applied → R2 bucket `hetzner-auction-data` exists
- [ ] R2 API token created in Cloudflare Dashboard
- [ ] Credentials stored in OpenBao at `rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token`
- [ ] CORS configured on R2 bucket for DuckDB-WASM access
- [ ] ExternalSecret reconciled successfully → Kubernetes secret exists
- [ ] ArgoCD Application `hetzner-auction-dashboard` is healthy

## Troubleshooting

**ExternalSecret not reconciling?**
- Check OpenBao secret path is correct: `rs-manager/hetzner-auction-dashboard/cloudflare/r2-api-token`
- Verify OpenBao is accessible from the cluster
- Check ExternalSecret events: `kubectl get externalsecret -n hetzner-auction-dashboard -o yaml`

**CORS errors in browser console?**
- Verify CORS is configured on the correct bucket
- Check allowed origins match your Cloudflare Pages domain
- Ensure exposed headers include `Content-Range`, `Accept-Ranges`, `Content-Length`

**R2 API token permissions issues?**
- Verify token has R2 Edit permissions
- Check token is scoped to correct account
- Ensure bucket exists and token has access to it

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
