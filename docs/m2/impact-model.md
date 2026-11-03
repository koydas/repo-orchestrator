# Impact model

How a pull request is mapped to relationships, classified and compared. Decisions: [ADR-0013](../adr/0013-m2-pr-impact-contracts.md). Concepts: [domain model](../domain-model.md#relationship-types), [workflows W2](../workflows.md#w2-cross-repository-pr-analysis).

## 1. Snapshots

| Repository | Revision |
|---|---|
| The PR repository, side **base** | `base.sha` as GitHub reports it |
| The PR repository, side **head** | `head.sha` (a fork head is read through the base repository) |
| Every other repository | One SHA per repository, resolved once per run (pin set, `--pin`, or default-branch HEAD), identical on both sides |

Policies run on these frozen snapshots; evaluation has no network path of its own.

## 2. The change set

`changed = API file list (+ rename sources) ∪ blob-level difference of the base and head trees`. The trees are the evidence the API cannot contradict; the list adds renames.

| Condition | Cause | Run |
|---|---|---|
| The pull request cannot be read | `pull_request_not_found`, `fetch_failed`, `rate_limited`, … | incomplete |
| Base or head tree unavailable | the collection cause (`revision_unresolved`, `fetch_failed`, …), side `base` or `head` | incomplete, even if no anchor is touched |
| GitHub reports more changed files than were listed, or the 3000-file cap is reached | `pr_files_truncated` | incomplete |
| The trees differ in a path the list does not explain (list incomplete, or base is not the merge base) | `pr_file_list_mismatch` | incomplete |
| A tree listing is truncated | `tree_truncated` | incomplete |

## 3. Anchoring (C4)

A relationship is **touched** when a changed path falls into its footprint on the PR repository's side:

| Trigger | Footprint | Example |
|---|---|---|
| `anchor` | A declared anchor: a file, or a directory (everything below it) | `docs/adr/` of `autonomous-dev-loop` for the ADR count |
| `request` | An artifact one of the relationship's policies reads: a path or a glob | `apps/*/service.yaml` for P4 claims |
| `reference` | A file linked by a living document of the *other* repository (P5), read at its pinned revision | the renamed `ADR-001-human-as-gate.md` (U4) |
| `authority` | A policy authority, exception authority or repository authority artifact | a quote removed from the P3 authority ADR |
| `unresolved` | The footprint could not be computed (the linking document is unreadable) | the relationship is treated as touched |

Both names of a rename are changed paths. **One hop**: only relationships incident to the PR repository are traversed, plus those governed by a changed authority; there is no transitive closure. Only `declared` relationships are traversed. A relationship without anchors (R4, R10, R11) is never touched.

## 4. Direction and impact class

| Type | Impact class | Direction | Affected repository |
|---|---|---|---|
| `runtime_contract`, `build_dependency`, `deployment`, `shared_resource` | **breaks** | bilateral | the other endpoint, whichever side changed |
| `documentary` | **falsifies** | source → target (`downstream`); a change in the target is `self` | the claimant (target); for `self` the PR repository |
| `tooling_adoption` | **outdates** | same as documentary | the adopter (target); `self` for the adopter itself |
| `conceptual` | **outdates** | source → target only; a change in the target affects nothing | the derived repository |
| (any, via an authority) | by type | `policy` | both endpoints |

The class is by **type**, not by what changed; the changed extracted values (versions, tags, addresses, counts, links) are reported separately as *value changes*, per `(policy, relationship, subject)`, from the base and head outcomes. A relationship no policy evaluates (R6) is *potential impact only* (`evaluated: false`) and never makes a run incomplete.

Note the two notions of "affected": the PR report's `affected_repos` names the repository the pull request can break, falsify or outdate; a finding's own `affected_repos` (as in M1) names the sides of the inconsistency. For U2 the first is `homelab`, the second `chat`.

## 5. What is evaluated

Only the policies of touched relationships, on head and on base, against the other snapshots. Relationships incident to the PR repository that no changed path reaches are listed as `not_touched`, never evaluated.

## 6. Delta labels (by fingerprint)

| Label | Rule | Gate |
|---|---|---|
| `introduced` | On head only; on base the subject was evaluated (consistent, excepted, in conflict), or it is absent from a family the policy enumerates and the policy completed on base | exit 1 |
| `pre-existing` | The same fingerprint on base and head | none, never alone |
| `resolved` | On base only; on head the subject is evaluated (consistent, excepted, in conflict) or has vanished under the M1 vanishing rule | none |
| `undetermined` | One side is `cannot_evaluate`, `failed` or a `policy_clarification` for the subject, or an absent subject cannot be shown absent | exit 2 |

`cannot_evaluate` on either side never becomes `introduced` or `resolved`. A head outcome that cannot be evaluated makes the run incomplete even when no finding exists on either side; a base-only `cannot_evaluate` without a head finding is listed (`base_unevaluated`) and does not by itself make the run incomplete.
