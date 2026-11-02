# ADR-0006: Evidence is pinned to revisions, findings carry an explicit verification status, and every run is reproducible

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

A cross-repository finding is a claim about two moving targets. D2 in the baseline shows the cost of imprecision: the profile says 59, its source README says 59, the ADR directory holds 65. Which file is "wrong" depends on which revision of which file is read and which counting rule applies. A finding that does not pin these cannot be checked or reproduced a week later.

`autonomous-dev-loop` already uses verification statuses for review findings (`verified`, `supported`, `unverified`, `refuted`, its ADR-0063) and replays recorded LLM answers in its evals (ADR-0027).

## Decision

1. **Evidence** is a record: repository, commit SHA, path, line range (or structured locator such as a YAML path), the exact quoted text, its content hash, and the extractor (name and version) or Investigator call that produced it.
2. A **Repository Snapshot** is identified by `(repository, commit SHA)`. A run pins one snapshot per repository at start; all evidence in that run refers to those snapshots.
3. Every finding carries two independent statuses. **Evidence authenticity is not claim correctness.**
   - **Evidence status**: is the cited text authentic at the pinned revision?
     - `verified`: produced by a deterministic extractor;
     - `supported`: produced by the Investigator, every quote verified in code against the snapshot;
     - `unverified`: at least one quote could not be checked (file unreadable, too large, locator ambiguous);
     - `refuted`: a quote does not exist at the cited location; the claim is discarded and counted.
   - **Validation**: does the conclusion follow from that evidence?
     - `validated_by_check`: a deterministic check produced the conclusion, or an **entailment check** declared by the policy confirmed the reasoning claim (for example the Investigator locates a consumer's version constant, code compares it);
     - `validated_by_human`: a human accepted the inference;
     - `not_validated`.
   Quote verification is necessary, never sufficient. A finding reaches `confirmed` only with evidence `verified` or `supported` **and** validation other than `not_validated`. A `supported`, `not_validated` finding is in state `needs_validation` ([domain model](../domain-model.md#finding-lifecycle)), reported in its own section, and never counted or worded as confirmed. A reasoning-assisted claim of impact class `breaks` without an entailment check always goes to a human.
4. **Confidence is categorical, not numeric.** No probability is attached to findings. The status says how the claim was established; an `intent` field says whether the divergence is `accidental`, `intentional` (a matching exception exists) or `undetermined`.
5. A finding has a **fingerprint**: policy id + relationship id + normalized locators (without line numbers or SHAs), so the same inconsistency is recognized across runs and revisions.
6. Every run writes a **Run Record**: pinned snapshots, policy and relationship declaration revision, tool version, model id and prompt hash per Investigator call, the LLM responses, budgets used, and the outputs. Deterministic stages must reproduce bit-for-bit from the record; Investigator stages are replayed from the recorded responses.
7. Repository content is **data**. Quotes enter prompts fenced and labelled as data; an instruction found in a quote is never followed (as in `ai-dev-tools` ADR-017 §4).

## Rationale

- Pinning to SHAs makes each finding independently checkable by a reader with `git show`.
- Categorical status is honest about what was proven; numeric confidence from an LLM is not calibrated and invites false precision.
- Fingerprints are what allow `resolved` and `regressed` states and longitudinal metrics later.

## Alternatives considered

- **Numeric confidence scores (0–1).** Rejected: uncalibrated, not comparable across policies, and not actionable.
- **Evidence as links to `main`.** Rejected: links move; the finding becomes false or unverifiable after the next commit.
- **Treat verified quotes as a confirmed claim.** Rejected: quotes can be authentic while the inference is wrong (context misread, invalid step); authenticity and correctness are different properties.
- **Store only the final report.** Rejected: the report cannot be reproduced or audited, and eval replay needs the raw LLM responses.

## Consequences

- ✅ Every confirmed finding can be checked by hand from its evidence, and its conclusion was established by code or by a named human decision.
- ⚠️ Reasoning-assisted findings without an entailment check wait on a human; the `needs_validation` backlog is an evaluation metric.
- ✅ Evaluations can be rerun on fixed snapshots at zero LLM cost through replay.
- ⚠️ The Run Record contains quoted repository content; for private repositories it inherits their confidentiality (open question).
- ⚠️ Line-range locators are brittle across revisions; fingerprints exclude them, so two distinct issues on the same locator path can collide. Accepted for the MVP scale.

## Open questions

- Retention period for Run Records and recorded LLM responses.
- Which reasoning-assisted policies can declare an entailment check (count-like sub-claims of P7 can; free-text claims cannot).
