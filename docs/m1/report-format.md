# Evidence and report format

Decisions: [ADR-0006](../adr/0006-evidence-provenance-uncertainty-and-reproducibility.md), [ADR-0011](../adr/0011-m1-outcome-contracts-and-reproducible-reports.md). Full example: [`examples/baseline-report.md`](examples/baseline-report.md) (generated from the committed fixtures and checked by a test).

## Two parts

`report.json` is `{"semantic": …, "execution": …}`.

- **`semantic`** is a pure function of the inputs (declarations digest, pinned SHAs, scope, `as_of`, policy and extractor versions) and ends with `result_digest`. Identical inputs give an identical `semantic`, `report.md` included. It holds no timestamp, run id, path or machine value.
- **`execution`** is volatile: `run_id`, `started_at`, `duration_ms`, `stats` (API requests, cache hits), `run_key`, `new_run`, and `history` (the finding transitions of this run against the store: `new`, `unchanged`, `regressed`, `state_changed`, `resolved`, `not_evaluated`).

`report.md` renders the semantic part only.

## Outcome statuses

| Status | Meaning | Section | Run effect |
|---|---|---|---|
| `inconsistent` | A deterministic check failed: a **confirmed finding** | Findings | exit 1 |
| `consistent` | Evidence from both sides agrees | Consistent relationships | — |
| `cannot_evaluate` | Evidence missing, unavailable or undecidable | Insufficient evidence / Collection failures | exit 2, except cause `not_checkable_deterministically` |
| `policy_clarification` | Applicability ambiguous or an owner decision is open (Q1–Q3) | Policy clarifications | none |
| `policy_conflict` | A wider policy contradicts a repository's own verified authority | Policy conflicts | none |
| `excepted` | A valid, unexpired exception suppresses the finding | Intentional exceptions | none |
| `failed` | The engine itself failed (policy crash, evidence not verifiable, fingerprint collision) | Evaluation failures | exit 2 |

### `cannot_evaluate` causes

| Class | Causes |
|---|---|
| collection | `repository_not_found`, `private_repository_out_of_scope`, `permission_denied`, `revision_unresolved`, `fetch_failed`, `rate_limited`, `api_budget_exhausted`, `not_cached_offline`, `integrity_mismatch`, `pull_request_not_found` (M2) |
| change set (M2) | `pr_files_truncated`, `pr_file_list_mismatch` ([PR report](../m2/report-format.md)); only `analyze-pr` produces them |
| artifact | `artifact_not_found`, `artifact_too_large`, `tree_truncated`, `artifact_not_collected`, `authority_unresolved` |
| parse | `parse_error`, `unsupported_syntax` |
| locator | `locator_no_match`, `locator_ambiguous`, `locator_invalid`, `reference_not_pinned` |
| by design | `not_checkable_deterministically` |

`failed` outcomes use the internal causes `internal_error` (a policy raised an unexpected exception), `evidence_unverifiable` (a quote or hash does not verify against the pinned snapshot) and `fingerprint_collision` (two distinct findings share one fingerprint; the second is not reported as a finding).

## A finding

```json
{
  "policy_id": "P1", "policy_version": "1.0.0",
  "relationship_id": "R1.adl", "subject": "claim:adrs", "status": "inconsistent",
  "reason": "koydas/koydas README.md states 59 adrs; koydas/autonomous-dev-loop has 65 (numbered_adr_files). …repeats 59: the root cause is upstream.",
  "comparison": {"comparator": "equal", "left": 59, "right": 65, "result": "not_equal",
                 "detail": {"rule": "numbered_adr_files", "alternative_counts": {"adr_index_entries": 65},
                            "upstream_value": 59, "attribution": "upstream"}},
  "severity": "medium", "impact": "falsifies", "intent": "accidental",
  "affected_repos": ["profile", "adl"],
  "pinned": {"adl": "1607f6107b1f50f4e9baa1916a187c7bc16ca18a", "profile": "c43e48bbb4ce6b70ae70e8652ef05f64a23fcadb"},
  "fix_sites": [{"repo": "adl", "path": "README.md", "reason": "states 59, 65 counted: root cause"},
                {"repo": "profile", "path": "README.md", "reason": "repeats the upstream value 59; update after the upstream fix"}],
  "investigate": ["adl:README.md", "profile:README.md", "adl:docs/adr"],
  "open_questions": ["Q2"],
  "fingerprint": "e20e9eee6ded8aaf",
  "evidence": [{"side": "claimant", "repo": "profile", "slug": "koydas/koydas", "sha": "c43e48bb…", "path": "README.md",
                "locator": "claim 'adrs' in README.md", "kind": "text", "quote": "| [`autonomous-dev-loop`]… 59 ADRs …",
                "content_sha256": "…", "blob_sha": "…", "line_start": 11, "line_end": 11, "extractor": "md_line_value@1"}]
}
```

- **Both sides.** A finding carries evidence from the claimant and from the described repository (`claimant`, `counted`, `upstream`; `producer`, `consumer`; `provider`; `pool`, `claim`; `adopter`, `registry`, `authority`).
- **Evidence kinds.** `text`: a verbatim slice of a file (≤ 800 characters, exact lines); `tree_listing`: entry names of a directory (first 40 shown). Both are re-verified by code against the snapshot before the outcome is reported; a mismatch makes the outcome `failed`.
- **Validation.** Every M1 finding is `validated_by_check` with evidence `verified`; there is no numeric confidence anywhere.
- **Fingerprint.** `sha256(policy | relationship | subject | sorted normalized locators)[:16]`: no value, line number or SHA. The same inconsistency keeps its fingerprint when a number moves (D1 at the baseline 17/20, at the 2026-11-02 HEADs 20/21).
- **Order.** Severity, then blast radius (affected repositories), then fingerprint. `first_seen` ages are in the store, not in the reproducible report.

## Other semantic sections

`summary` (counts by status and severity, incomplete count), `inputs` (revisions with requested ref and pinned SHA, declarations digest, scope, `as_of`, policies), `run_status` (`complete` | `partial`), `coverage` (every declared relationship: `evaluated`, `no_policy`, `not_analyzed`, `outside_scope`, `proposal`, with the reason), `authorities` (each policy authority and whether it verified), `exceptions` (`applied`, `expired`, `authority_unresolved`, `unused`), `superseded`, `open_questions` (each with the outcomes that reference it).

## Run store

`.repo-orchestrator/state.sqlite`: `runs` (key, input and result digests, status, `as_of`, tool, declarations digest, revisions, policy versions, summary, execution counter), `outcomes` (every outcome of every run, JSON body), `findings` (state `open | excepted | conflict | resolved`, first run, last seen, regression count), `finding_events`. Recording a run already stored (same inputs and same result) only increments its counter. A finding is **resolved only when its subject is evaluated and found consistent**, or when the subject has disappeared (a removed link, an address no longer claimed) while its policy completed on that relationship with no `cannot_evaluate` or `failed` outcome. Only subjects of a policy's *enumerated families* (`PolicyMeta.enumerated_subjects`: P4 `address:` / `claim-site:` / `claims`, P5 `ref:` / `source:`) can vanish; a static subject such as `adopter_table` that is simply not emitted stays `not_evaluated`; otherwise `cannot_evaluate`, `failed` and clarifications leave it as it was.
