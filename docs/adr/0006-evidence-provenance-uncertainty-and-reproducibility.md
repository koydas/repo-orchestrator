# ADR-0006: Evidence is pinned to revisions, findings carry an explicit verification status, and every run is reproducible

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

A cross-repository finding is a claim about two moving targets. D2 in the baseline shows the cost of imprecision: the profile says 59, its source README says 59, the ADR directory holds 65. Which file is "wrong" depends on which revision of which file is read and which counting rule applies. A finding that does not pin these cannot be checked or reproduced a week later.

`autonomous-dev-loop` already uses verification statuses for review findings (`verified`, `supported`, `unverified`, `refuted`, its ADR-0063) and replays recorded LLM answers in its evals (ADR-0027).

## Decision

1. **Evidence** is a record: repository, commit SHA, path, line range (or structured locator such as a YAML path), the exact quoted text, its content hash, and the extractor (name and version) or Investigator call that produced it.
2. A **Repository Snapshot** is identified by `(repository, commit SHA)`. A run pins one snapshot per repository at start; all evidence in that run refers to those snapshots.
3. Every finding carries a **verification status**:
   - `verified`: produced by a deterministic check;
   - `supported`: produced by the Investigator, every quote verified in code against the snapshot;
   - `unverified`: a claim with at least one quote that could not be checked (file unreadable, too large, locator ambiguous);
   - `refuted`: a quote does not exist at the cited location.
   Only `verified` and `supported` findings reach `confirmed` ([domain model](../domain-model.md#finding-lifecycle)). `refuted` claims are discarded and counted.
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
- **Store only the final report.** Rejected: the report cannot be reproduced or audited, and eval replay needs the raw LLM responses.

## Consequences

- ✅ Every confirmed finding can be checked by hand from its evidence.
- ✅ Evaluations can be rerun on fixed snapshots at zero LLM cost through replay.
- ⚠️ The Run Record contains quoted repository content; for private repositories it inherits their confidentiality (open question).
- ⚠️ Line-range locators are brittle across revisions; fingerprints exclude them, so two distinct issues on the same locator path can collide. Accepted for the MVP scale.

## Open questions

- Retention period for Run Records and recorded LLM responses.
- Whether a `supported` finding needs a second, independent check (a different model or a human) before `confirmed` for high-impact relationship types.
