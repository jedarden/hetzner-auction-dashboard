# Hetzner Auction Dashboard - Cloudflare Pages Deployment

This directory contains the Argo Workflow and Event resources for deploying the hetzner-auction-dashboard web/ directory to Cloudflare Pages via `wrangler pages deploy` (Direct Upload), following ADR-6.

## Architecture Decision: ADR-6

**Decision:** Deploy `web/` to Cloudflare Pages via Argo Workflow + `wrangler` Direct Upload instead of Cloudflare's git-integration auto-build.

**Rationale:**
- Cloudflare's git-integration meters production deployments (500/month on Free, 5,000/month on Pro)
- Direct Upload avoids this quota limitation
- Matches jedarden.com's existing deployment pattern
- Keeps all build/deploy in iad-ci Argo Workflows, not a third-party platform's CI
- Cloudflare's own documentation recommends Direct Upload for "bring your own CI" scenarios

## Components

### 1. WorkflowTemplate: `hetzner-auction-dashboard-web-deploy.yaml`
Defines the deployment workflow with two main steps:
- **checkout-and-build**: Clones repository and prepares web/ artifacts
- **deploy-to-cloudflare**: Deploys artifacts via `wrangler pages deploy`

### 2. Sensor: `hetzner-auction-dashboard-web-push-sensor.yaml`
Listens for git push events and triggers the workflow submission.

### 3. EventSource: `hetzner-auction-dashboard-web-eventsource.yaml`
Receives webhook events from Forgejo/Git repositories.

## Prerequisites

### Kubernetes Secrets

Create the required secret for Cloudflare API authentication:

```bash
# Create Cloudflare API token secret
kubectl create secret generic cloudflare-api-token \
  --from-literal=token=<your-cloudflare-api-token> \
  --namespace=argo
```

**Required Cloudflare API Token Permissions:**
- Account > Cloudflare Pages > Edit
- (Optional) Account > Cloudflare Pages > Delete Deployments (for cleanup)

**Token Scope:** Account (not Zone-level)

### Cloudflare Pages Project Setup

Before first deployment, create the Cloudflare Pages project:

```bash
# Install wrangler
npm install -g wrangler

# Authenticate
wrangler login

# Create project (one-time setup)
wrangler pages project create hetzner-auction-dashboard \
  --production-branch=main
```

## Deployment via ArgoCD

These resources should be deployed via GitOps to the `jedarden/declarative-config` repository:

```
jedarden/declarative-config/
└── k8s/
    └── iad-ci/
        └── argo-workflows/
            └── hetzner-auction-dashboard-web-deploy.yaml
        └── argo-events/
            ├── hetzner-auction-dashboard-web-eventsource.yaml
            └── hetzner-auction-dashboard-web-push-sensor.yaml
```

Apply to cluster via ArgoCD as part of the declarative-config sync.

## Manual Workflow Submission

As a fallback (or for testing), manually submit the workflow:

```bash
# Using Argo CLI
argo workflow submit \
  --s workflow-template-ref \
  -p branch=main \
  -p commit_sha=<specific-commit> \
  -p cloudflare_account_id=<account-id> \
  hetzner-auction-dashboard-web-deploy

# Or via Argo UI
# Navigate to Workflow Templates → hetzner-auction-dashboard-web-deploy → Submit
```

## Webhook Configuration

Configure webhook in Forgejo/Git repository settings:

**URL:** `https://<argo-events-hostname>/git-push/hetzner-auction-dashboard`  
**Content type:** `application/json`  
**Events:** Push events  
**Secret:** (configure matching secret in EventSource if auth is enabled)

## Testing

### Test Manual Deployment

```bash
# Submit with specific parameters
argo workflow submit \
  -s workflow-template-ref \
  -p branch=main \
  -p commit_sha=$(git rev-parse HEAD) \
  hetzner-auction-dashboard-web-deploy

# Watch workflow execution
argo watch <workflow-name>
```

### Test Webhook Trigger

1. Push a change to the main branch
2. Verify webhook is received in EventSource logs
3. Verify Sensor triggers workflow submission
4. Verify workflow completes successfully

## Troubleshooting

### Workflow Fails at Build Step
- Check git repository accessibility from cluster
- Verify branch/commit parameters
- Check Argo workspace pod logs

### Workflow Fails at Deploy Step  
- Verify Cloudflare API token permissions
- Check `CLOUDFLARE_ACCOUNT_ID` parameter
- Verify Cloudflare Pages project exists
- Check wrangler deploy logs for specific errors

### Webhook Not Triggering
- Verify EventSource is running and accessible
- Check webhook configuration in Forgejo/Git
- Verify Sensor filter conditions match webhook payload
- Check EventSource and Sensor logs

## Web Structure

The `web/` directory contains static HTML files with no build step required:
- Static HTML/CSS/JS dashboard
- DuckDB-WASM loaded via CDN
- Agentation toolbar mounted in isolated React root
- No npm/build dependencies

## Monitoring

After deployment, verify:
1. Cloudflare Pages deployment shows new deployment
2. Website loads correctly at configured domain
3. Dashboard functions properly with data from R2
4. Agentation toolbar appears and functions

## Related Documentation

- **ADR-6**: Full rationale for wrangler Direct Upload approach
- **jedarden.com website-build**: Reference implementation for similar pattern
- **Argo Workflows Documentation**: https://argoproj.github.io/argo-workflows/
- **Cloudflare Pages Direct Upload**: https://developers.cloudflare.com/pages/functions/wrangler-pages/

## Maintenance Notes

- **No build process**: The web/ directory is static HTML - no build step needed
- **Framework-free**: Dashboard uses no JS framework (per ADR-5)
- **Agentation isolation**: React only used for Agentation toolbar, not dashboard
- **GitOps managed**: All changes go through declarative-config, never live kubectl mutations
- **Webhook fallback**: Manual submission available if webhook has issues
