# Operator Runbook

## Common Tasks

**Bump or add an Ollama model:**
```bash
# edit apps/ollama/application.yaml -> spec.source.helm.valuesObject.ollama.models.pull
git add apps/ollama/application.yaml
git commit -m "Add/bump model: <tag>"
git push
# optional: force an immediate sync instead of waiting ~3 min
sudo microk8s kubectl -n argocd annotate application ollama argocd.argoproj.io/refresh=hard --overwrite
```
Before committing a new tag, verify it actually exists — a typo produces a silent-until-sync failure, not a CI failure (see [ADR-0009](./adr/0009-static-ci-validation.md)):
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://registry.ollama.ai/v2/library/<model>/manifests/<tag>
# 200 = exists, 404 = check spelling
```

**Force an ArgoCD sync for any app:**
```bash
sudo microk8s kubectl -n argocd annotate application <name> argocd.argoproj.io/refresh=hard --overwrite
```

**Check ArgoCD / Ollama status:**
```bash
sudo microk8s kubectl -n argocd get application -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
sudo microk8s kubectl -n ollama get pods,pvc,svc
```

**Confirm inference is actually running on the GPU (not falling back to CPU):**
```bash
curl -s http://192.168.1.241:11434/api/generate -d '{"model":"llama3.1:8b-instruct-q4_0","prompt":"hi","stream":false}' &
watch -n1 nvidia-smi   # VRAM usage and GPU-Util%% should spike during the request
```
(Previously used `qwen2.5:0.5b` for a faster/lighter check — removed 2026-07-30 as an unused
model; any installed tag works here, this one's just the one always present.)

**A request feels stuck/slow:** expected if other requests are queued ahead of it — see [ADR-0013](./adr/0013-ollama-sequential-requests.md). Ollama serves one generation at a time on this GPU by design (no `OLLAMA_NUM_PARALLEL`); N simultaneous requests finish roughly N × (single-request time) apart, not all at once. Check for queued requests:
```bash
sudo microk8s kubectl -n ollama logs deploy/ollama --since=5m | grep -E "\[GIN\].*POST|slot launch_slot_"
```

**Rotate the ArgoCD admin password:** log into the UI (`https://192.168.1.240`) → user icon (top right) → *Update Password*. The initial auto-generated password is only ever shown once, via:
```bash
sudo microk8s kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

**Access Grafana / retrieve its admin password:** Grafana is exposed at `http://192.168.1.242` (MetalLB, `monitoring` Application — see [ADR-0012](./adr/0012-monitoring-stack.md)). Username is `admin`; the password lives in a Secret created once out-of-band (**not** the chart's own auto-generated one — that one drifts under ArgoCD, see below):
```bash
sudo microk8s kubectl -n monitoring get secret grafana-admin-credentials \
  -o jsonpath="{.data.admin-password}" | base64 -d
```
If that secret doesn't exist yet (fresh install), create it before/alongside the first sync — the Application's `grafana.admin.existingSecret` value expects it to already be there:
```bash
sudo microk8s kubectl -n monitoring create secret generic grafana-admin-credentials \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')"
```

**Check that Prometheus is actually scraping the GPU exporter:**
```bash
sudo microk8s kubectl -n monitoring get servicemonitor nvidia-dcgm-exporter
# In the Prometheus UI (port-forward or via Grafana's Explore), Status > Targets should show
# nvidia-dcgm-exporter/0 as "UP":
sudo microk8s kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090
```

