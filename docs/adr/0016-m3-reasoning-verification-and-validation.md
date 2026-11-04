# ADR-0016: M3 reasoning claims: quote verification, validation, entailment checks and exit codes

- **Date:** 2026-11-04
- **Status:** Proposed

## Context

[ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md) separates evidence authenticity from claim correctness; [ADR-0004](0004-deterministic-checks-before-agentic-reasoning.md) lets the Investigator propose and code decide. M3 must turn that into exact rules: what makes a quote `verified`, when a candidate is `confirmed`, `needs_validation` or `insufficient_evidence`, and what the CLI exit code says.

## Decision

1. **Candidate states** (the finding lifecycle of [domain-model.md](../domain-model.md#finding-lifecycle), reasoning part): `confirmed`, `needs_validation`, `insufficient_evidence`, `dismissed`, `policy_question`. Evidence status per candidate: `verified | supported | unverified | refuted`. Validation: `validated_by_check | validated_by_human | not_validated`. No numeric confidence anywhere.
2. **Quote verification is exact and located.** A quote cites `(repo, sha, path, line_start, line_end, text)`. It is `verified` iff the repository is in the run, `sha` equals the pinned revision, the file is readable, and `text` equals, character for character (no case, whitespace or Unicode normalization), a substring of lines `line_start..line_end` of that file. The record stores the content hash and blob id from the pinned snapshot. A quote that exists elsewhere in the file, at another revision, with altered whitespace or case, or not at all is `unverified`. **Refinement of ADR-0006 §3:** the ADR words a missing quote as `refuted`; M3 reports every quote that cannot be located as `unverified` (the evidence cannot be established), and reserves `refuted` for a claim that a deterministic entailment check disproves.
3. **Candidate evidence status:** every quote verified and at least one quote → `supported`; anything else → `unverified`; a failed entailment check → `refuted`.
4. **Decision rule (code, pure function).** The model proposes `finding ∈ {divergent, consistent, cannot_determine}`, quotes, named values and an intent. Then: failure/budget/malformed/provider error → `insufficient_evidence` (with cause); no quote or any unverified quote → `insufficient_evidence` (`quote_unverified`), never promoted; `cannot_determine` → `insufficient_evidence`; `consistent` with verified quotes → `dismissed` (listed, with the quotes); `divergent` and `supported`: if the candidate is an intent review → `policy_question` (the outcome of the deterministic policy is unchanged); if the policy declares an entailment check and it holds → `confirmed`/`validated_by_check`; if it fails → `dismissed`/`refuted`; if it cannot be evaluated (a named value is not inside a verified quote) → `needs_validation`; with no entailment check → `needs_validation`. A `breaks` claim without an entailment check is `needs_validation` and also a human-triage interrupt.
5. **Entailment checks** are a closed set of deterministic tests a policy declares (M3: `numeric_relation`, a comparison between two operands, each bound by the declaration to a source line of the deterministic evidence and extracted from it by code with a single-group regular expression, as a whole number token; the Investigator must name the same numbers, otherwise the check is not evaluable and a human decides). No expression language.
6. **Intent.** The Investigator may propose `accidental | intentional | undetermined` with a justification; the justification must cite a verified quote or the proposal is shown as unverified and ignored. A proposal never changes a state; `intentional` becomes real only through an exception recorded by a human ([domain-model.md](../domain-model.md#uncertainty-model)).
7. **Human decisions (W4)** are append-only rows bound to the digest of the evidence they saw: `validate` and `reject` apply to `needs_validation`; `dismiss` applies to `insufficient_evidence`/`needs_validation`; `acknowledge` applies to `confirmed`. A decision whose evidence digest no longer matches is reported as stale and not applied. A human cannot validate an `unverified` candidate.
8. **Exit codes with `--investigate`** (without it, 0–3 are exactly M1's): `3` configuration/runtime failure; `2` the evaluation is incomplete, including any `insufficient_evidence` (it is never a pass); `1` at least one confirmed finding (deterministic or reasoning); `4` complete but awaiting a human (`needs_validation` or a paused triage); `0` otherwise. Precedence: 3 > 2 > 1 > 4 > 0.
9. **Severity** of a confirmed reasoning finding is `severity_for(policy impact, relationship type)` ([coherence-model.md](../coherence-model.md#prioritization)); the model never sets it.
10. **Prompt-injection hygiene.** Repository text is fenced in a labelled data block whose delimiter is derived from the content hash; a closing delimiter inside the data is neutralized; instruction-like text found by code in a quote or tool result is recorded as an `observation` and nothing else changes. The model output schema is closed: unknown fields (a smuggled `state`, `validation`) make the answer malformed.

## Rationale

- Exact, located comparison is the only check that cannot be satisfied by a fluent paraphrase.
- Separating `needs_validation` (exit 4) from `insufficient_evidence` (exit 2) keeps "a human must decide" distinct from "the system could not decide".
- A closed set of entailment checks keeps validation by code auditable.

## Alternatives considered

- **Whitespace/case-insensitive matching.** Rejected: it accepts altered quotes.
- **Whole-file substring matching** (what `evidence.verify` does for deterministic evidence). Rejected for model output: a model can cite a real sentence at a wrong place.
- **Exit 2 for `needs_validation`.** Rejected: it hides a decision pending behind an infrastructure code.
- **LLM-judged entailment.** Rejected: the model would validate its own claim.

## Consequences

- ✅ Zero `confirmed` finding without verified quotes and a check or a human.
- ⚠️ Fresh exit code 4 (only with `--investigate`); CI users must handle it.
- ⚠️ A model that paraphrases instead of quoting yields `insufficient_evidence`; the recall cost is accepted.

## Open questions

- Whether `dismissed` by a `consistent` proposal should also wait for a human (currently listed, not silent).
- Which further entailment kinds are worth declaring (set membership, equality).
