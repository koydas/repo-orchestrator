# MVP

> **Status: M1 and M2 are implemented** ([M1 docs](m1/README.md), [M1 evaluation](m1/evaluation.md); [M2 docs](m2/README.md), [M2 evaluation](m2/evaluation.md)). **M3–M5 are proposed scope and not implemented.** The throwaway S0 spike remains isolated (see the milestone table).

The first release is a **read-only, on-demand analyzer** of a small, declared set of repositories, with seven policies and an evaluation set built from the [ecosystem baseline](research/ecosystem-baseline.md).

## Scope

| Dimension | MVP |
|---|---|
| Repositories | `autonomous-dev-loop`, `ai-dev-tools`, `agent-patterns`, `koydas`, `gitops-homelab`, `ollama-chat` (public, `koydas`) |
| Relationships | Declared by hand: R1–R11 from the baseline. R6 (deployment) is used by impact analysis only (M2: it has anchors, no policy); R4, R10, R11 (conceptual) are declared but no MVP policy evaluates them and no anchor lets a pull request touch them. |
| Policies | P1–P7 ([coherence model](coherence-model.md#mvp-policy-catalog)); P1–P6 deterministic, P7 reasoning-assisted |
| Triggers | A human runs a baseline scan (W1, M1) or a PR analysis (W2, M2) |
| Output | Local Markdown and JSON report; SQLite run store |
| GitHub access | Read-only token |
| LLM | Only from M3, only in the Investigator, only for P7 and intent proposals |

## Milestones

| Milestone | Delivers | Exit criteria |
|---|---|---|
| **M0 Design** (this repository today) | Documentation, ten Proposed ADRs | ADRs reviewed; open questions Q1–Q6 answered or explicitly deferred |
| **S0 LangGraph validation spike** (first implementation activity, before or alongside M1) | Time-boxed, throwaway spike outside the core package, scripted Investigator stand-in, synthetic candidates ([ADR-0005](adr/0005-langgraph-for-investigation-workflows-only.md) §5) | Resume after `interrupt()` across restarts, isolated fan-out, budget guard holding across resume, generated diagram matching the docs, pinned dependencies; outcome (adopt or fallback) recorded in ADR-0005 before M1 completes. No core code depends on it. **Status: executed** in [`prototypes/langgraph-spike`](../prototypes/langgraph-spike/README.md): experiments A–F pass (81 tests, Python 3.11 and 3.13), recommendation *adopt for M3 with explicit constraints*, not measured items listed in the [results](../prototypes/langgraph-spike/docs/results.md#remaining-architectural-risks). ADR-0005 remains Proposed until the owner decides; the exit criterion "outcome recorded in ADR-0005" is met by the pointer added there |
| **M1 Deterministic baseline** | C1, C2 (declared relationships only), C3 with P1–P6, W1, report, run store with fingerprints | Evaluation set (below) passes: 100% recall on seeded and mutation cases for P1–P6, zero findings on negative controls, deterministic rerun produces an identical report. **Status: implemented, in review.** All three criteria are met on the pinned baseline by the offline suite ([results](m1/evaluation.md)); a live read-only scan of the six repositories succeeded and is recorded there. [ADR-0011](adr/0011-m1-outcome-contracts-and-reproducible-reports.md) (Proposed) records the contracts M1 introduced |
| **M2 PR impact** | C4, W2 with base/head comparison | Mutation cases U1–U5 replayed as PRs against a coherent base yield the expected affected repository, impact class and `introduced` label; U5 replayed against the raw baseline (D1 already present) yields `pre-existing`, so no pre-existing finding is blamed on a PR. **Status: implemented.** All criteria are met on the pinned fixtures by the offline suite ([results](m2/evaluation.md)): U1–U5 yield the expected affected repository, impact class, severity and `introduced` label; U5 on the raw baseline is `pre-existing` with D1's fingerprint; a PR that fixes D1 or D2 resolves it; a missing PR, base, head or file list is incomplete, never a pass. [ADR-0012](adr/0012-m2-pr-impact-contracts.md) (Proposed) records the contracts M2 introduced |
| **M3 Bounded investigation** | LangGraph-backed Investigator on the S0-validated design (or the S0 fallback); C5; P7; quote verification; validation status and entailment checks; interrupts | H2 ends in `needs_validation` with verified quotes (a human decides) or `insufficient_evidence`; zero `confirmed` findings with an unverified quote or without validation; every `breaks` reasoning claim without an entailment check routed to a human; LLM cost per run reported |
| **M4 Remediation plans** | C6, W5, prevention steps, regression detection | D1/D2 plans name the correct fix sites and order; a plan for N3 surfaces `ai-dev-tools` ADR-018 as a conflict and cannot be approved until resolved; no report states "compatible"; a reintroduced mutation reopens its finding as a regression |
| **M5 Optimization opportunities** | Opportunity lifecycle and W6 ([ADR-0009](adr/0009-optimization-opportunities-as-first-class-output.md)): discovery from findings history, shared contracts and recorded guardrails; benefit/cost/risk assessment; human decision | Every proposed opportunity has pinned benefit evidence; N3 consolidations discarded against ADR-018; the R3 `status.json` schema candidate assessed with H4 as benefit evidence; declined opportunities never re-proposed |

M1 alone is useful: it would have caught D1 and D2 with no LLM.

## Deferred

| Capability | Why deferred | Prerequisite |
|---|---|---|
| Scheduled ecosystem scans | On-demand runs suffice to measure precision; scheduling adds state-sharing questions ([ADR-0008](adr/0008-git-declarations-and-local-sqlite-state.md)) | M1 precision sustained; decision on where the run store lives |
| Automatic relationship discovery | Low precision expected; must not drive findings ([ADR-0002](adr/0002-explicit-typed-relationship-model.md)) | Observers' proposals measured against declared relationships |
| PR comments | First write path; re-entry risk | Level 2 ADR ([ADR-0007](adr/0007-read-only-operation-and-human-gated-remediation.md)), distinct identity |
| Coordinated issue creation | Re-entry into `autonomous-dev-loop` (`ai-dev-tools` ADR-017 §5) | Level 2 ADR, exclusion from pipeline triggers |
| Coordinated multi-repository PRs | Largest blast radius | Level 3 ADR, measured plan quality |
| Remediation feedback loops | Need resolved findings history first | M4 + several months of runs |
| Longitudinal drift metrics | Need fingerprint history | M4 + scheduled scans |
| Private repositories | Report confidentiality undecided | Answer to Q6 and ADR-0006 open question |

## Evaluation

The evaluation design is part of the MVP, not an afterthought. **The M1 part is implemented** as `tests/test_baseline_eval.py` and companions, offline against committed fixtures ([results](m1/evaluation.md)), and **the M2 part** (the U1–U5 mutation row replayed as pull requests, with the `introduced` / `pre-existing` labels) as `tests/test_pr_eval.py` ([results](m2/evaluation.md)); the P7, history-replay and M3–M4 rows below are still specification.

### Evaluation set

| Case family | Source | Expected outcome |
|---|---|---|
| Seeded positives | D1, D2 at the baseline revisions | One `confirmed`, `verified` finding each; D2 names `autonomous-dev-loop README.md` as the first fix site |
| Negative controls | C1–C8 | No finding |
| Look-alikes | N1, N2, N3, N4 | No finding; N2 yields a policy clarification question; N3 yields no proposed opportunity: if discovered, it is discarded at assessment as contradicting `ai-dev-tools` ADR-018 |
| Mutations | U1–U5, applied in memory to the pinned snapshots (version bump, model tag change, IP collision, renamed ADR, added pattern). U1–U4 mutate relationships that are coherent at the baseline (C3, C5, C6, C7). U5's relationship is not (D1): its base is first normalized in memory (profile count set to 20), and U5 is also run once on the raw baseline | Exactly the expected finding, impact class and affected repository; `introduced` on a coherent base, `pre-existing` for U5 on the raw baseline (same fingerprint as D1) |
| Undecidable | H1 (plugin scope), H2 (qualitative claim) | H1: `undetermined` intent or policy question, never `confirmed` until Q1 is answered; H2: `needs_validation` with verified quotes, or `insufficient_evidence`, never `confirmed` without a human |
| History replay | Past commits of `koydas` README against past commits of the described repositories | Detection at the first commit where the drift existed; measures what a scheduled scan would have caught |

### Metrics

| Metric | Definition | MVP target |
|---|---|---|
| Precision | Confirmed findings a human judges correct / confirmed findings | 1.0 for deterministic policies on the evaluation set; ≥ 0.8 for P7 |
| Recall (seeded) | Expected findings produced / expected findings, over seeded and mutation cases | 1.0 for P1–P6 |
| False positives on controls | Findings on negative controls and look-alikes | 0 |
| Missed inconsistencies | Inconsistencies found by a periodic human audit of a sample of relationships that no policy reported | Reported, no target in the MVP; each one becomes a case or a new policy proposal |
| Evidence validity | Evidence records whose quote exists at the cited SHA and locator (checked by code) | 1.0 |
| Attribution | Findings whose named fix site is correct (D2-type) | 1.0 on the evaluation set |
| Insufficient-evidence ratio | `insufficient_evidence` / reasoning candidates | Reported; rising ratio is a signal to add declarations, not to relax verification |
| Validation backlog | Count and age of `needs_validation` findings | Reported; a growing backlog calls for entailment checks, not for relaxing validation |
| Opportunity quality (M5) | Proposed with pinned benefit evidence / proposed; accepted / proposed; declined as noise; re-proposals of declined ones | 1.0; reported; reported; 0 |
| Reproducibility | Identical report hash on rerun (deterministic) or replay (with recorded LLM responses) | 100% |
| Operational cost | GitHub API calls, wall time, LLM tokens and list-price cost per run and per investigation | Reported per run; budget constants set from M3 measurements |

Precision is judged by a human on every confirmed finding in the MVP period; the judgement is recorded with the finding (dismissals count as false positives).

## Validation criteria for the MVP as a whole

The MVP is worth continuing if, after M4:

1. It reports every seeded and mutation case with correct attribution.
2. It produced zero findings that a human dismissed as noise on the six repositories over the evaluation period, or each dismissal led to a declaration or policy fix.
3. At least one real inconsistency was found that existing single-repository tooling did not report.
4. A full baseline scan of six repositories runs in minutes and costs nothing in LLM calls; a PR analysis with investigations stays within the budget constants.

If 3 fails after a reasonable period, the product boundary ([ADR-0001](adr/0001-cross-repository-coherence-as-product-boundary.md)) should be reconsidered before adding capabilities.

## Future evolution

In order, each gated by the previous one's measurements: optimization opportunities (M5) → PR comments (level 2) → scheduled scans with a shared run store → observed-relationship proposals with measured precision → coordinated issue drafts → coordinated draft PRs (level 3) → longitudinal coherence metrics. Each step that adds a write or a new autonomy level gets its own ADR.

## Overengineering risks

| Risk | Mitigation in this design |
|---|---|
| LangGraph adopted before it has a job, or validated too late | Not used in M1–M2 (both implemented without it); validated early by the isolated S0 spike (executed, see above), built only at M3 ([ADR-0005](adr/0005-langgraph-for-investigation-workflows-only.md)) |
| A policy framework larger than seven policies need | Python functions with metadata; no DSL ([ADR-0003](adr/0003-policy-driven-coherence-with-scoped-exceptions.md)) |
| Generic multi-agent design | One Investigator; everything else deterministic ([ADR-0004](adr/0004-deterministic-checks-before-agentic-reasoning.md)) |
| Graph DB, vector store, services | Explicitly excluded with revisit triggers ([architecture](architecture.md#complexity-budget)) |
| Seven relationship types too fine for six repositories | Types are kept because fix order and severity differ by type; `build_dependency` has no verified MVP instance and is the first candidate to drop if it stays unused |
| Opportunities become a uniformity linter | Anti-uniformity test: benefit evidence required, "same as elsewhere" never counts, declined ones never re-proposed ([ADR-0009](adr/0009-optimization-opportunities-as-first-class-output.md)) |
| Exceptions and precedence rules unused in practice | P6 exercises them on real data (N2, H1); if they stay unused after M4, simplify |
