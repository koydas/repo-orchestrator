# ADR-0003: Coherence is policy-driven, scoped, and admits recorded exceptions

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

"Coherence is not uniformity." The baseline makes this concrete:

- ADR file naming differs across repositories (N1) and nothing consumes it across repositories: not a defect.
- `new-adr` procedures are duplicated in five repositories (N3), and `ai-dev-tools` ADR-018 decides that skills belong to each repository: duplication is intentional.
- `autonomous-dev-loop`'s `settings.json` diverges from ADR-018's literal text (N2) for a documented reason: the policy text is ambiguous, the repository is not wrong.
- Repository-level instructions in the ecosystem consistently say that the narrowest rule wins ("Repo rules win over toolbox defaults").

A system that flags every divergence trains its users to ignore it.

## Decision

1. **No policy, no finding.** A divergence becomes a finding only when a **Coherence Policy** says it matters. There is no built-in "be consistent" rule.
2. A policy has a **scope**, from widest to narrowest: `global` → `relationship_type` (for example every `runtime_contract`) → `relationship` (one edge) → `repository`.
3. A policy references its **authority**: the document that justifies it (an ADR in some repository, a `CLAUDE.md` rule, or an ADR in this repository). A policy without an authority is rejected at load time.
4. **Intentional Exceptions** are records, not suppressions: scope (policy + repositories/artifacts), justification, authority link, owner, and a review date. An expired exception stops suppressing: the finding returns to `confirmed`, marked as an expired exception ([finding lifecycle](../domain-model.md#finding-lifecycle)).
5. **Precedence** (applied in order):
   1. A repository's own accepted ADR or instruction file is authoritative for that repository's internal conventions. A wider policy that contradicts it does not produce a finding against the repository; it produces a **policy conflict** for a human.
   2. A valid exception suppresses the matching finding (state `excepted`, still listed).
   3. Otherwise, the narrowest applicable policy wins over wider ones on the same subject.
   4. Ties between policies of the same scope are a configuration error, reported at load time.
6. **Opportunities** (consolidation, propagation of an improvement) are a separate output kind, never a finding. Each opportunity must state which accepted ADRs it is compatible with or contradicts (N3 contradicts `ai-dev-tools` ADR-018 and must say so). Their lifecycle and evaluation are defined in [ADR-0009](0009-optimization-opportunities-as-first-class-output.md).

## Rationale

- Policies with authorities make every finding traceable to a decision a human made.
- Exceptions with expiry make "intentional" an auditable claim instead of a silent mute.
- Precedence mirrors the ecosystem's existing rule (narrowest wins) and its respect for per-repository ADRs.

## Alternatives considered

- **Detect all divergences, let users filter.** Rejected: noise is the failure mode of this product class.
- **A global style guide enforced everywhere.** Rejected: contradicts the core principle and existing ADRs (ADR-018 §3).
- **Policies in a general-purpose policy language (Rego/OPA, CEL).** Rejected for the MVP: adds a language and runtime for seven policies whose checks are mostly extraction plus comparison. Policies are Python functions with declared metadata; revisit if non-developers must author policies.
- **Exceptions as inline comments in the target repositories.** Rejected: requires writing to those repositories and scatters the audit trail.

## Consequences

- ✅ Each finding answers "who decided this matters" by its authority link.
- ✅ Intentional divergence is visible and reviewable, not invisible.
- ⚠️ Policy authoring is the main ongoing cost. The MVP keeps the set to seven ([coherence model](../coherence-model.md#mvp-policy-catalog)).
- ⚠️ Ambiguous authority text (N2) still needs a human; the system can only detect the ambiguity.

## Open questions

- Where do decisions that belong to no single repository live (baseline H5: the joint "handoff packet" contract)? Candidate: this repository's `docs/adr/`, with a distinct prefix for ecosystem decisions.
- Are some policies **non-waivable** (no exception possible), in the manner of `autonomous-dev-loop` ADR-0044's hard-coded floor? None of the MVP policies needs it.
