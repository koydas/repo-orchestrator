# ADR-0001: Cross-repository coherence is the product boundary

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

The `koydas` ecosystem already has strong single-repository tooling: `autonomous-dev-loop` reviews PRs with tool evidence, impact analysis inside the repository (its ADR-0058) and documents that mention the change (its ADR-0062); `ai-dev-tools` provides the interactive review and fix commands. None of them reads a second repository. The defects they cannot see are the ones in the [ecosystem baseline](../research/ecosystem-baseline.md): a profile count made stale by a change in another repository (D1, D2), a schema version shared by a producer and a consumer in different repositories (R2), a model tag hard-coded in one repository and served by another (R7), a shared IP pool (R8).

The repository is named `repo-orchestrator`. The name invites scope creep toward a multi-repository task runner (fan out builds, sync files, open PRs everywhere). That product exists in several forms and is not what the baseline shows a need for.

## Decision

1. The product is **cross-repository engineering coherence**: discover and model how independent repositories relate, detect where one repository's claims or contracts about another are false or at risk, explain the impact with evidence, and (later) plan remediation for a human to approve.
2. **In scope:** relationships, contracts, shared resources, documentary claims and conventions that span at least two repositories.
3. **Out of scope:**
   - Single-repository code review, lint, test execution. Those belong to the repository's own CI and to `autonomous-dev-loop` / `ai-dev-tools`.
   - Executing builds, deployments or migrations across repositories.
   - Enforcing identical conventions where no relationship requires them ([ADR-0003](0003-policy-driven-coherence-with-scoped-exceptions.md)).
4. A finding is only in scope if its evidence cites **at least two repositories**, or one repository plus a declared relationship to another. A defect fully contained in one repository is out of scope even when this system notices it; it may be listed as "out of scope, observed" with no lifecycle.

## Rationale

- The baseline's verified inconsistencies are all cross-repository; the single-repository ones (for example a stale branch name in one `CLAUDE.md`) are already within reach of existing reviewers.
- A narrow boundary keeps the policy set small and the evaluation measurable.
- Single-repository review is commoditized (see `autonomous-dev-loop docs/landscape.md`); cross-repository coherence is not covered by any tool in the ecosystem.

## Alternatives considered

- **A multi-repository orchestrator (task runner, file sync, mass PRs).** Rejected: it optimizes uniformity, which the baseline shows is often wrong (N1, N3), and it requires write access from day one.
- **Extend `autonomous-dev-loop`'s reviewer to read other repositories.** Rejected as the primary vehicle: the reviewer runs per PR inside one repository's Actions with a token budget sized for one diff, and its pipeline must stay free of external I/O it does not control. A later integration (the reviewer consuming this system's report as evidence) remains possible.
- **A generic "engineering intelligence" platform (metrics, ownership, DORA).** Rejected: no stated need, large surface.

## Consequences

- ✅ A crisp test for every proposed feature: does it need two repositories to be meaningful?
- ✅ No overlap with existing reviewers; the systems can compose.
- ⚠️ The repository name does not describe the product. The README states the boundary in its first paragraph.
- ⚠️ Some valuable single-repository observations (D2's root cause lies inside `autonomous-dev-loop`) are only reported through their cross-repository effect.

## Open questions

- Should the project be renamed to match the boundary, or is the name kept for continuity?
