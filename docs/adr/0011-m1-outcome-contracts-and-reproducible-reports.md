# ADR-0011: M1 outcome contracts, reproducible reports and run identity

- **Date:** 2026-11-02
- **Status:** Proposed

## Context

M1 is the first code that turns the design into observable behaviour ([MVP](../mvp.md)). Building it forced decisions that no earlier ADR fixes and that later milestones (PR impact, investigation, remediation) will depend on:

- [ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md) defines how a *finding* is established, but not what the engine reports for everything else a policy can conclude: a consistent relationship, missing evidence, an ambiguous policy, a conflict with a repository's own rules, a suppressed finding, an engine failure. The baseline shows these are common: the pinned run has 27 consistent subjects, 3 policy questions (N2, H1) and 1 claim that is not checkable by design (C9), against 2 findings.
- ADR-0006 §6 asks that deterministic stages "reproduce bit-for-bit", and [ADR-0008](0008-git-declarations-and-local-sqlite-state.md) stores runs, but neither says what is part of the reproducible content, what identifies a run, or how a repeated evaluation interacts with finding history.
- The domain model and the baseline draw relationship edges in opposite directions for documentary and conceptual types (the baseline diagram follows "describes", the type table follows "who is affected").
- D2 depends on a counting rule that baseline Q2 leaves open.

## Decision

1. **Seven outcome statuses**, one per `(policy, relationship, subject)`: `consistent`, `inconsistent` (the only confirmed finding: a deterministic check failed), `cannot_evaluate`, `policy_clarification`, `policy_conflict`, `excepted`, `failed`.
   - `cannot_evaluate` carries a stable **cause** (`repository_not_found`, `private_repository_out_of_scope`, `artifact_not_found`, `locator_no_match`, `parse_error`, …) grouped in classes `collection`, `artifact`, `parse`, `locator`, `by_design`. Every cause except the `by_design` one (`not_checkable_deterministically`, for example a "2,400+ tests" lower bound) makes the run **partial**.
   - `policy_clarification` is the output when applicability is ambiguous (baseline Q1, Q3): a question for the owner with the open question's id, never a finding.
   - `policy_conflict` is a failing check on a subject that a repository's own verified authority governs ([coherence model](../coherence-model.md#precedence), rule 1).
   - `failed` is reserved for engine faults: a crashing policy, evidence that does not verify, a fingerprint collision. It never hides a result: the run is partial.
2. **Evidence is verified by code before an outcome is reported.** An outcome whose quote is not found at its pinned `(repository, SHA, path)`, or whose content hash differs, becomes `failed(evidence_unverifiable)`.
3. **Edge direction**: `source` is the repository whose change can affect `target`. For `documentary`, `source` is the described repository and `target` makes the claim; for `conceptual`, `source` is the origin of the idea. The baseline relationship map draws R1, R9, R10 the other way; the declarations follow this convention.
4. **A report has two parts.** The *semantic* part is a pure function of the inputs (declarations digest, pinned SHAs, selected scope, `as_of` date, policy and extractor versions, mutation digest) and carries `result_digest`. The *execution* part (run id, timestamps, durations, API and cache statistics, history transitions) is separate, written to the JSON report only, and never hashed. `as_of` is an input because exception expiry depends on it.
5. **Run identity.** `run_key = digest(inputs, result_digest)`. Recording the same run twice only increments an execution counter. Two *complete* runs with equal inputs and different results are a determinism violation and fail the command.
6. **Finding history is keyed by fingerprint** (policy, relationship, subject, normalized locators: no value, line or SHA) and changes only on evidence: `new`, `unchanged`, `regressed`, `state_changed`, `resolved`. A finding is `resolved` only when its subject is evaluated and is `consistent` (or reported under another fingerprint), or when the subject itself has disappeared (a removed link, an address no longer claimed; only subjects of a family the policy declares as enumerated) while its policy completed on the relationship without any `cannot_evaluate` or `failed` outcome; otherwise `cannot_evaluate`, `failed` and `policy_clarification` leave it untouched (`not_evaluated`).
7. **Provisional counting rules** (Q2). A policy parameter can name the open question a rule depends on. A finding that uses such a rule lists the question; if the claimed number equals the count under a declared alternative rule (the ADR index), the outcome is a `policy_clarification`, not a finding.
8. **Exit codes**: 0 complete and no finding; 1 complete with findings; 2 incomplete evaluation (takes precedence over 1: a partial run cannot claim to have found every finding); 3 configuration or runtime failure.
9. **Collection** uses the Git Trees API (one recursive listing per repository) and the Git Blobs API (content-addressed, verified against the blob id), so a fetched artifact is immutable by construction. The artifact cache is keyed by `(repository, SHA, path)` and is also the offline fixture format. Requests are sequential and bounded (attempts, request budget, file size, files per repository, total bytes).

## Rationale

- Distinguishing "consistent", "unknown" and "ambiguous" is what makes "missing evidence never becomes implicit success" testable: each is a different status with a different exit behaviour.
- Excluding volatile metadata from the digest is the only way to test "identical inputs, identical result" without freezing a clock.
- Content-addressed fetching means a cache hit and a network read are indistinguishable in the result.
- Naming the open question on the outcome keeps the decision with its owner: when Q2 is answered, a declaration changes, not code.

## Alternatives considered

- **Booleans (finding / no finding).** Rejected: it collapses missing evidence into "no finding", the failure mode the design exists to prevent.
- **Hashing the whole report including timestamps, or freezing a clock in tests.** Rejected: it tests the clock, and a frozen clock hides time-dependent bugs such as exception expiry.
- **Resolving a finding when its fingerprint is absent from a run.** Rejected: an absent fingerprint is also what a failed collection looks like.
- **Treating a lower-bound claim ("2,400+ tests") as a finding or an approximate pass.** Rejected: no deterministic rule exists; it is reported as not checkable, visibly, without failing the run.
- **Contents API per file / `git clone`.** Rejected: the contents API is path-addressed and mutable; a clone downloads whole repositories and invites running tooling on them.
- **Numeric confidence.** Rejected, as in ADR-0006.

## Consequences

- ✅ Every declared relationship ends in a structured, traceable outcome or in a stated reason why none exists (coverage table).
- ✅ Findings keep one identity across revisions: at the 2026-11-02 HEADs the pattern-count finding has the same fingerprint as D1 although the numbers moved from 17/20 to 20/21.
- ✅ A repeated evaluation of pinned inputs is byte-identical (Markdown and JSON semantic part).
- ⚠️ Replaying an earlier run (identical inputs) never changes history; a regression is only observable on a later input (a new SHA), by design.
- ⚠️ `as_of` makes "today" an explicit input; automation must pass it to compare runs.
- ⚠️ Edge direction differs from the baseline diagram; a note in the domain model records it. Moving the diagram would be an edit to evidence.
- ⚠️ Cause codes are a public vocabulary: renaming one is a breaking change for consumers of the JSON report.

## Open questions

- Should a `policy_clarification` that persists across many runs eventually raise the exit code (a gate that nobody answers)? Decided for M2 in [ADR-0012](0012-m2-pr-impact-contracts.md) §12 (no; still open for persistent history).
- Report schema versioning policy once a second consumer (M2 PR gate) reads it. Proposed in [ADR-0012](0012-m2-pr-impact-contracts.md) §11.
