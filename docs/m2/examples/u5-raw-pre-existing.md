# Pull request impact: `koydas/agent-patterns#106`

`repo-orchestrator` 0.1.0 · base `6d00841fc67d` → head `560133333e72` · as of 2026-11-02 · run **complete** · gate **pass** (exit 0) · result digest `a20d75319f03c392`

> Gate rule: exit 0: evaluation complete, no introduced finding · exit 1: complete, at least one introduced finding · exit 2: incomplete (missing PR, base, head, file list or evidence; undetermined delta): never a pass · exit 3: configuration or runtime failure. Pre-existing findings never fail a PR gate by themselves.

## Summary

| Item | Count |
|---|---|
| changed paths | 2 |
| relationships touched | 1 |
| incident relationships not touched | 3 |
| introduced | 0 |
| pre-existing | 1 |
| resolved | 0 |
| undetermined | 0 |
| unevaluated on head | 0 |

Affected repositories: `profile`.

## Introduced by this pull request (fails the gate)

None.

## Undetermined (evidence missing on a side; the run is incomplete)

None.

## Pre-existing (present on base and head; not blamed on this pull request, never fails the gate)

### MEDIUM · P1 v1.0.0 · R1.ap · `claim:patterns`

koydas/koydas README.md states 17 patterns; koydas/agent-patterns has 21 (pattern_dirs).

- Fingerprint `5de44d75ee04c4f0` · impact **falsifies** · intent accidental · validation `validated_by_check` · evidence `verified`
- Delta **pre-existing**: the same finding is present on base and on head: not caused by this pull request (base inconsistent, head inconsistent)
- Affected: `profile`, `ap` · pinned: `ap@560133333e72`, `profile@c43e48bbb4ce`
- Comparison `equal`: 17 vs 21 → **not_equal**
- Attribution: **claimant**
- Fix sites, in order: `profile:README.md` (states 17, 21 counted)
- Investigate: `profile:README.md`, `ap:patterns`

Evidence:
- **claimant** `koydas/koydas@c43e48bbb4ce` `README.md:13` — claim 'patterns' in README.md (sha256 `f76996195127`, md_line_value@1)
  ```text
  | [`agent-patterns`](https://github.com/koydas/agent-patterns) | 17 multi-agent patterns indexed by the problem they solve — diagrams, trade-offs, failure modes, runnable code. 12 extracted from the two repos above. |
  ```
- **counted** `koydas/agent-patterns@560133333e72` `patterns` — directories directly under patterns/ (sha256 `812484b27fe5`, count_dirs@1)
  ```text
  ac-traceability
  bounded-feedback-injection
  checkpoint-resume
  critic-pair
  escalation-ladder
  eval-gate-ratchet
  fan-out
  human-gate
  label-driven-state-machine
  loop-with-guard
  new-pattern
  read-only-meta-loop
  reflexive-loop
  router
  sequential-pipeline
  speculative-race
  staged-prompt-separation
  stateful-upgrade-verification
  tool-grounded-review
  typed-evidence-chain
  validator-first
  ```
- **upstream** `koydas/agent-patterns@560133333e72` `README.md:3` — claim 'patterns' in README.md (sha256 `033be96f70b3`, md_line_value@1)
  ```text
  **21 patterns for multi-agent systems — each with a diagram, trade-offs, failure modes, when *not* to use it, and a runnable `impl.mjs`.**
  ```
## Resolved by this pull request

None.

## Impact: relationships this pull request touches

| Relationship | Type | Impact | Affected | Direction | Triggers | Policies | Deltas |
|---|---|---|---|---|---|---|---|
| R1.ap | documentary | **falsifies** | `profile` | downstream | anchor `README.md` (README.md); anchor `patterns/new-pattern/README.md` (patterns); listing `patterns/new-pattern/README.md` (patterns); request `README.md` (README.md) | P1, P5 | pre-existing `claim:patterns` |

## Changed values (base → head)

| Policy | Relationship | Subject | Base | Head |
|---|---|---|---|---|
| P1 | R1.ap | `claim:patterns` | inconsistent: 17 vs 20 | inconsistent: 17 vs 21 |

## Problems

None.

## Unevaluated on head

None.

## Changed files

2 path(s) (renames count both names); first 40:

- `README.md` (modified)
- `patterns/new-pattern/README.md` (added)

## Incident relationships not touched

`R4.adl`, `R4.adt`, `R5.ap`

## Pinned revisions

| Repository | Role | Commit | Error |
|---|---|---|---|
| `koydas/agent-patterns` | pr_base | `6d00841fc67d0dbeb3e35e7c2109d201b3979925` |  |
| `koydas/agent-patterns` | pr_head | `560133333e7285c9d82adcb16b6620f6d52dff3b` |  |
| `koydas/koydas` | pinned | `c43e48bbb4ce6b70ae70e8652ef05f64a23fcadb` |  |

Declarations digest `01ca03f47401aa24`; changed-file list digest `52dd78447e9e548e`. Everything above is a function of the pull request's head and base, the pinned revisions and the declarations; run metadata is in the JSON report only.
