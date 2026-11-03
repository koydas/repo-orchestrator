# Pull request impact: `koydas/autonomous-dev-loop#101`

`repo-orchestrator` 0.1.0 · base `1607f6107b1f` → head `230fd893f493` · as of 2026-11-02 · run **complete** · gate **fail** (exit 1) · result digest `b886f39a4803dc37`

> Gate rule: exit 0: evaluation complete, no introduced finding · exit 1: complete, at least one introduced finding · exit 2: incomplete (missing PR, base, head, file list or evidence; undetermined delta): never a pass · exit 3: configuration or runtime failure. Pre-existing findings never fail a PR gate by themselves.

## Summary

| Item | Count |
|---|---|
| changed paths | 1 |
| relationships touched | 2 |
| incident relationships not touched | 5 |
| introduced | 1 |
| pre-existing | 0 |
| resolved | 0 |
| undetermined | 0 |
| unevaluated on head | 0 |

Affected repositories: `adt`.

## Introduced by this pull request (fails the gate)

### HIGH · P2 v1.0.0 · R2 · `schema_version`

findings.json: producer koydas/autonomous-dev-loop has schema_version 2, consumer koydas/ai-dev-tools has 1

- Fingerprint `019035c33ba491af` · impact **breaks** · intent accidental · validation `validated_by_check` · evidence `verified`
- Delta **introduced**: on base this subject was consistent; on head it is inconsistent (base consistent, head inconsistent)
- Affected: `adt` · pinned: `adl@230fd893f493`, `adt@62f30ed88820`
- Comparison `equal`: 2 vs 1 → **not_equal**
- Fix sites, in order: `adt:scripts/loop-insights.mjs` (consumer: accept both versions first); `adl:scripts/lib/findings_export.mjs` (producer: change after the consumer accepts both)
- Investigate: `adl:scripts/lib/findings_export.mjs`, `adt:scripts/loop-insights.mjs`

Evidence:
- **producer** `koydas/autonomous-dev-loop@230fd893f493` `scripts/lib/findings_export.mjs:6` — js_const_int:FINDINGS_SCHEMA_VERSION (sha256 `ce4f48b9507f`, js_const_int@1)
  ```text
  export const FINDINGS_SCHEMA_VERSION = 2;
  ```
- **producer** `koydas/autonomous-dev-loop@230fd893f493` `scripts/lib/findings_export.mjs:31` — emitted schema_version (sha256 `ce4f48b9507f`, js_object_value@1)
  ```text
      schema_version: FINDINGS_SCHEMA_VERSION,
  ```
- **consumer** `koydas/ai-dev-tools@62f30ed88820` `scripts/loop-insights.mjs:13` — js_const_int:SUPPORTED_SCHEMA_VERSION (sha256 `d2aa7502f662`, js_const_int@1)
  ```text
  export const SUPPORTED_SCHEMA_VERSION = 1;
  ```
- **consumer** `koydas/ai-dev-tools@62f30ed88820` `scripts/loop-insights.mjs:110` — accepted schema_version (sha256 `d2aa7502f662`, js_guard@1)
  ```text
  if (raw.schema_version !== SUPPORTED_SCHEMA_VERSION) {
  ```
## Undetermined (evidence missing on a side; the run is incomplete)

None.

## Pre-existing (present on base and head; not blamed on this pull request, never fails the gate)

None.

## Resolved by this pull request

None.

## Impact: relationships this pull request touches

| Relationship | Type | Impact | Affected | Direction | Triggers | Policies | Deltas |
|---|---|---|---|---|---|---|---|
| R2 | runtime_contract | **breaks** | `adt` | bilateral | anchor `scripts/lib/findings_export.mjs` (scripts/lib/findings_export.mjs); request `scripts/lib/findings_export.mjs` (scripts/lib/findings_export.mjs) | P2 | introduced `schema_version` |
| R3 | runtime_contract | **breaks** | `adt` | bilateral | anchor `scripts/lib/findings_export.mjs` (scripts/lib/findings_export.mjs); request `scripts/lib/findings_export.mjs` (scripts/lib/findings_export.mjs) | P2 | no finding |

## Changed values (base → head)

| Policy | Relationship | Subject | Base | Head |
|---|---|---|---|---|
| P2 | R2 | `schema_version` | consistent: 1 vs 1 | inconsistent: 2 vs 1 |

## Problems

None.

## Unevaluated on head

None.

## Changed files

1 path(s) (renames count both names); first 40:

- `scripts/lib/findings_export.mjs` (modified)

## Incident relationships not touched

`R1.adl`, `R10`, `R4.adl`, `R5.adl`, `R9`

## Pinned revisions

| Repository | Role | Commit | Error |
|---|---|---|---|
| `koydas/autonomous-dev-loop` | pr_base | `1607f6107b1f50f4e9baa1916a187c7bc16ca18a` |  |
| `koydas/autonomous-dev-loop` | pr_head | `230fd893f493aa7c3277afcccd8755be190e889a` |  |
| `koydas/ai-dev-tools` | pinned | `62f30ed888201deb713126db90975983c4d6fab0` |  |

Declarations digest `01ca03f47401aa24`; changed-file list digest `86d1c140df9e1cbb`. Everything above is a function of the pull request's head and base, the pinned revisions and the declarations; run metadata is in the JSON report only.
