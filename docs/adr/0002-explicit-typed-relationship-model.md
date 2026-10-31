# ADR-0002: Relationships are explicit, typed and declared before they are inferred

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

The baseline found eleven relationships (R1–R11) of very different kinds: a runtime data contract (R2), a deployment edge (R6), a shared resource (R8), a profile describing other repositories (R1), a skill modeled after another (R10). Treating them all as "dependencies" would produce wrong conclusions: a pattern catalog citing a repository does not break when that repository changes; a consumer pinned to `schema_version = 1` does.

Relationships can be discovered by text search (a repository name in a README), but the baseline shows how weak that signal is: `ollama-chat` mentions `gitops-homelab` in a dozen places, most of them historical context inside ADRs.

## Decision

1. A **Relationship** is a typed, directed edge between two repositories, optionally anchored to artifacts on each side. Types (defined in the [domain model](../domain-model.md#relationship-types)): `runtime_contract`, `build_dependency`, `deployment`, `shared_resource`, `tooling_adoption`, `documentary`, `conceptual`.
2. Every relationship has an **origin**:
   - `declared`: written by a human in this repository's versioned relationship declarations;
   - `observed`: produced by a deterministic extractor from source evidence (for example a URL in a config file that resolves to another repository);
   - `inferred`: proposed by an LLM or a heuristic.
3. **Only `declared` relationships, and `observed` relationships confirmed by a human, drive policy evaluation and impact analysis.** `inferred` relationships are output as proposals in the report and have no effect until declared.
4. Each relationship type carries a default **impact semantics**: whether a change on the source side can break the target (`runtime_contract`, `deployment`, `shared_resource`, `build_dependency`), can make it false (`documentary`), or only make it outdated (`conceptual`). Impact analysis uses this, not the edge's existence alone.
5. Contracts are first-class: a `runtime_contract` relationship names its **Contract** (the producer artifact, the consumer artifact, and the property that must agree, for example `schema_version`).

## Rationale

- Typed edges are what make "is this a real inconsistency" answerable: a count mismatch on a `documentary` edge and a version mismatch on a `runtime_contract` edge have different priorities and different fix sites.
- Declaration-first keeps the MVP small and the false-positive rate low; the six repositories of the MVP have few enough relationships to write by hand.
- Separating origin from type lets automatic discovery be added later without changing what drives findings.

## Alternatives considered

- **Infer all relationships from text and imports.** Rejected as the source of truth: high recall, low precision, and no way to tell a deployment edge from a historical mention. Kept as a proposal generator (future).
- **Use package manifests only (`package.json`, `pyproject.toml`).** Rejected: none of the verified relationships is a package dependency. The ecosystem's coupling is through published files, URLs, manifests and documents.
- **An untyped graph with free-text labels.** Rejected: labels cannot drive impact semantics or policy scope.
- **A graph database.** Rejected: tens of nodes and edges; an in-memory graph built per run is enough ([ADR-0008](0008-git-declarations-and-local-sqlite-state.md)).

## Consequences

- ✅ Impact analysis can rank affected repositories by what can actually break.
- ✅ The relationship declarations are reviewable in pull requests like code.
- ⚠️ Manual declaration does not scale to hundreds of repositories; discovery is required before that, and is deferred.
- ⚠️ An undeclared relationship produces no finding. The MVP's recall is bounded by the declarations; the evaluation must measure it ([mvp.md](../mvp.md#evaluation)).

## Open questions

- Should a relationship be declarable from the target repository (for example a `CLAUDE.md` statement such as the profile's counting rule, R1) and imported, rather than only declared here?
