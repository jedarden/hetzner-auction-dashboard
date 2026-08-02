# Hetzner Auction Dashboard - CI/CD Integration Guide

## Overview

This document describes the complete CI/CD setup for deploying the Hetzner Auction Dashboard, following the house pattern of GitOps via ArgoCD to `jedarden/declarative-config`.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Forgejo/Git   │────▶│  Argo Events    │────▶│  Argo Workflow  │
│   (Push Event)  │     │  (Webhook)      │     │  (Build+Deploy) │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
                                                   ┌─────────────────┐
                                                   │ Cloudflare Pages│
                                                   │ (Direct Upload) │
                                                   └─────────────────┘
```

## Integration with declarative-config

All Kubernetes resources should be managed in the `jedarden/declarative-config` repository:

```
jedarden/declarative-config/
└── k8s/
    └── iad-ci/
        └── argo-workflows/
            └── hetzner-auction-dashboard-web-deploy.yaml
        └── argo-events/
            ├── hetzner-auction-dashboard-web-eventsource.yaml
            ├── hetzner-auction-dashboard-web-push-sensor.yaml
            └── hetzner-auction-dashboard-web-rbac.yaml
        └── secrets/
            └── cloudflare-api-token-sealed.yaml (SealedSecret)
```

## Setup Steps

### 1. Cloudflare Pages Setup

```bash
# Create Cloudflare Pages project
wrangler pages project create hetzner-auction-dashboard \
  --production-branch=main

# Note the account ID from the output
```

### 2. Create Kubernetes Secrets

```bash
# Method 1: Direct secret creation (for testing)
kubectl create secret generic cloudflare-api-token \
  --from-literal=token=<your-cloudflare-api-token> \
  --namespace=argo

# Method 2: SealedSecret (recommended for production)
# Create sealed secret from your workstation
kubectl create secret generic cloudflare-api-token \
  --from-literal=token=<your-cloudflare-api-token> \
  --namespace=argo \
  --dry-run=client \
  -o yaml | kubeseal --format yaml > sealed-secret.yaml

# Commit sealed secret to declarative-config repository
```

### 3. Configure Repository Webhook

In Forgejo/Git repository settings:

**Webhook URL:** `https://<argo-events-hostname>/git-push/hetzner-auction-dashboard`  
**HTTP Method:** POST  
**Content Type:** application/json  
**Events:** Push events  
**Active:** ✅  

### 4. Deploy to declarative-config

Copy the generated resources to the declarative-config repository:

```bash
# From hetzner-auction-dashboard repo
cp pipeline/k8s/iad-ci/argo-workflows/hetzner-auction-dashboard-web-deploy.yaml \
   ~/declarative-config/k8s/iad-ci/argo-workflows/

cp pipeline/k8s/iad-ci/argo-events/*.yaml \
   ~/declarative-config/k8s/iad-ci/argo-events/

# Commit and push to declarative-config
cd ~/declarative-config
git add k8s/iad-ci/argo-workflows/ k8s/iad-ci/argo-events/
git commit -m "Add hetzner-auction-dashboard Cloudflare Pages deployment workflow"
git push
```

### 5. Verify ArgoCD Sync

```bash
# Check that resources sync to cluster
kubectl get workflowtemplate -n argo
kubectl get sensor -n argo
kubectl get eventsource -n argo

# Verify webhook endpoint is accessible
curl -X POST https://<argo-events-hostname>/git-push/hetzner-auction-dashboard \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
```

## Usage

### Automatic Deployment on Push

When code is pushed to the `main` branch:
1. Forgejo/Git sends webhook to Argo Events
2. Sensor validates it's for the correct repository and branch
3. Workflow is submitted to Argo Workflows
4. Workflow clones repo, prepares artifacts, and deploys to Cloudflare Pages

### Manual Deployment

```bash
# Using Argo CLI
argo workflow submit \
  -s workflow-template-ref \
  -p branch=main \
  -p commit_sha=$(git rev-parse HEAD) \
  -p cloudflare_account_id=<account-id> \
  hetzner-auction-dashboard-web-deploy

# Or via Argo UI: Workflow Templates → Submit
```

## Monitoring and Troubleshooting

