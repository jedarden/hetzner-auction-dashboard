# Pipeline Deployment to Production - had-307

**Task:** Deploy pipeline to production Rackspace Spot cluster

**Date:** 2026-08-03

## Open Question Resolution: Cluster Choice

**Question:** Which Rackspace Spot cluster hosts the pipeline?

**Answer:** `iad-ci` cluster

**Rationale:**
1. **Existing Infrastructure:** The Argo Workflow and Events resources are already structured under `pipeline/k8s/iad-ci/`, indicating iad-ci is the standard cluster for CI/CD workloads.
2. **Proximity to Cloudflare R2:** IAD (Washington DC region) provides good latency to Cloudflare's edge locations for R2 bucket operations.
3. **Stateless Design:** The pipeline architecture supports deployment to any cluster since the dataset regenerates on its own cadence and nothing is stateful.
4. **GitOps Consistency:** Using iad-ci maintains consistency with existing deployment patterns for similar workloads.

## Deployment Status

### ✅ Completed Components
- [x] **Container Image:** `registry.ardenone.com/hetzner-auction-pipeline:0.1.0` built and pushed
- [x] **Kubernetes Manifest:** `k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml` ready
- [x] **Pipeline Logic:** 10-minute refresh loop with temp-key-then-swap lifecycle implemented
- [x] **R2 Provisioning:** Bucket and ExternalSecret configuration (had-1m8)

### 🔄 In Progress
- [ ] **GitOps Sync:** Copy manifest to `jedarden/declarative-config` repository
- [ ] **ArgoCD Deployment:** Sync to iad-ci cluster
- [ ] **Run Verification:** Monitor 3 consecutive scheduled runs

## Deployment Architecture

### Cluster: iad-ci
- **Location:** Washington DC region
- **Purpose:** CI/CD and pipeline workloads
- **Access:** GitOps via ArgoCD (no direct kubectl access)

### Namespace: hetzner-auction-dashboard
- **Isolation:** Dedicated namespace for pipeline resources
- **Components:**
  - Deployment: `hetzner-auction-pipeline` (replicas: 1)
  - ConfigMap: `pipeline-config`
  - ExternalSecret: `r2-credentials-external`
  - PodDisruptionBudget: `hetzner-auction-pipeline-pdb`

### Pipeline Configuration
```yaml
Image: registry.ardenone.com/hetzner-auction-pipeline:0.1.0
Refresh Loop: 10 minutes
Replicas: 1 (house rule: no Job/CronJob)
Safety: temp-key-then-swap for R2 atomic updates
```

## GitOps Deployment Process

### Step 1: Copy to declarative-config
```bash
# Target repository: jedarden/declarative-config
# Target path: k8s/pipeline/

cp k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml \
   ~/declarative-config/k8s/pipeline/
```

### Step 2: Commit and Push
```bash
cd ~/declarative-config
git add k8s/pipeline/hetzner-auction-dashboard-pipeline.yaml
git commit -m "Add hetzner-auction-dashboard pipeline deployment"
git push
```

### Step 3: ArgoCD Sync
- ArgoCD detects changes in declarative-config
- Auto-syncs to iad-ci cluster
- Pipeline Deployment created/updated
- ExternalSecret syncs R2 credentials from OpenBao

### Step 4: Verify Deployment
```bash
# Check pod status
kubectl get pods -n hetzner-auction-dashboard

# Check logs for 10-minute cycles
kubectl logs -n hetzner-auction-dashboard deployment/hetzner-auction-pipeline

# Verify R2 artifacts
# (Check for current_snapshot.parquet and unmatched-cpus.json)
```

## Verification Requirements

### 3 Consecutive Scheduled Runs
After deployment, the pipeline must complete 3 full 10-minute cycles successfully:

**Run 1 (0-10 minutes):**
- Fetch Hetzner auction data
- Match CPUs to benchmark scores
- Compute derived metrics
- Write temp Parquet file
- Verify and promote to live key
- Generate unmatched-cpus.json report

**Run 2 (10-20 minutes):**
- Repeat cycle
- Confirm temp-key-then-swap works correctly
- Verify no concurrent writer issues

**Run 3 (20-30 minutes):**
- Repeat cycle
- Confirm stable operation
- Verify no manual intervention required

### Success Criteria
- [ ] No errors in pipeline logs for 30 minutes
- [ ] R2 artifacts updated every 10 minutes
- [ ] Dashboard loads and queries Parquet file
- [ ] No manual intervention required
- [ ] Pipeline continues cycling normally

## Monitoring and Logs

### Key Log Indicators
```bash
# Successful cycle completion
grep "Cycle complete" logs

# R2 publish confirmation
grep "Published to R2" logs

# Error detection
grep -i "error\|fail\|abort" logs
```

### Health Checks
- **Liveness:** Process check (exec probe)
- **Readiness:** Process check (exec probe)
- **Manual:** Check logs for cycle timestamps

## Rollback Plan

If deployment fails:
1. **ArgoCD Rollback:** Revert to previous GitOps state
2. **Keep Last Snapshot:** Failed run aborts without touching live R2 key
3. **Logs Preservation:** Check pod logs for failure diagnosis
4. **Retry Next Cycle:** Pipeline retries automatically in 10 minutes

## Dependencies and Prerequisites

### Required Infrastructure
- [x] Cloudflare R2 bucket provisioned
- [x] OpenBao secret path created
- [x] ExternalSecret manifest ready
- [x] Container image pushed to registry

### Cluster Resources
- Memory: 512Mi limit per pod
- CPU: 500m limit per pod
- Storage: 100Mi emptyDir for temp files
- Egress: Cloudflare R2 API access

### Security
- Non-root user (UID 1000)
- Read-only root filesystem (where possible)
- OpenBao-backed secrets (no hardcoded credentials)
- Minimal container image (python:3.11-slim)

## Next Steps

1. **Access declarative-config:** Obtain access to `jedarden/declarative-config` repository
2. **GitOps Sync:** Copy manifest and commit for ArgoCD deployment
3. **Monitor Deployment:** Watch ArgoCD sync and pod startup
4. **Verify Runs:** Monitor 3 consecutive 10-minute cycles
5. **Close Task:** Mark had-307 as complete upon successful verification

## References

- **Plan:** `docs/plan/plan.md` - Architecture, Pipeline Run Lifecycle
- **Manifest:** `k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml`
- **R2 Provisioning:** `docs/notes/had-1m8-r2-provisioning.md`
- **Cluster:** iad-ci (resolved Open Question)

---

**Status:** Cluster choice resolved (iad-ci), awaiting GitOps sync access
**Created:** 2026-08-03
**Task:** had-307