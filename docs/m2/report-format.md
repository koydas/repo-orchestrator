# PR report format

Decisions: [ADR-0013](../adr/0013-m2-pr-impact-contracts.md) (builds on [ADR-0011](../adr/0011-m1-outcome-contracts-and-reproducible-reports.md) and the [M1 report format](../m1/report-format.md)). Examples: [Markdown](examples/u1-introduced.md), [JSON](examples/u1-introduced.json) (both generated from the offline fixtures and checked by a test).

`pr-impact.json` is `{"semantic": …, "execution": …}`, like M1. `pr-impact.md` renders the semantic part only.

## Semantic part

A pure function of the declarations digest, the pull request's head, base and changed-file list, the pinned SHAs, the as-of date and the policy and extractor versions; it ends with `result_digest`. No timestamp, run id, PR state or request count.

| Field | Contents |
|---|---|
| `schema_version`, `kind` | `1`, `"pr_impact"` |
| `tool`, `inputs` | Version; declarations digest, as-of date, the pull request (repo id, slug, number, base/head SHA, head slug, declared and listed file counts, file-list digest), revisions (role `pr_base`, `pr_head` or `pinned`), policies |
| `run_status` | `complete` \| `partial` |
| `gate` | `result` (`pass` \| `fail` \| `incomplete`), `exit_code` (0, 1, 2), counts, `incomplete_reasons`, the rule text |
| `summary` | Changed paths, touched and not-touched relationships, affected repositories, delta counts, unevaluated count |
| `changed_files` | Path, status, previous path |
| `impact` | Per touched relationship: type, **impact class**, `direction`, `affected_repos`, `triggers` (kind, changed path, what matched), evaluated policies, `evaluated`, and its deltas |
| `value_changes` | Per `(policy, relationship, subject)` whose base and head extracted values differ |
| `findings` | `introduced`, `undetermined`, `pre_existing`, `resolved`: each row is an M1 finding (same fields, evidence verified by code) plus `delta` (`label`, `reason`, base and head status) |
| `problems` | PR-level causes: side (`pull_request`, `base`, `head`, `change_set`), cause, detail |
| `unevaluated`, `base_unevaluated`, `policy_clarifications` | Head outcomes that could not be decided; base ones; questions for the owner (never a verdict) |
| `not_touched` | Incident relationships no changed path reaches |

New cause codes ([M1 list](../m1/report-format.md)): `pull_request_not_found` (collection), `pr_files_truncated`, `pr_file_list_mismatch` (class `change_set`).

## Execution part

`run_id`, `started_at`, `duration_ms`, `stats` (API requests, cache hits and misses), `pull_request` (state, merged, refs), `persisted`, `run_key`, `new_run`. Never hashed.

## Versioning

Every JSON report carries an integer `schema_version`; the PR report also carries `kind`. Adding a field keeps the version; renaming, removing, retyping or changing the meaning of a field, a cause, a status or a label increments it. A consumer checks `kind` and refuses a `schema_version` it does not know. The M1 scan report is at version 1 and has no `kind`.

## Run store

Same SQLite file as M1, separate tables:

| Table | Contents |
|---|---|
| `pr_runs` | One row per distinct analysis (`run_key = digest(kind, inputs, result)`): repository, PR number, base/head SHA, gate result, run status, summary, execution counter |
| `pr_deltas` | The deltas of that run (label, policy, relationship, subject, fingerprint, reason) |

Recording an analysis already stored (same inputs, same result) only increments its counter; two *complete* analyses with equal inputs and different results are a determinism violation and exit 3. The tables `runs`, `outcomes`, `findings` and `finding_events` of the baseline scan are never read or written by `analyze-pr`: a pull request analysis says what a change *would* do, not what the repositories' state *is*.
