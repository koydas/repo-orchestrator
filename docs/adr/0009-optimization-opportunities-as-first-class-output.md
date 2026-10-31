# ADR-0009: Optimization opportunities are a first-class output with their own lifecycle

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

The first eight ADRs make the product detect, explain and plan the remediation of **violations**. The owner's intent is wider: across related repositories, some changes are worth making even though no policy is violated:

- **Guardrail propagation.** A guardrail proven in one repository may protect a related one. Illustration: `autonomous-dev-loop`'s hard guardrail "never produce a test file with fewer test cases than the original" (`AGENTS.md`) has a counterpart in `ollama-chat`'s `test-and-lint` skill ("Never shrink an existing test file"). Whether that was propagated deliberately is a hypothesis (baseline R10 records a related "modeled after" link for `new-adr`), but it is the shape of the capability.
- **Contract consolidation.** `status.json` is kept "same shape as autonomous-dev-loop's" by convention in two repositories (baseline R3), with no shared schema artifact (H4).
- **Duplicated maintenance.** The plugin declaration is duplicated in every adopter; `ai-dev-tools` ADR-018 explicitly accepts that cost ("two keys, accepted"), and skills stay per repository by decision (N3).

The last example shows the risk: an "opportunities" feature easily becomes a linter that makes every repository look the same. [ADR-0003](0003-policy-driven-coherence-with-scoped-exceptions.md) §6 already separates opportunities from findings, but defines neither their lifecycle nor how to evaluate them.

## Decision

1. **Opportunity is a distinct entity**, never a Finding and never a severity. Initial kinds (closed set, extended by ADR): `guardrail_propagation`, `contract_consolidation`, `maintenance_deduplication`.
2. **Lifecycle** ([domain model](../domain-model.md#opportunity-lifecycle)): `discovered` → `evidenced` → `assessed` → `proposed` → human decision `accepted` / `declined` / `deferred` → `realized` (verified on a later snapshot), with `discarded` for candidates that fail evidence or assessment.
3. **Anti-uniformity test.** An opportunity is proposed only if all hold:
   1. the repositories involved are related by a declared relationship, or share a concern evidenced in both (the same contract, the same consumer);
   2. **benefit evidence** exists and is pinned like any evidence: an incident, a recurring finding, an ADR in the source repository recording why the guardrail exists, repeated maintenance commits. "The other repositories do it this way" is never benefit evidence;
   3. cost and risk are stated, including any **new coupling** the change creates (a consolidated contract is a new `runtime_contract` relationship with an owner);
   4. ADR compatibility is reported in the three tiers of the [coherence model](../coherence-model.md#adr-compatibility), and an unresolved candidate conflict keeps it out of `proposed` (fail-closed).
4. **Assessment is categorical**: benefit, cost and risk each `high` / `medium` / `low` with the evidence that sets it. No numeric score ([ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md)).
5. **Human decision.** A declined opportunity keeps its fingerprint and reason and is not proposed again unless its evidence changes materially; a deferred one carries a review date.
6. **Same write rules as remediation.** An accepted opportunity becomes a plan under [ADR-0007](0007-read-only-operation-and-human-gated-remediation.md); nothing is written to GitHub without a level ≥ 2 ADR. A realized guardrail propagation should come with a policy that keeps it in place.
7. **Milestone M5**, outside M1. Discovery reuses the findings history (M4) and declared relationships; the LLM, if used, only drafts benefit and risk wording from evidence already collected.
8. **Evaluation**: share of proposed opportunities with verifiable benefit evidence (target: all), human acceptance rate, declined-as-noise rate, re-proposal of declined opportunities (target: none), and look-alike cases that must be discarded (N3 against ADR-018).

## Rationale

- Separating opportunities from findings keeps the violation report precise and gives improvements their own, stricter admission test.
- Requiring benefit evidence is what distinguishes optimization from uniformity.
- Naming the coupling a consolidation creates prevents "removing duplication" from silently adding a dependency.

## Alternatives considered

- **Opportunities as low-severity findings.** Rejected: conflates "something is false" with "something could be better" and floods the finding report.
- **No opportunities until violations are solved.** Rejected: an explicit product goal; deferring it to M5 already bounds the cost.
- **LLM brainstorming across repositories.** Rejected: unbounded cost and a bias toward sameness; proposals would not be evidence-backed.
- **Numeric benefit/cost scoring.** Rejected for the same reasons as numeric confidence in ADR-0006.

## Consequences

- ✅ Improvements across repositories have a place in the model without weakening "coherence is not uniformity".
- ✅ Declined opportunities stop recurring, which is the main source of noise in this kind of tool.
- ⚠️ Benefit evidence is often absent or informal; many candidates will be discarded, and the feature's value is unproven until M5 is evaluated.
- ⚠️ A second lifecycle to maintain alongside findings.

## Open questions

- Where does a consolidated cross-repository contract live and who owns it (baseline H5)?
- Should every accepted `guardrail_propagation` automatically get a policy draft, or only when the owner asks?
