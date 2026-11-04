# ADR-0008: MetalLB IP Pool Adopted Into Git Management

**Date:** 2026-07-23  
**Status:** Accepted

---

## Context

`microk8s enable metallb:<range>` both installs the MetalLB controller *and* creates a default `IPAddressPool`/`L2Advertisement` using the given range, entirely as host-level imperative state — invisible to Git, changeable only by re-running the addon command on the box. This was flagged as a gap once the base stack was working: the LAN IP range is exactly the kind of thing that should be changeable via commit like everything else in this repo, per [ADR-0001](./0001-kubernetes-gitops-over-docker.md)'s core requirement.

---

## Decision

Track `IPAddressPool` and `L2Advertisement` as plain manifests under `apps/metallb-config/`, using the **same names** (`default-addresspool`, `default-advertise-all-pools`) as the objects the addon originally created. Because the `root` Application applies everything under `apps/` recursively as raw resources (not only `Application`-kind manifests — see [architecture.md](../architecture.md)), ArgoCD's next sync updates these existing objects in place rather than creating conflicting duplicates.

Verified live: after this change was pushed and ArgoCD refreshed, `kubectl get ipaddresspool` showed the same range and the Ollama service kept the same assigned IP (`192.168.1.241`) — the adoption caused zero disruption.

---

## Considered Alternatives

### Leave MetalLB config as addon-only, imperative host state
This was the original state, consistent with how the addon is designed to be used. Rejected once "everything infra-relevant should be a Git commit" was applied consistently — the IP range is no different in kind from the Ollama model version, which was already Git-managed.

### Create new, differently-named `IPAddressPool`/`L2Advertisement` objects
Would have required first deleting or shrinking the addon-created pool to avoid MetalLB's overlapping-range validation webhook rejecting the new one. Adopting the existing names in place avoided any deletion step and kept the currently-assigned Service IPs stable.

---

## Consequences

**Good:**
- Changing the LAN range is now `apps/metallb-config/ipaddresspool.yaml` + commit + push, consistent with how every other infra change in this repo is made.
- No disruption when adopting the existing objects — same names meant an in-place update, not a recreate.

**Neutral:**
- The MetalLB *controller* (the addon itself) is still imperative host state (`bootstrap/install-host.sh`), only the address pool configuration is Git-managed. Installing the controller itself via Git would require a different mechanism (e.g. its own Helm-based ArgoCD Application) — not done here, judged not worth the added complexity for a single-controller-instance homelab.

**Negative:**
- If someone runs `microk8s enable metallb:<different-range>` directly on the host again, it would fight with ArgoCD's `selfHeal: true`, which would revert it back to what's in Git on the next sync. This is arguably correct GitOps behavior, but is a footgun for anyone who doesn't know the range is Git-managed and tries the old imperative method out of habit.
