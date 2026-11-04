# M3: report format

`scan --investigate` adds one object to the semantic report, `investigation`, and leaves every M1 field untouched. By [ADR-0013 §11](../adr/0013-m2-pr-impact-contracts.md) an additive change keeps `schema_version` = 1; a report without `--investigate` has no `investigation` and no `deterministic_digest` key, and is byte-identical to M1's. The dashboard reads the new section's counts only ([dashboard](../m1/dashboard.md)).

## Top level

| Field | Meaning |
|---|---|
| `deterministic_digest` | the `result_digest` the same scan has without `--investigate` |
| `result_digest` | digest of the whole semantic report, **including** `investigation` (hence its `recorded_digest`) |
| `investigation.schema` | 1 |
| `investigation.status` | `incomplete` (some `insufficient_evidence`), `awaiting_validation` (some `needs_validation`), `complete` |
| `investigation.inputs` | `run_id`, `model`, `prompt_version`, `effort`, `budget` (the constants), `claims` (policy id and version, declarations digest), `candidates_digest`, `decisions_digest` |
| `investigation.summary` | `candidates`, `by_state`, `quotes_verified`, `quotes_unverified`, `observations`, `awaiting_human`, `pending` (candidates with no result yet) |
| `investigation.candidates` | one row per candidate, sorted by relationship and subject |
| `investigation.skipped` | declared claims whose relationship is outside the selected scope, with the reason (never dropped silently) |
| `investigation.recorded_digest` | digest of the (request key, response digest) pairs the run used |

Usage, tokens, cost and latency are **not** in the semantic part: they are in `execution.investigation` of `report.json` (`mode` live or replay, `usage`, `cost_usd_estimate`, `price_table`, `budget`, `review`).

## Candidate row

```json
{
  "key": "c7c7ed61fdd533a3",
  "policy_id": "P7", "policy_version": "1.0.0",
  "relationship_id": "R1.ap", "subject": "claim:extracted_from", "kind": "qualitative_claim",
  "state": "needs_validation", "evidence_status": "supported", "validation": "not_validated",
  "reason": "all quotes are verified but the conclusion needs validation: ...",
  "cause": null, "impact": "falsifies", "severity": null,
  "claim": "The profile says 12 patterns were extracted; ...", "finding": "divergent",
  "quotes": [{"status": "verified", "reason": "ok", "repo": "profile", "sha": "c43e48bb...", "path": "README.md",
              "line_start": 13, "line_end": 13, "text": "12 extracted from the two repos above", "content_sha256": "..."}],
  "deterministic_evidence": [{"side": "claimant", "repo": "profile", "path": "README.md", "line_start": 13, "quote": "..."}],
  "values": {}, "entailment": null, "intent": null, "observations": [],
  "requires_triage": false, "evidence_digest": "...", "decision": null, "acknowledged": false, "stale_decisions": []
}
```

`deterministic_evidence` is what code collected before the model was involved; `quotes` are the Investigator's, each checked in code. `severity` exists only for a `confirmed` row and is derived from the policy's impact and the relationship type, never by the model. `claim` and `finding` are the Investigator's proposal, shown as such.

## States (`state`)

| State | Meaning |
|---|---|
| `confirmed` | verified quotes **and** `validated_by_check` or `validated_by_human`; counts as a finding (exit 1) |
| `needs_validation` | every quote verified, no check validates the conclusion: a human decides (exit 4); a `breaks` claim also pauses the run at triage |
| `insufficient_evidence` | the system could not establish it (cause below); never a pass (exit 2) |
| `dismissed` | the Investigator proposes no divergence (verified quotes), an entailment check refuted the claim, or a human rejected or dismissed it; listed with the reason |
| `policy_question` | an intent review: the deterministic outcome is unchanged and stays a question for the owner; the proposed intent is shown, not applied |

## Evidence status and validation

| `evidence_status` | Meaning |
|---|---|
| `verified` | produced by a deterministic extractor (the `deterministic_evidence`) |
| `supported` | produced by the Investigator, every quote verified in code |
| `unverified` | at least one quote could not be verified at its pinned location, or none was given |
| `refuted` | a declared deterministic entailment check disproved the claim |

| `validation` | Meaning |
|---|---|
| `validated_by_check` | the policy's entailment check held on values located in verified quotes |
| `validated_by_human` | a recorded human `validate` decision on this evidence |
| `not_validated` | everything else |

A quote is `verified` iff it equals, character for character, a substring of lines `line_start..line_end` of the pinned file (at least 8 characters, at most 20 lines). Any other outcome is `unverified` with a `reason`: `not_found_at_locator`, `revision_mismatch`, `repo_out_of_scope`, `unreadable`, `too_short`, `bad_line_range`, `revision_unresolved`. This refines [ADR-0006](../adr/0006-evidence-provenance-uncertainty-and-reproducibility.md) §3, which words a missing quote as `refuted` ([ADR-0016](../adr/0016-m3-reasoning-verification-and-validation.md) §2).

## Cause codes (`cause`)

| Cause | When |
|---|---|
| `budget` | a budget refused the investigation, a model call or a tool call; or the candidate was not investigated because the run budget was spent |
| `provider_error` | timeout, rate limit, server error (after one retry), authentication or other client error |
| `provider_refusal` | the model declined (`stop_reason: refusal`) |
| `no_credentials` | no key and the call is not recorded |
| `replay_miss` | `--replay` has no recorded response for the request |
| `malformed_output` | still outside the closed schema after one retry |
| `quote_unverified` | no quote, or a quote that does not verify |
| `investigator_undetermined` | the Investigator answered `cannot_determine` |
| `claim_not_found` | reserved for a claim that is no longer present |
| `evidence_missing` | a repository of the candidate has no pinned revision |
| `snapshot_changed` | a resumed run's evidence no longer matches the pinned snapshot (exit 3) |
| `not_investigated` | a frozen candidate with no stored result yet (the run was cut short): reported as incomplete, never hidden; resume the run |
| `internal_error` | an unexpected exception in a worker, isolated to that candidate |

A pre-filter failure carries the M1 cause instead (`locator_no_match`, `locator_ambiguous`, `artifact_not_found`, ...).

## Intent, entailment, observations

- `intent`: `{proposal, justification, quote, justification_verified, applied: false, note}`. The justification must cite a verified quote or it is reported unverified. A proposal never changes a state.
- `entailment`: `{kind, test, status, values, detail?}` with `status` `holds`, `fails` or `not_evaluable`.
- `observations`: `{kind: "instruction_like_text", repo, path, line, excerpt, pattern}` found by code in data shown to the model.

## Markdown

`report.md` is the M1 report followed by `## Bounded investigation (M3, reasoning-assisted)`: a summary table, then one section per state (confirmed, needs validation, insufficient evidence, policy questions, dismissed), each candidate with its deterministic evidence, the Investigator's quotes marked verified or UNVERIFIED, any entailment, intent, decision and observation. Example: [investigation-report.md](examples/investigation-report.md); the JSON section: [investigation-section.json](examples/investigation-section.json). Both are what the committed recording replays to; a test regenerates them.