### Check Workflow Status

```bash
# List recent workflows
argo list --instance-id --since 1h

# Get workflow details
argo get <workflow-name>

# Watch workflow execution
argo watch <workflow-name>

# Check workflow logs
argo logs <workflow-name>
```

### Check EventSource and Sensor

```bash
# Check EventSource status
kubectl get eventsource forgejo-webhook -n argo

# Check Sensor status  
kubectl get sensor hetzner-auction-dashboard-web-push -n argo

# View EventSource logs
kubectl logs -n argo -l event-source-name=forgejo-webhook

# View Sensor logs
kubectl logs -n argo -l sensor-name=hetzner-auction-dashboard-web-push
```

### Verify Deployment

```bash
# Check Cloudflare Pages deployments
wrangler pages deployment list --project-name=hetzner-auction-dashboard

# Test website loads correctly
curl -I https://<your-domain>.pages.dev

# Check Agentation toolbar appears
# (Inspect page for Agentation elements)
```

## Common Issues

### Issue: Workflow not triggered by webhook
**Solution:**
- Verify webhook configuration in Forgejo/Git
- Check EventSource logs for webhook reception
- Verify Sensor filter conditions match webhook payload
- Test webhook manually with curl

### Issue: Deployment fails at build step
**Solution:**
- Check git repository is accessible from cluster
- Verify branch and commit SHA parameters
- Check workspace pod has network access

### Issue: Deployment fails at wrangler deploy step
**Solution:**
- Verify Cloudflare API token permissions
- Check `CLOUDFLARE_ACCOUNT_ID` is correct
- Verify Cloudflare Pages project exists
- Check wrangler output for specific errors

### Issue: Website doesn't load after deployment
**Solution:**
- Verify deployment completed successfully in Cloudflare dashboard
- Check DNS settings for custom domain (if configured)
- Check Cloudflare Pages project logs
- Verify R2 bucket contains current Parquet file

## Testing Changes

### Test Local Changes

```bash
# Make changes to web/ files
vim web/index.html

# Test deployment manually
argo workflow submit \
  -s workflow-template-ref \
  -p branch=$(git branch --show-current) \
  -p commit_sha=$(git rev-parse HEAD) \
  hetzner-auction-dashboard-web-deploy

# Watch workflow
argo watch <workflow-name>
```

### Rollback Deployment

```bash
# List recent deployments
wrangler pages deployment list --project-name=hetzner-auction-dashboard

# Rollback to specific deployment
wrangler pages deployment rollback \
  --project-name=hetzner-auction-dashboard \
  --deployment-id=<previous-deployment-id>
```

## Maintenance

### Update Workflow Parameters

Edit the WorkflowTemplate in declarative-config:
```yaml
# Modify parameters as needed
- name: cloudflare_project_name
  value: hetzner-auction-dashboard
```

ArgoCD will sync changes automatically.

### Rotate Cloudflare Token

```bash
# Create new token in Cloudflare dashboard
# Update secret
kubectl create secret generic cloudflare-api-token \
  --from-literal=token=<new-token> \
  --namespace=argo \
  --dry-run=client -o yaml | kubeseal --format yaml > sealed-secret.yaml

# Commit updated sealed secret to declarative-config
```

## Related Documentation

- **ADR-6**: Rationale for Direct Upload vs Cloudflare git integration
- **jedarden.com website-build**: Reference implementation
- **Argo Workflows**: https://argoproj.github.io/argo-workflows/
- **Argo Events**: https://argoproj.github.io/argo-events/
- **Cloudflare Pages**: https://developers.cloudflare.com/pages/

## Security Considerations

1. **API Token Scope**: Cloudflare token should have minimal required permissions
2. **Secret Management**: Use SealedSecrets or ExternalSecret for production
3. **Webhook Authentication**: Enable webhook secret in production
4. **RBAC**: Sensor only has permissions to submit workflows
5. **Network Access**: Cluster needs egress to git.ardenone.com and Cloudflare API

## Future Enhancements

- Add staging environment deployment
- Implement smoke tests after deployment
- Add deployment metrics and monitoring
- Configure automatic rollback on failure
- Add deployment notifications