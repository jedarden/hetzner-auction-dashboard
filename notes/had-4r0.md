# Bead had-4r0: Argo Workflow for Cloudflare Pages Deployment

## Summary

Successfully built the complete Argo Workflow infrastructure for deploying `web/` to Cloudflare Pages via `wrangler pages deploy` (Direct Upload), following ADR-6 and modeled on jedarden.com's website-build pattern.

## What Was Created

### 1. Core Workflow Infrastructure
- **WorkflowTemplate** (`hetzner-auction-dashboard-web-deploy.yaml`)
  - Two-step workflow: checkout-and-build + deploy-to-cloudflare
  - Uses `wrangler pages deploy` for Direct Upload (ADR-6 compliant)
  - No build step needed - web/ is static HTML
  - Configurable parameters for repo, branch, commit, Cloudflare settings

### 2. Webhook Integration
- **EventSource** (`hetzner-auction-dashboard-web-eventsource.yaml`)
  - Receives webhook events from Forgejo/Git
  - Configurable endpoint: `/git-push/hetzner-auction-dashboard`
  - Filter for main branch only

- **Sensor** (`hetzner-auction-dashboard-web-push-sensor.yaml`)
  - Listens for push events via EventSource
  - Automatically triggers workflow on main branch pushes
  - Manual submission fallback available

### 3. Supporting Resources
- **RBAC** (`hetzner-auction-dashboard-web-rbac.yaml`)
  - ServiceAccount for Argo Events Sensor
  - Role with permissions to submit workflows
  - RoleBinding connecting ServiceAccount to Role

### 4. Documentation
- **Comprehensive README** in argo-events directory
  - Architecture overview and ADR-6 rationale
  - Prerequisites and setup instructions
  - Webhook configuration guide
  - Troubleshooting section

- **Integration Guide** (k8s/iad-ci/README.md)
  - Complete setup walkthrough
  - declarative-config integration instructions
  - Monitoring and maintenance procedures
  - Security considerations

## Architecture Compliance

✅ **ADR-6 Compliant**: Uses `wrangler pages deploy` Direct Upload, avoiding Cloudflare's git-integration quota
✅ **Matches jedarden.com pattern**: Follows established website-build WorkflowTemplate structure  
✅ **GitOps managed**: All changes go through declarative-config, no live kubectl mutations
✅ **Webhook + manual fallback**: Automatic deployment on push, manual submission available
✅ **Pure devops work**: Independent of dashboard feature code, no blockers

## Integration Path

These resources should be copied to `jedarden/declarative-config`:

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
            └── cloudflare-api-token-sealed.yaml (to be created)
```

## Prerequisites for Production

1. **Cloudflare Pages project** created via `wrangler pages project create`
2. **Cloudflare API token** with Pages Edit permissions
3. **Kubernetes secret** created (preferably via SealedSecret)
4. **Repository webhook** configured in Forgejo/Git settings
5. **ArgoCD sync** configured for declarative-config repository

## Testing Validation

### Manual Workflow Submission
```bash
argo workflow submit \
  -s workflow-template-ref \
  -p branch=main \
  -p commit_sha=$(git rev-parse HEAD) \
  hetzner-auction-dashboard-web-deploy
```

### Webhook Trigger Test
```bash
# Push to main branch and verify:
# 1. EventSource receives webhook
# 2. Sensor triggers workflow submission  
# 3. Workflow completes successfully
# 4. Cloudflare Pages shows new deployment
```

## Next Steps

1. **Copy resources to declarative-config**: Move all YAML files to jedarden/declarative-config
2. **Create Cloudflare project**: Run `wrangler pages project create`  
3. **Generate API token**: Create token with Pages Edit permissions
4. **Create Kubernetes secret**: Use SealedSecret for production
5. **Configure webhook**: Add webhook in repository settings
6. **Test deployment**: Verify manual and webhook-based deployment
7. **Monitor first production deployment**: Validate end-to-end pipeline

## House Pattern Compliance

- ✅ Follows jedarden.com's website-build pattern
- ✅ Uses iad-ci cluster via ArgoCD
- ✅ No live kubectl mutations (GitOps only)
- ✅ Managed in declarative-config repository
- ✅ Webhook + manual submission fallback
- ✅ ADR-6 Direct Upload approach

## Files Created

1. `pipeline/k8s/iad-ci/argo-workflows/hetzner-auction-dashboard-web-deploy.yaml`
2. `pipeline/k8s/iad-ci/argo-events/hetzner-auction-dashboard-web-eventsource.yaml`
3. `pipeline/k8s/iad-ci/argo-events/hetzner-auction-dashboard-web-push-sensor.yaml`
4. `pipeline/k8s/iad-ci/argo-events/hetzner-auction-dashboard-web-rbac.yaml`
5. `pipeline/k8s/iad-ci/argo-events/README.md`
6. `pipeline/k8s/iad-ci/README.md`
7. `notes/had-4r0.md` (this file)

## Status: ✅ COMPLETE

All components for the Argo Workflow deployment infrastructure have been created and documented. The bead is ready to be committed and closed.