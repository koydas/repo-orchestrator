# ADR-0013: M2 pull-request analysis contracts: change set, impact, delta, gate and persistence

- **Date:** 2026-11-03
- **Status:** Proposed

## Context

M2 turns the design of C4 and W2 ([architecture](../architecture.md), [workflows](../workflows.md#w2-cross-repository-pr-analysis)) into code. Building it forced decisions that no earlier ADR fixes and that M3 (investigation inside a PR run), the optional PR gate and any later PR-comment ADR will depend on:

- A pull request is **mutable** on GitHub (its head moves, its state changes, its file list is paginated and capped at 3000 files), and the base commit it reports is not always the merge base. What an analysis depends on must be pinned, and what cannot be established must not become a pass.
- [ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md) and [ADR-0011](0011-m1-outcome-contracts-and-reproducible-reports.md) define outcomes and reproducibility for one snapshot. A PR analysis compares two, so it needs a rule for what *introduced*, *pre-existing* and *resolved* mean when one side is `cannot_evaluate`.
- ADR-0011 left two questions open that a PR gate makes concrete: **report-schema versioning** once a second consumer reads the JSON, and whether a **persistent `policy_clarification`** should ever change an exit code.
- The M1 run store keeps finding history by fingerprint across runs. A PR analysis is a hypothetical ("if this PR merged"), not an observation of the repositories' state, so it must not feed that history.

## Decision

1. **PR source boundary (C1 extension).** Two read-only calls: `GET /repos/{slug}/pulls/{n}` and `GET /repos/{slug}/pulls/{n}/files` (paginated, 100 per page). The token needs `contents: read` and `pull_requests: read` (a public repository needs neither). The answer is a frozen `PullRequest`: head SHA, base SHA, changed files with rename sources, the count GitHub reports. Malformed payloads, unsafe paths and unknown statuses are fetch failures. A fork head is read through the base repository; if that fails the head is unavailable.
2. **Offline record format.** A PR record is `<cache>/<owner>/<name>/pulls/<n>/<head sha>.<base sha>.json` with `schema_version`, `kind: pull_request`, base/head, state, `changed_files` and the listed files. It is immutable by construction (named by its head and base SHA, because the base can move without a new head); a live source is asked every time and records the answer. Offline, several recorded inputs need an explicit `--head-sha` and/or `--base-sha`. A record that does not describe the pull request it is filed under is `integrity_mismatch`.
3. **Snapshots.** The PR repository is evaluated at the PR **base** and at the PR **head**. Every other repository is resolved **once** to an immutable SHA (a pin set, `--pin`, or default-branch HEAD) and used identically on both sides. A pin for the PR repository is refused; a pin set's entry for it is ignored. Policy evaluation has no network path: all I/O stays in the collector.
4. **Change set.** The changed paths are the union of the API list (with rename sources) and the blob-level difference of the two trees. The trees are the evidence: a difference the API list does not explain is `pr_file_list_mismatch` (the list is incomplete, or the base is not the merge base); a list shorter than GitHub's count, or at the 3000 cap, is `pr_files_truncated`; a truncated tree cannot be cross-checked (`tree_truncated`). Each makes the run incomplete.
5. **C4 anchoring.** A relationship is *touched* when a changed path falls into its footprint on the PR repository's side: a declared anchor (file, or directory: everything below), an artifact a policy reads (path or glob), a file that a living document of the other repository links (P5), or an authority artifact of a policy, exception or repository authority. A footprint that cannot be computed makes the relationship touched (fail closed), never skipped. One hop: only relationships incident to the PR repository, plus those governed by a changed authority. R6 (deployment) gets declared anchors: MVP scope says impact analysis uses it, and without anchors it could never be touched.
6. **Impact class and direction** come from the relationship type: `runtime_contract`, `build_dependency`, `deployment`, `shared_resource` **break** and are *bilateral* (the other endpoint is affected from either side); `documentary` **falsifies** and `tooling_adoption`, `conceptual` **outdate**, and flow from `source` to `target` (a change in the target itself can only stale its own claim: direction `self`; a conceptual target never outdates its origin). A relationship with no policy is reported as *potential impact only*; it is never incomplete.
7. **Delta by fingerprint**, per finding (`inconsistent` outcome):
   - fingerprint on base and head → `pre-existing`;
   - head only, and the subject was evaluated on base (consistent, excepted, in conflict) or is absent while its policy completed on a base subject family it enumerates → `introduced`;
   - base only, and the subject is evaluated on head (consistent, excepted, in conflict) or has vanished under the M1 vanishing rule → `resolved`;
   - **anything involving `cannot_evaluate`, `failed` or `policy_clarification` on the other side, or an absent subject that cannot be shown absent → `undetermined`.** `undetermined` never becomes `introduced` or `resolved`.
8. **Gate and exit codes** (same codes as M1): `0` complete, no introduced finding; `1` complete, at least one introduced finding; `2` incomplete (PR, base, head or file list missing, a head outcome that could not be evaluated, an undetermined delta; takes precedence over 1, as in ADR-0011 §8); `3` configuration or runtime failure (unknown or undeclared PR repository, bad reference, invalid declarations, determinism violation). **A pre-existing finding never fails a PR gate by itself.** A `resolved` finding never fails it.
9. **Report.** Same two-part contract as ADR-0011 §4: a `semantic` part that is a pure function of declarations, PR head/base/files, pinned SHAs, as-of date and policy/extractor versions (with `result_digest`), and an `execution` part (run id, timestamps, PR state and refs, request counts) that is never hashed. PR state (open/merged) is volatile and therefore execution-only.
10. **Persistence: a separate run kind.** `pr_runs` and `pr_deltas` tables in the same SQLite file; idempotent by `digest(kind, inputs, result)`; two complete runs with equal inputs and different results fail the command. A PR analysis never reads or writes `findings`, `finding_events` or `runs`: baseline-scan history is untouched.
11. **Report-schema versioning** (open in ADR-0011). Every JSON report carries an integer `schema_version`; the PR report also carries `kind: "pr_impact"`. Adding a field keeps the version; renaming, removing, retyping or changing the meaning of a field, or of a cause, status or label value, increments it. A consumer must check `kind` (when present) and refuse a `schema_version` it does not know. The M1 scan report stays at version 1 without a `kind`; it is not retrofitted. The PR report starts at version 1.
12. **`policy_clarification` and exit codes** (open in ADR-0011). In M2 a clarification never changes an exit code, in `scan` or in `analyze-pr`: it is a question for the owner, not a verdict about the code under analysis. The PR report lists the clarifications present on head so a gate consumer can display them. Escalating a clarification that stays unanswered needs history across runs, which a PR analysis deliberately does not have (decision 10); it stays open.

## Rationale

- Pinning the PR by head SHA and cross-checking its file list against the trees removes the two ways a PR analysis silently lies: a moved head and a truncated list. The trees are immutable evidence the API cannot contradict.
- One-hop anchoring from the PR repository keeps the report short and explainable (every touched relationship names the path and the anchor that matched). Treating an uncomputable footprint as touched follows ADR-0004/0006: uncertainty is reported, not dropped.
- Classifying by fingerprint reuses identity that already survives moved numbers and lines (D1 at 17/20 and 17/21 is one finding), which is exactly what "not blamed on this PR" requires.
- `undetermined` is the only classification that is honest when one side is missing; mapping it to exit 2 keeps "missing evidence never becomes implicit success" testable with one rule shared with M1.
- Separate persistence keeps a hypothetical ("if this merged") out of a history of observations; a regression count that included hypothetical PR heads would be meaningless.

## Alternatives considered

- **Trust the API file list alone.** Rejected: it is paginated, capped, and describes a diff against a merge base the base SHA may not be; a truncated list is a silent pass.
- **Evaluate every relationship on both sides and report only what changed.** Rejected as the default: six repositories make it cheap, but it hides *why* a relationship was in the report and does not scale with the declared set. Anchoring is explicit and testable.
- **Transitive impact (two hops and more).** Rejected: noise ([architecture](../architecture.md#c4-impact-analyzer)); revisit when a contract declares transitivity.
- **Treat a pre-existing finding as a warning that fails strict gates.** Rejected: it blames a PR for a state it did not create (the baseline case U5/D1). A consumer that wants strictness runs `scan`.
- **Resolve a base-only finding when its subject has no head outcome.** Rejected, as in ADR-0011: an absent outcome is also what a failed collection looks like.
- **Record PR runs in the finding history.** Rejected: see Rationale.
- **A new JSON `kind` field on the M1 report.** Rejected for now: changing the M1 report for symmetry would be a breaking change for no consumer; `kind` is absent there, and its absence means "scan".

## Consequences

- ✅ U1–U5 replayed as pull requests yield the expected affected repository, impact class, severity and `introduced` label; U5 on the raw baseline is `pre-existing` with the same fingerprint as D1 ([evaluation](../m2/evaluation.md)).
- ✅ Missing PR, base, head, file list or evidence is exit 2 with a named cause; none of them can be a pass or a false introduced/resolved.
- ✅ The M1 report, store tables and CLI are unchanged (the M1 example report changed only in its digests, because R6 gained anchors).
- ⚠️ A base SHA that is not the merge base makes the analysis incomplete (`pr_file_list_mismatch`) rather than guessing; a long-lived PR must be rebased or merged with its base to be analyzed. The API's `base.sha` is taken as reported.
- ⚠️ Only declared anchors, policy reads, P5 link targets and authorities are footprints. A relationship whose real dependency is not declared (R4, R10, R11 have no anchors) is never touched: absence of impact is not evidence of coherence.
- ⚠️ The impact class is by relationship type, not by what changed: R3 is reported as "breaks, potential" for any edit of its anchor file, even when no value changed. Changed values are listed separately.
- ⚠️ A second consumer of the JSON now exists in principle (a CI gate): the versioning rule above is a promise to it; breaking it is an ADR-level change.
- ⚠️ Fork heads depend on GitHub serving the head commit through the base repository.

## Open questions

- Should a persistent `policy_clarification` ever change an exit code (a gate that nobody answers)? Needs history across runs, which a PR analysis does not keep.
- Should a PR whose base is not the merge base be analyzed against the merge base (a compare-API read) instead of being incomplete? It would add an endpoint and a mapping from merge base to a tree.
- Should `breaks` for a relationship with no touched *value* (R3 above) be downgraded to "potential" in the exit code? Today the exit code depends on findings only.
- Will M3's Investigator run inside a PR analysis, and on which side (head only)? This ADR makes no provision for it.
