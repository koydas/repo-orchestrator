# Vision

> **Status: design phase.** This page states intent, not capability.

## Problem

Independent repositories make claims about each other: a consumer pins a producer's schema version, a deployment repository serves the model tags an application hard-codes, a profile describes how many ADRs another repository holds, a toolbox decides how every consumer must declare it. Each repository's CI and reviewers check the repository they run in. Nothing checks the claims **between** them.

The result is drift that no single-repository tool can see. In the [ecosystem baseline](research/ecosystem-baseline.md), the profile README describes `agent-patterns` as having 17 patterns when it has 20 (D1), and states 59 ADRs for `autonomous-dev-loop`, matching that repository's own README, while its ADR directory holds 65 (D2): the second error is invisible to a reviewer of either file alone, and the fix belongs upstream.

## Goals

1. **Model** how repositories relate, with typed relationships and explicit contracts.
2. **Detect** meaningful cross-repository inconsistencies, defined by policies with named authorities.
3. **Explain** each finding with evidence pinned to commits, an explicit verification status and intent.
4. **Assess** the cross-repository impact of a pull request before it merges.
5. **Plan** remediation across repositories, ordered and checked against existing ADRs, for a human to approve.
6. **Respect intentional divergence**: coherence is not uniformity.

## Non-goals

- Single-repository code review, linting or testing.
- Enforcing identical conventions without a relationship that requires them.
- Executing builds, deployments or migrations across repositories.
- Autonomous merges, or any write to GitHub without per-item human approval.
- Ranking repositories or engineers.
- Replacing `autonomous-dev-loop` or `ai-dev-tools`; this system can feed them evidence later.

## Intended users

| User | Need |
|---|---|
| Owner of a multi-repository ecosystem (initially the `koydas` repositories) | Know which claims between repositories are false, and which change will make one false |
| Reviewer of a PR in one repository | See which other repositories the PR affects, and how |
| Maintainer of a shared contract or toolbox | Know every consumer and whether it still conforms |

## Success criteria

The MVP's measurable criteria are in [mvp.md](mvp.md#validation-criteria-for-the-mvp-as-a-whole). In product terms:

- Every finding can be checked by a human from its evidence in under a minute.
- Users stop seeing noise: dismissed-as-noise findings trend to zero, because divergence without a policy is never a finding.
- At least one class of drift is caught before merge that was previously caught after (or never).

## Differentiation

| Approach | What it does | What it misses |
|---|---|---|
| Single-repository AI reviewers (including `autonomous-dev-loop`'s, with in-repository impact analysis and document evidence) | Review one diff against one repository | Any claim whose other side lives in another repository |
| Dependency scanners, SBOM tools | Package-manifest dependencies | Contracts through published files, URLs, manifests and documents, which are all of the baseline's verified relationships |
| Monorepo tooling, code-ownership graphs | Relationships inside one repository | Independent repositories with independent owners |
| Org-wide linters and "golden path" templates | Enforce uniformity | Intentional divergence; produce noise where no relationship exists |
| Multi-repository batch-change tools | Apply the same change across repositories | Whether the change is needed, and in what order |

What is specific here: typed relationships with impact semantics, policies with authorities and expiring exceptions, deterministic checks before LLM reasoning, quote-verified evidence pinned to commits, and a human at every write.

## Naming

The repository and project are named `repo-orchestrator`. The product is **cross-repository coherence**; it does not orchestrate builds or tasks across repositories ([ADR-0001](adr/0001-cross-repository-coherence-as-product-boundary.md)). Whether to rename is an open question in that ADR.