**Prune stale model tags** (the Helm chart's `models.clean` is left `false` — see [ADR-0004](./adr/0004-ollama-helm-deployment.md) — so removing a tag from `models.pull` does not delete it from the PVC):
```bash
sudo microk8s kubectl -n ollama exec deploy/ollama -- ollama rm <tag>
```

**Query homelab-gateway's per-call log** (see `homelab-gateway`'s [ADR-0001](https://github.com/koydas/homelab-gateway/blob/main/docs/adr/0001-mongodb-call-log.md)):
```bash
sudo microk8s kubectl exec -n homelab-gateway deploy/homelab-gateway-mongo -- mongosh --quiet --eval '
db = db.getSiblingDB("homelab_gateway");
db.call_log.find().sort({timestamp:-1}).limit(20)
'
```
Only the last 30 days are kept (TTL index); filter with e.g. `{backend:"ollama", statusCode:{$gte:400}}` to find errors.

**Set up or rotate the Claude Code runner's token** (see [ADR-0022](./adr/0022-onboard-claude-code-runner.md)). Run both steps yourself, on your own machine, in your own terminal — not pasted through an AI chat session, since the result is a credential valid for a full year:
```bash
# 1. Generate the token. Opens a browser OAuth flow; needs a Claude Pro/Max/Team/
#    Enterprise login. Prints the token to your terminal — it is not saved anywhere.
claude setup-token

# 2. Store it out-of-band (not in Git). `read -s` keeps it out of shell history and
#    off your screen as you paste it.
read -s -p "Paste the token: " CCTOKEN && echo
sudo microk8s kubectl create secret generic claude-code-oauth-token \
  -n claude-code-runner \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN="$CCTOKEN" \
  --dry-run=client -o yaml | sudo microk8s kubectl apply -f -
unset CCTOKEN
```
Verify it landed without printing the value:
```bash
sudo microk8s kubectl get secret claude-code-oauth-token -n claude-code-runner
```

**Trigger a one-off Claude Code run:** the `claude-code-runner` `CronJob` is a suspended template, not a live schedule. `create job --from=cronjob/...` alone races the Job controller (it creates the pod almost immediately), so bake the real prompt/tools into the manifest *before* applying it rather than editing the Job after:
```bash
sudo microk8s kubectl create job -n claude-code-runner "claude-run-$(date +%s)" \
  --from=cronjob/claude-code-runner --dry-run=client -o yaml > /tmp/claude-run-job.yaml

python3 - <<'EOF'
import yaml
path = "/tmp/claude-run-job.yaml"
job = yaml.safe_load(open(path))
env = job["spec"]["template"]["spec"]["containers"][0]["env"]
overrides = {"CLAUDE_PROMPT": "the real prompt here", "CLAUDE_ALLOWED_TOOLS": "Read,Bash"}
for e in env:
    if e["name"] in overrides:
        e["value"] = overrides[e["name"]]
yaml.dump(job, open(path, "w"))
EOF

sudo microk8s kubectl apply -f /tmp/claude-run-job.yaml
sudo microk8s kubectl -n claude-code-runner logs -f job/<job-name-from-above>
```
Finished Jobs self-delete after an hour (`ttlSecondsAfterFinished: 3600`) — no manual cleanup needed for routine runs.

**Rebuild this server from scratch:** see [README.md § Recreate from scratch](../README.md#recreate-from-scratch).

---

## Incidents Hit While Building This

### `kubectl apply` fails with `metadata.annotations: Too long` on the ArgoCD install
**Cause:** the `applicationsets.argoproj.io` CRD's schema is large enough that the `kubectl.kubernetes.io/last-applied-configuration` annotation client-side `apply` writes exceeds Kubernetes' 262144-byte annotation limit.
**Fix:** use `--server-side --force-conflicts` instead of plain `apply` (already baked into `bootstrap/install-host.sh`).

### GPU addon pods stuck in `Init` forever
**Cause, per the upstream issue this project's driver/CUDA combo matches (canonical/microk8s#5229):** a separately apt-installed `containerd` package conflicting with microk8s's bundled one, not an inherent version bug. See [ADR-0005](./adr/0005-microk8s-version-pin.md) for the full root-cause writeup.
**Fix:** `dpkg -l | grep containerd` should be empty before enabling the `gpu`/`nvidia` addon; `bootstrap/install-host.sh` checks this automatically and refuses to proceed if it finds a conflict.

### A newly-rolled-out Ollama pod sits in `ContainerCreating` for 10+ minutes with no events in the namespace
**Observed:** during the `qwen2.5-coder` model rollout, the new pod showed `0/1 ContainerCreating` with `kubectl describe pod` reporting `Events: <none>`, while `containerd`'s own journal logs showed the container had actually started successfully (`StartContainer ... returns successfully`) within seconds.
**Likely cause:** a kubelet↔API-server event/status sync lag rather than a real failure — the kubelet journal around the same time showed `EndpointSlice informer cache is out of date` and `container event discarded` messages.
**Resolution:** self-resolved without intervention after several minutes; the pod's actual `status.phase` became `Running` once the sync caught up. No fix was applied.
**If it recurs:** check `sudo journalctl -u snap.microk8s.daemon-containerd -n 50` for `StartContainer ... returns successfully` before assuming the pod is genuinely stuck — the container may already be up despite what `kubectl get pods` reports.

### `curl: (7) Failed to connect ... No route to host` against the Ollama Service IP
**Cause:** the old pod had just been terminated (ArgoCD sync rolling out a new one) and the new pod hadn't yet become `Ready`, so the Service had zero healthy endpoints for a brief window.
**Fix:** none needed — wait for the new pod to reach `Running`/`Ready` (`kubectl -n ollama get pods -w`).

### `monitoring` Application stuck `SyncFailed` on several CRDs: `metadata.annotations: Too long`
**Cause:** same class of issue as the ArgoCD-install-itself incident above — `kube-prometheus-stack`'s largest CRDs (`prometheuses`, `alertmanagers`, `alertmanagerconfigs`, `prometheusagents`, `thanosrulers`, `scrapeconfigs`) have OpenAPI schemas large enough that a plain `kubectl apply`'s `last-applied-configuration` annotation exceeds the 262144-byte Kubernetes limit. Smaller CRDs (`servicemonitors`, `podmonitors`, `probes`, `prometheusrules`) synced fine; only the large ones failed, which made the symptom look partial/confusing at first.
**First attempt that didn't fully work:** adding `ServerSideApply=true` to the Application's `syncPolicy.syncOptions`. ArgoCD does not honor this Application-level setting for CRDs that come from a Helm chart's own `crds/` directory — every periodic auto-sync kept re-attempting a plain patch on those 6 CRDs and failing (retried 5 times, then gave up until the next poll), even though the CRDs already existed and everything using them worked fine.
**Actual fix:** install the CRDs once out-of-band with real server-side apply (`kubectl apply --server-side --force-conflicts -f <chart>/charts/crds/crds/`), then set `crds.enabled: false` in the Application's Helm values so the chart stops trying to manage them at all going forward (see `apps/monitoring/application.yaml`). A chart version bump that changes CRD schemas will need the same manual `--server-side` apply repeated by hand.

### `monitoring` Application's root-driven bootstrap: `AppProject`/`Application` changes never applied, sync stuck retrying forever
**Cause:** the root Application's directory-recurse sync validates every manifest under `apps/` as a single all-or-nothing batch before applying any of it. The `ServiceMonitor` manifest (`apps/monitoring/dcgm-servicemonitor.yaml`) references a CRD that only the `monitoring` Application itself installs — but that Application's own creation was *also* stuck in the same failing root batch, so nothing could ever converge on its own (a genuine bootstrap cycle, not a transient race).
**Fix:** manually `kubectl apply -f apps/appproject.yaml` and `apps/monitoring/application.yaml` once, out-of-band, the same way `bootstrap/root-app.yaml` itself is documented as "applied manually, once" (see [architecture.md](./architecture.md)). Once the `monitoring` Application exists and its CRDs are installed, the root Application's own sync (retried automatically via `selfHeal`) succeeds on its own from then on.

### Grafana gets OOMKilled during normal use ("out of memory" errors in the UI)
**Cause:** confirmed via `dmesg -T | grep -i oom` — the container's cgroup memory limit (originally 256Mi) was hit. Grafana 13.1.1 (bundled by this chart) auto-preinstalls 3 "app" plugins at every startup (`grafana-lokiexplore-app`, `grafana-pyroscope-app`, `grafana-exploretraces-app`, visible as `gpx_*` processes in `dmesg`) that this repo has no matching datasource for, plus a new "unified storage" search index — steady-state RSS was already ~250Mi (`anon-rss` in the `dmesg` OOM line) *before* any interactive dashboard use, so real usage reliably pushed it over.
**Fix:** `grafana.resources` raised to 256Mi request / 512Mi limit, and `GF_PLUGIN_PREINSTALL_DISABLED: "true"` added to stop downloading/loading the three unused app plugins in the first place (`apps/monitoring/application.yaml`). Check `sudo microk8s kubectl -n monitoring get pod monitoring-grafana-0 -o jsonpath='{.status.containerStatuses[0].restartCount}'` after a while to confirm it's stopped climbing.

### Grafana admin login stops working with a previously-valid password
**Cause:** the chart's auto-generated admin-password `Secret` is not stable under ArgoCD — its `lookup()`-based "reuse the existing password" logic doesn't reliably hold (confirmed in practice: the templated password value changed across syncs with no config change on our end), matching the cosmetic `OutOfSync` status also seen on this Application. Enabling `grafana.persistence` alone wasn't enough: it stopped the SQLite user DB from being *wiped* on restart, but the DB still only picks up a *new* password value on whichever boot happens to land after the Secret drifted, so `kubectl get secret ... admin-password` could still report a value that didn't match what the running pod actually accepted.
**Fix:** stopped letting the chart template a password at all. Created a Secret once out-of-band (`grafana-admin-credentials`, see Common Tasks above) and pointed `grafana.admin.existingSecret` at it (`apps/monitoring/application.yaml`). With `existingSecret` set, the chart never computes/templates a password value — it just mounts env vars from that Secret — so there's nothing left for ArgoCD to drift.

### Grafana shows "failed to load dashboard" for the GPU dashboard
**Cause:** the embedded dashboard (Grafana.com ID 12239) is from 2020 (`schemaVersion: 22`) and uses the legacy Angular-based `graph` panel type. Grafana 13.1.1 (bundled by this chart) has dropped Angular support entirely, so those panels can't render — the API still serves the dashboard JSON fine (`GET /api/dashboards/uid/<uid>` returns 200), the failure is purely in the frontend trying to render an unsupported panel type.
**Fix:** already applied — `apps/monitoring/dcgm-dashboard-configmap.yaml`'s embedded JSON was migrated to `timeseries`/`fieldConfig` panels (`schemaVersion: 39`) before being committed. If a *different* legacy Grafana.com dashboard is ever added the same way, check its `panels[].type` for `graph` first and migrate it the same way rather than embedding it as-is.

### `/api/embed` returns `"This server does not support embeddings. Start it with --embeddings"`
**Cause:** the `otwld/ollama-helm` chart does not pass the `--embeddings` flag by default; not something CI or `kubeconform` can catch since the manifest is schema-valid.
**Fix:** not yet applied. To enable, add `--embeddings` to the container args via the Helm values (e.g. an `extraArgs` field, chart-version-dependent) and let ArgoCD redeploy.

### A git-source app's pod runs the wrong image right after a code push (`homelab-gateway`, 2026-07-30)
**Cause:** a code push to a git-source app's repo (e.g. `homelab-gateway`) triggers two commits in sequence: the push itself, then a follow-up `docker-publish.yml` commit that rewrites `k8s/deployment.yaml`'s image tag to the freshly built SHA (same pattern as `ollama-chat`'s ADR-0006). ArgoCD's automated poll-sync can land on the *first* commit before CI finishes and pushes the second — briefly deploying new manifests (e.g. a new env var) against the *old* image, which doesn't contain the corresponding code yet.
**Symptom:** `kubectl get application <name> -o jsonpath='{.status.sync.revision}'` matches your code-push commit SHA, not the later `chore: deploy <sha>` one; the running pod's image tag (`kubectl get pod ... -o jsonpath='{.spec.containers[0].image}'`) still shows the previous build.
**Fix:** wait for `gh run list --limit 1` to show the `docker-publish` workflow `completed`/`success`, then force a hard refresh so ArgoCD picks up the tag-rewrite commit instead of waiting for the next ~3min poll:
```bash
sudo microk8s kubectl -n argocd annotate application <name> argocd.argoproj.io/refresh=hard --overwrite
```
Don't trust the first post-push "Synced" status alone — cross-check the pod's actual image tag against the latest commit on `origin/main` before declaring a deploy done, same discipline as `ollama-chat`'s own `homelab-deploy` skill already applies.

### An image request returns `500` after ~11s: `cudaMalloc failed: out of memory`, then `llama-server terminated` (2026-07-30)
**Cause:** in `ollama-chat`, a text message loaded `llama3.1:8b-instruct-q4_0`, then a photo routed to `qwen2.5vl:3b`. With no `OLLAMA_MAX_LOADED_MODELS` set, Ollama tried to keep both models resident on the 6 GB card, on top of `whisper`'s idle ~1.3–1.4 GB. The second load hit `cudaMalloc failed: out of memory` and the runner aborted (`signal: aborted (core dumped)`) instead of rejecting the request cleanly. GPU time-slicing (ADR-0017) shares scheduling, not VRAM.
**Symptom:** `sudo microk8s kubectl logs -n ollama deploy/ollama` shows `ggml_backend_cuda_buffer_type_alloc_buffer: ... cudaMalloc failed: out of memory`, `llama-server terminated`, then `[GIN] 500 | ~10s | POST "/api/chat"`.
**Fix:** `OLLAMA_MAX_LOADED_MODELS=1` in the chart-root `extraEnv` of `apps/ollama/application.yaml`, so Ollama unloads the previous model before loading another ([ADR-0019](./adr/0019-ollama-max-loaded-models-one.md)). Check with `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` that only one Ollama runner is resident.

### A Helm value in an Application never reaches the pod, and nothing reports an error (`OLLAMA_KEEP_ALIVE`, found 2026-07-30)
**Cause:** `OLLAMA_KEEP_ALIVE=30s` had been added the day before under `ollama.extraEnv`, but in the `otwld/ollama` chart `extraEnv` is a **chart-root** key (`helm show values otwld/ollama --version 1.68.0`). Helm ignores unknown values keys without error, and the manifest is schema-valid, so neither CI (`kubeconform`) nor ArgoCD flagged it. Ollama kept running on the chart's 5-minute default keep-alive.
**Symptom:** the Application is `Synced`, but `sudo microk8s kubectl get deploy -n ollama ollama -o jsonpath='{.metadata.generation}'` doesn't change after the commit (it was still `4`), and the container env (`kubectl get deploy -n ollama ollama -o jsonpath='{.spec.template.spec.containers[0].env}'`) lacks the variable.
**Fix:** moved `extraEnv` to the values root ([ADR-0019](./adr/0019-ollama-max-loaded-models-one.md)). After any Helm-values change, confirm `metadata.generation` moved and the live env contains the new value before calling it deployed.

---

## Symptom → Probable Cause

| Symptom | Probable Cause |
|---|---|
| ArgoCD `Application` stuck `OutOfSync` after referencing a new `AppProject` | Sync-ordering race — the `AppProject` manifest hasn't landed yet. Should self-heal within one reconcile loop; add/check `argocd.argoproj.io/sync-wave: "-1"` on the project if it doesn't (see [ADR-0007](./adr/0007-dedicated-appproject.md)) |
| MetalLB stops handing out the expected IP after re-running `microk8s enable metallb:<range>` on the host | The address pool is Git-managed ([ADR-0008](./adr/0008-metallb-config-in-git.md)) — ArgoCD's `selfHeal: true` reverts host-level imperative changes back to what's in `apps/metallb-config/`. Edit the Git manifest instead. |
| GitHub push doesn't seem to deploy for several minutes | Expected — no webhook is possible on a LAN-only cluster ([ADR-0002](./adr/0002-lan-only-exposure.md)); ArgoCD polls every ~3 min. Force a refresh (see Common Tasks) for immediate feedback. |
| CI (`validate.yml`) is green but the app fails to sync/run anyway | Expected class of gap — CI only validates schema/syntax, not runtime semantics like Helm value correctness or registry tag existence ([ADR-0009](./adr/0009-static-ci-validation.md)) |
| 40Gi PVC filling up | Stale model tags not auto-pruned (`models.clean: false`) — see Common Tasks above to remove them manually |
| `monitoring` Application shows `Healthy` but `OutOfSync` even right after a clean sync | Cosmetic, unrelated to the password issue below: the Grafana subchart's admin-password `Secret` re-renders slightly differently on each `helm template` diff pass. Doesn't affect the running pod once `grafana.persistence` is enabled (see below). |
| A `monitoring` namespace pod is `Pending` or gets `OOMKilled`, especially while a larger Ollama model is loaded | Node only has 15Gi RAM total, shared between Ollama and the monitoring stack's fixed resource requests/limits ([ADR-0012](./adr/0012-monitoring-stack.md)) — check `free -h` on the host and `sudo microk8s kubectl top pods -A` before assuming a config bug |
| A git-source app's pod is running right after a push but the new code doesn't seem to be there | ArgoCD synced the code-push commit before CI's follow-up image-tag-rewrite commit landed — see Incidents above. Check `gh run list` and the pod's actual image tag, then force a hard refresh |
| `ollama-chat` returns `500` on an image right after a text message (or vice versa) | Two Ollama models resident at once on the 6 GB card → CUDA OOM and runner abort. Check `OLLAMA_MAX_LOADED_MODELS=1` is in the live env ([ADR-0019](./adr/0019-ollama-max-loaded-models-one.md)) — see Incidents above |
| A Helm value change is `Synced` but has no effect | The key is nested at the wrong level and Helm ignored it silently. Check that the Deployment's `metadata.generation` moved and the live env contains the value — see Incidents above |
