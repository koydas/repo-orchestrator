# ADR-0004: Deterministic checks first; bounded agentic reasoning only where they cannot decide

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

Of the baseline's verified relationships, most carry a property that code can compare: a version constant (R2), a key set (R3), a list membership (R7), an IP within a range and unique (R8), a file that exists (R9), a count (D1, D2). The cases that resist code are semantic: does "12 extracted from" contradict "16 of 20 have implementations" (H2)? Is an observed divergence intentional when no exception exists?

The ecosystem has a clear precedent: `autonomous-dev-loop` lets tools, not the model, decide verdicts (its ADR-0024, ADR-0026), and checks the model's quotes in code (ADR-0061, ADR-0063).

## Decision

1. Every policy declares its **check kind**:
   - `deterministic`: an extractor plus a comparator. Its output is `pass`, `fail` or `cannot_evaluate` (with the reason). No LLM.
   - `reasoning_assisted`: a deterministic **pre-filter** produces candidates; the Investigator ([architecture](../architecture.md#c5-investigator)) evaluates only those candidates.
2. **A deterministic `fail` is a confirmed finding without any LLM call.** The LLM may be used afterwards only to draft the explanation text, never to change status, severity or evidence.
3. The Investigator:
   - receives one candidate, the policy, the relationship and pre-collected evidence;
   - may read additional files only from the run's pinned snapshots, through read-only tools (`read`, `search`) with fixed call and size caps;
   - returns a structured claim with verbatim quotes and locations;
   - has no network access, no shell, no write tool.
4. **Every quote the Investigator returns is checked in code** against the snapshot at the cited location. A claim whose quotes do not all verify cannot become `confirmed`. A claim whose quotes all verify is only `supported`: the conclusion still needs validation by an entailment check the policy declares, or by a human ([ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md)).
5. Budgets (candidates per run, tool calls per candidate, tokens per run) are **constants in code**, not configuration, following `autonomous-dev-loop` ADR-0039 §5.
6. The MVP has **one** reasoning role (the Investigator). No multi-agent debate, no planner agent.
7. The same bounded Investigator may look for **candidate semantic conflicts** between a proposed remediation or opportunity and accepted ADRs written in prose. Its output is a candidate conflict with quotes, never a verdict of compatibility ([coherence model](../coherence-model.md#adr-compatibility)).

## Rationale

- Deterministic checks are reproducible, cheap and testable; they set the precision floor.
- Restricting the LLM to candidates bounds cost and keeps it where it adds value (meaning, intent).
- Quote verification turns the LLM's output into evidence that can be audited.

## Alternatives considered

- **LLM reads both repositories and reports inconsistencies.** Rejected: unbounded cost, unverifiable findings, non-reproducible results.
- **Deterministic only.** Viable for most MVP policies and the plan for milestone M1. Rejected as the end state: it cannot address H2-type claims or classify intent, which is where most of the remaining value lies.
- **Several specialized agents (one per relationship type, a critic, a judge).** Rejected for the MVP: no evidence that one bounded investigator plus code verification is insufficient; each additional agent multiplies cost and failure modes.
- **LLM as the final judge of severity.** Rejected: severity derives from policy and relationship semantics ([coherence model](../coherence-model.md#prioritization)).

## Consequences

- ✅ The MVP can ship its first milestone with no LLM at all.
- ✅ Every confirmed finding is either code-proven, or quote-verified and then validated by an entailment check or a human.
- ⚠️ Pre-filters bound recall: a semantic inconsistency no pre-filter flags is never investigated.
- ⚠️ Quote verification proves that the quoted text exists, not that the reasoning about it is correct; hence the separate validation status of ADR-0006, and more findings waiting on a human.

## Open questions

- Should the Investigator be allowed to propose a new pre-filter (as a report entry for a human) when it notices a recurring pattern?
