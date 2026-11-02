# Domain model

Conceptual model, not a schema. Field lists name what each concept must carry to answer the questions in [§ Questions the model answers](#questions-the-model-answers); storage follows [ADR-0008](adr/0008-git-declarations-and-local-sqlite-state.md).

## Concepts

| Concept | What it is | Origin |
|---|---|---|
| **Repository** | A GitHub repository in the analyzed set: `owner/name`, default branch, visibility. | Declared |
| **Repository Snapshot** | A repository at one commit SHA. All evidence of a run refers to snapshots pinned at run start. | Collected |
| **Artifact** | A file or a located part of a file in a snapshot, with a kind: `adr`, `readme`, `instruction` (`CLAUDE.md`, `AGENTS.md`), `skill`, `workflow`, `manifest` (k8s, Argo CD), `config`, `source`, `doc`. | Extracted |
| **Architectural Decision** | An `adr` artifact parsed into number, title, status, supersession links. Format-tolerant: `NNNN-*` and `ADR-NNN-*` both parse (baseline N1). | Extracted |
| **Relationship** | Typed, directed edge between two repositories, optionally anchored to artifacts on each side, with an origin (`declared`, `observed`, `inferred`) ([ADR-0002](adr/0002-explicit-typed-relationship-model.md)). | Declared / observed / inferred |
| **Contract** | The property a `runtime_contract`, `deployment` or `shared_resource` relationship requires to agree: producer locator, consumer locator, comparator (equal, member-of, unique-within-range, exists). | Declared |
| **Coherence Policy** | A rule that makes a divergence matter: id, version, scope, check kind (`deterministic` / `reasoning_assisted`), authority, impact class ([ADR-0003](adr/0003-policy-driven-coherence-with-scoped-exceptions.md)). | Declared (code + parameters) |
| **Intentional Exception** | A recorded, justified, expiring suppression of a policy for a scope. | Declared |
| **Finding** | A policy violation on a relationship, with evidence, verification status, intent, severity and lifecycle state. | Produced |
| **Evidence** | A quote pinned to `(repository, SHA, path, locator)` with its hash and producer ([ADR-0006](adr/0006-evidence-provenance-uncertainty-and-reproducibility.md)). | Produced |
| **Impact Assessment** | For a change set (a PR or a commit range), the relationships it touches and, per affected repository, what can happen: `breaks`, `falsifies`, `outdates`. | Produced |
| **Remediation Plan** | Ordered steps per repository to resolve one or more findings, with ADR compatibility and a prevention step. | Produced, approved by a human |
| **Opportunity** | An improvement across related repositories that is not a policy violation: propagate a proven guardrail, consolidate a shared contract, remove duplicated maintenance. Own lifecycle, benefit evidence, cost/risk assessment, human decision ([§ Opportunity lifecycle](#opportunity-lifecycle), [ADR-0009](adr/0009-optimization-opportunities-as-first-class-output.md)). Never a finding. | Produced, decided by a human |
| **Run** | One execution: pinned snapshots, declaration revision, budgets, Run Record. | Produced |

## Entity relationships

```mermaid
erDiagram
    REPOSITORY ||--o{ REPOSITORY_SNAPSHOT : "captured as"
    REPOSITORY_SNAPSHOT ||--o{ ARTIFACT : contains
    ARTIFACT ||--o| ARCHITECTURAL_DECISION : "parsed as"
    REPOSITORY ||--o{ RELATIONSHIP : "source of"
    REPOSITORY ||--o{ RELATIONSHIP : "target of"
    RELATIONSHIP }o--o{ ARTIFACT : "anchored to"
    RELATIONSHIP ||--o| CONTRACT : "requires"
    COHERENCE_POLICY }o--|| ARTIFACT : "authority"
    COHERENCE_POLICY ||--o{ INTENTIONAL_EXCEPTION : "waived by"
    INTENTIONAL_EXCEPTION }o--|| ARTIFACT : "authority"
    RUN ||--|{ REPOSITORY_SNAPSHOT : pins
    RUN ||--o{ FINDING : "observes"
    FINDING }o--|| COHERENCE_POLICY : violates
    FINDING }o--|| RELATIONSHIP : "concerns"
    FINDING ||--|{ EVIDENCE : "supported by"
    EVIDENCE }o--|| ARTIFACT : quotes
    RUN ||--o| IMPACT_ASSESSMENT : "may produce"
    IMPACT_ASSESSMENT }o--|{ RELATIONSHIP : traverses
    REMEDIATION_PLAN }o--|{ FINDING : resolves
    REMEDIATION_PLAN }o--o{ ARCHITECTURAL_DECISION : "checked against"
    OPPORTUNITY }o--|{ REPOSITORY : spans
    OPPORTUNITY ||--|{ EVIDENCE : "benefit evidence"
    OPPORTUNITY }o--o{ ARCHITECTURAL_DECISION : "checked against"
    OPPORTUNITY |o--o| REMEDIATION_PLAN : "realized by"
```

Cardinality notes:

- A Finding concerns **exactly one** relationship and violates **exactly one** policy. Two policies flagging the same divergence produce two findings; the report groups them by relationship.
- A Finding has **at least one** Evidence per side of the relationship (both repositories), except `cannot_evaluate` outcomes, which are not findings.
- A Policy has **exactly one** authority artifact; it may live in any repository, including this one.
- An Impact Assessment exists only for change-set runs (PR analysis), not for baseline scans.

## Relationship types

| Type | Meaning | Can a change on the source side… | Baseline example |
|---|---|---|---|
| `runtime_contract` | Target consumes data or an interface produced by source at runtime | **break** the target | R2 `findings.json`, R7 model tags |
| `build_dependency` | Target builds against an artifact published by source (package, image, reusable workflow) | **break** the target | none verified in scope; `autonomous-dev-loop` reusable workflows are a candidate |
| `deployment` | Source deploys or configures the target | **break** the deployed target | R6 Argo CD git source |
| `shared_resource` | Both claim from a finite shared resource | **break** either side (collision) | R8 MetalLB pool |
| `tooling_adoption` | Target adopts tooling or conventions published by source | **outdate** the target; break only if the tooling contract changes | R5 plugin declaration |
| `documentary` | Target describes source (counts, capabilities, links) | **falsify** the target's claim | R1 profile, R9 ADR links |
| `conceptual` | Target derives ideas from source (patterns, modeled-after) | **outdate** the target; never break | R4, R10, R11 |

**Direction (M1).** `source` is the repository whose change can affect `target`; for `documentary` edges the *described* repository is the source and the repository making the claim is the target (R1: `autonomous-dev-loop` → `koydas`), for `conceptual` edges the origin of the idea is the source. The [baseline relationship map](research/ecosystem-baseline.md#relationship-map-verified-edges-only) draws R1, R9, R10 the other way ("describes", "cites", "modeled after"); the declarations follow this section ([ADR-0011](adr/0011-m1-outcome-contracts-and-reproducible-reports.md) §3). In M1 a baseline relationship that fans out (R1, R4, R5) is declared as one edge per repository pair (`R1.adl`, `R1.ap`, …) with the baseline id kept.

`build_dependency` and `deployment` are kept distinct because their fix order differs: a build dependency is fixed source-first; a deployment manifest may need the target's change first (R6: the image must exist before the manifest points to it).

## Invariants

1. Every Evidence references a snapshot pinned by its Run. No evidence refers to a moving branch.
2. A Finding in state `confirmed` has evidence status `verified` or `supported` **and** a validation other than `not_validated`. Quote verification is necessary, never sufficient ([ADR-0006](adr/0006-evidence-provenance-uncertainty-and-reproducibility.md)).
3. A Finding never changes verification status because of an LLM output alone; status changes come from code checks or from a human.
4. An Intentional Exception without an authority, an owner or a review date is invalid and not applied.
5. Only relationships with origin `declared` (or `observed` and confirmed) are traversed by impact analysis or evaluated by policies.
6. A Remediation Plan or Opportunity reports ADR compatibility in three tiers: formalized constraints checked, candidate semantic conflicts, ADRs not examined ([coherence model](coherence-model.md#adr-compatibility)). An unresolved candidate conflict blocks approval (fail-closed). "No conflict detected" is never reported as "compatible".
7. Accepted ADRs are point-in-time records: a policy comparing current state reads living documents (README, docs, manifests), not accepted ADRs, unless the ADR is explicitly cited as current (baseline N4).
8. An Opportunity is proposed only with pinned benefit evidence; a declined one keeps its fingerprint and reason and is not proposed again unless its evidence changes.

## Finding lifecycle

```mermaid
stateDiagram-v2
    [*] --> candidate: pre-filter or check flags a divergence
    candidate --> confirmed: deterministic check fails (verified, validated_by_check)
    candidate --> needs_validation: reasoning claim, all quotes verified (supported)
    needs_validation --> confirmed: entailment check passes, or human validates
    needs_validation --> dismissed: entailment check fails, or human rejects the inference
    candidate --> insufficient_evidence: unverified, cannot_evaluate, or budget exhausted
    candidate --> dismissed: refuted, or investigation shows no divergence
    insufficient_evidence --> candidate: next run or human adds evidence
    insufficient_evidence --> dismissed: human decision
    confirmed --> excepted: valid exception matches
    excepted --> confirmed: exception expired or revoked
    confirmed --> acknowledged: human triage
    acknowledged --> remediation_planned: plan approved
    acknowledged --> excepted: human records an exception
    confirmed --> resolved: policy passes on a later snapshot
    acknowledged --> resolved: policy passes on a later snapshot
    remediation_planned --> resolved: policy passes on a later snapshot
    resolved --> confirmed: same fingerprint reappears (regression)
    dismissed --> [*]
```

- State is per **fingerprint** (policy + relationship + normalized locators), so a finding keeps its history across runs.
- `needs_validation` findings are reported in their own section. A `breaks` claim in this state is never presented as a confirmed break.
- `resolved` is only ever set by a check passing on a newer snapshot, never by a human closing it.
- **M1 implements** the deterministic subset: `candidate` → `confirmed` is a single step (the check fails, `verified` and `validated_by_check`), reported with the outcome status `inconsistent`; the run store holds `open`, `excepted`, `conflict` and `resolved` per fingerprint with `regression_count`. `needs_validation`, `insufficient_evidence` (as a finding state), `acknowledged`, `remediation_planned` and `dismissed` require the Investigator or human triage (M3–M4) and are not implemented.
- A regression reopens the same finding with `regression_count` incremented; the report shows its history and the prevention step of the plan that resolved it.

## Uncertainty model

Three independent axes, all categorical ([ADR-0006](adr/0006-evidence-provenance-uncertainty-and-reproducibility.md)):

| Axis | Values | Set by |
|---|---|---|
| **Evidence** — is the cited text authentic at the pinned revision? | `verified` · `supported` · `unverified` · `refuted` | Code (extractor, quote verification) |
| **Validation** — does the conclusion follow from the evidence? | `validated_by_check` · `validated_by_human` · `not_validated` | Code (deterministic check, or an entailment check the policy declares); human |
| **Intent** — whether the divergence is meant | `accidental` · `intentional` · `undetermined` | Exception lookup (code); Investigator proposal; human decision |

Authentic evidence is not a correct claim: verified quotes can be misread or support an invalid inference. An **entailment check** is a deterministic test a policy declares for a reasoning claim (the Investigator locates a value, code compares it). Without one, validation is human.

The Investigator may **propose** `intentional` with a quoted justification (for example a `CLAUDE.md` line documenting the divergence, baseline N2); the proposal is shown as such and becomes `intentional` only through an exception recorded by a human.

A check that cannot run (file missing, parse failure, locator ambiguous) yields `cannot_evaluate` with a reason. It is reported, counted, and never silently treated as `pass`.

**Outcome statuses as implemented in M1** ([ADR-0011](adr/0011-m1-outcome-contracts-and-reproducible-reports.md), [report format](m1/report-format.md)). A policy concludes, per subject of a relationship, one of: `consistent`; `inconsistent` (a confirmed finding); `cannot_evaluate` (with a cause class: collection, artifact, parse, locator, or *by design*, for a claim no deterministic rule can check); `policy_clarification` (applicability ambiguous or an owner decision open: a question, never a finding); `policy_conflict` (a verified repository authority governs the subject); `excepted` (a valid unexpired exception); `failed` (an engine fault). Only `inconsistent` is a finding; only `cannot_evaluate` (except by design) and `failed` make a run partial.

## Opportunity lifecycle

```mermaid
stateDiagram-v2
    [*] --> discovered: signal (finding history, shared contract, guardrail in a source repo)
    discovered --> evidenced: pinned benefit evidence attached
    discovered --> discarded: no benefit evidence
    evidenced --> assessed: benefit, cost, risk, new coupling, ADR compatibility
    assessed --> proposed: passes the anti-uniformity test, no unresolved ADR conflict
    assessed --> discarded: fails the test or conflicts with an accepted ADR
    proposed --> accepted: human
    proposed --> declined: human, reason kept
    proposed --> deferred: human, review date
    deferred --> proposed: review date reached or evidence changed
    declined --> evidenced: evidence changed materially
    accepted --> realized: change verified on a later snapshot
    realized --> [*]
    discarded --> [*]
```

The anti-uniformity test and assessment rules are in the [coherence model](coherence-model.md#optimization-opportunities).

## Questions the model answers

| Question | Answered by |
|---|---|
| Which repositories depend on or document another repository? | Relationships filtered by target and type; `documentary` and `conceptual` are listed separately from `runtime_contract`, `build_dependency`, `deployment`. |
| Which policies apply globally, and which are scoped? | Policy scope; resolution by the precedence rules in the [coherence model](coherence-model.md#precedence). |
| Is a divergence intentional or accidental? | Finding intent: a matching valid exception → `intentional`; otherwise `undetermined` until a human decides, `accidental` when the policy's authority leaves no room (for example a contract version mismatch). |
| What evidence supports a finding? | Its Evidence records, each checkable with `git show <sha>:<path>`. |
| Which repositories would be affected by a proposed change? | Impact Assessment: relationships anchored to the changed artifacts, ranked by type semantics (`breaks` > `falsifies` > `outdates`). |
| Is the proposed correction compatible with existing ADRs? | Only partially decidable. The plan reports formalized constraints checked in code, candidate semantic conflicts with quotes, and ADRs not examined; an unresolved conflict blocks approval (baseline N3 against `ai-dev-tools` ADR-018). |
| Which improvement is worth carrying from one repository to a related one, and at what cost? | Opportunity: benefit evidence, cost, risk, coupling it creates, ADR compatibility, human decision. |
| How can a resolved inconsistency be prevented from recurring? | Every plan carries a prevention step: a deterministic policy here, or a recommended check in the owning repository (for example a consumer test pinning `schema_version`); recurrence is detected by fingerprint. |
