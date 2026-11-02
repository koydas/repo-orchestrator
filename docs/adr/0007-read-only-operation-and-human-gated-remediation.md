# ADR-0007: Read-only operation first; remediation is planned by the system and approved by a human

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

The long-term vision includes coordinated issues and pull requests across repositories. Writes across repositories carry two risks the ecosystem already documents:

- **Re-entry.** An object created by a tool is picked up by the pipeline it observes. `ai-dev-tools` ADR-017 §5 rejects creating issues for exactly this reason: `autonomous-dev-loop` reacts to labels and issues.
- **Blast radius.** A wrong finding turned into PRs in five repositories costs five reviews and five reverts.

The ecosystem's automation keeps a human at the irreversible step: "human as gate" in `ai-dev-tools` (ADR-001), human merge in `autonomous-dev-loop` (no merge call exists, `docs/landscape.md`).

## Decision

1. **The MVP is read-only.** It needs only read access (contents and pull requests, read). It holds no token with write scope. Its only output is a local report (Markdown and JSON) and its run store.
2. **Autonomy levels** for this system, each requiring its own ADR to enable:
   | Level | Output | Gate |
   |---|---|---|
   | 0 | Local report | Invocation by a human |
   | 1 | Remediation plan (ordered, per repository, with ADR compatibility) in the report | Human reads it |
   | 2 | Draft issue or PR comment, posted only after explicit per-item approval | Approval per item |
   | 3 | Coordinated branches or PRs, opened as drafts, never merged | Approval per plan; merge by humans in each repository |
   The MVP covers levels 0 and 1.
3. **No merge, ever.** Merging stays with each repository's own process and owners.
4. A future write path must: run under a distinct identity (not the user's account), mark every object it creates as machine-originated, and be excluded from the triggers of pipelines it may touch (for example `autonomous-dev-loop`'s labels). This is a precondition for any level ≥ 2 ADR.

## Rationale

- Read-only lets the system be wrong cheaply while its precision is measured.
- Approvals per item preserve each repository's ownership of its own changes.
- Matches the ecosystem's established gates and avoids the re-entry problem by construction.

## Alternatives considered

- **Open issues automatically for high-confidence findings.** Rejected for the MVP: precision is unmeasured; re-entry into `autonomous-dev-loop` is a known hazard.
- **Post PR comments from the start (as a check).** Deferred, not rejected: it is the most useful level 2 output, once false-positive rates are known.
- **Auto-merge low-risk documentation fixes (counts).** Rejected: contradicts the ecosystem's merge gate, and D2 shows that even a "trivial" count fix can target the wrong repository.

## Consequences

- ✅ The MVP can be pointed at any public repository without risk to it.
- ✅ No token with write scope exists to leak.
- ⚠️ The value of a finding depends on a human reading a local report; adoption friction is real until level 2.
- ⚠️ Remediation plans are not validated by execution (no branch is built), only by policy and ADR compatibility.

## Open questions

- Which identity would carry level 2 writes (a GitHub App installed per repository is the likely candidate)?
