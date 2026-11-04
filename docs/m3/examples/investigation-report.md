# Cross-repository coherence report

`repo-orchestrator` 0.1.0 · scope adl, adt, ap, chat, homelab, profile · as of 2026-11-02 · run **complete** · result digest `807124339dc4aa8d`

## Summary

| Outcome | Count |
|---|---|
| consistent | 27 |
| inconsistent | 2 |
| cannot_evaluate | 1 |
| policy_clarification | 3 |
| policy_conflict | 0 |
| excepted | 0 |
| failed | 0 |

Findings by severity: high 0, medium 2, low 0. Incomplete evaluations: 0 (plus 1 claim(s) not checkable by design).

## Findings (confirmed by a deterministic check)

### MEDIUM · P1 v1.0.0 · R1.ap · `claim:patterns`

koydas/koydas README.md states 17 patterns; koydas/agent-patterns has 20 (pattern_dirs).

- Fingerprint `5de44d75ee04c4f0` · impact **falsifies** · intent accidental · validation `validated_by_check` · evidence `verified`
- Affected: `profile`, `ap` · pinned: `ap@6d00841fc67d`, `profile@c43e48bbb4ce`
- Comparison `equal`: 17 vs 20 → **not_equal**
- Attribution: **claimant**
- Fix sites, in order: `profile:README.md` (states 17, 20 counted)
- Investigate: `profile:README.md`, `ap:patterns`

Evidence:
- **claimant** `koydas/koydas@c43e48bbb4ce` `README.md:13` — claim 'patterns' in README.md (sha256 `f76996195127`, md_line_value@1)
  ```text
  | [`agent-patterns`](https://github.com/koydas/agent-patterns) | 17 multi-agent patterns indexed by the problem they solve — diagrams, trade-offs, failure modes, runnable code. 12 extracted from the two repos above. |
  ```
- **counted** `koydas/agent-patterns@6d00841fc67d` `patterns` — directories directly under patterns/ (sha256 `6d097a45a014`, count_dirs@1)
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
- **upstream** `koydas/agent-patterns@6d00841fc67d` `README.md:3` — claim 'patterns' in README.md (sha256 `8d1fa0778d3b`, md_line_value@1)
  ```text
  **20 patterns for multi-agent systems — each with a diagram, trade-offs, failure modes, when *not* to use it, and a runnable `impl.mjs`.**
  ```

### MEDIUM · P1 v1.0.0 · R1.adl · `claim:adrs`

koydas/koydas README.md states 59 adrs; koydas/autonomous-dev-loop has 65 (numbered_adr_files). koydas/autonomous-dev-loop README.md repeats 59: the root cause is upstream.

- Fingerprint `e20e9eee6ded8aaf` · impact **falsifies** · intent accidental · validation `validated_by_check` · evidence `verified`
- Affected: `profile`, `adl` · pinned: `adl@1607f6107b1f`, `profile@c43e48bbb4ce`
- Comparison `equal`: 59 vs 65 → **not_equal**
- Attribution: **upstream**
- Depends on open question(s): Q2
- Fix sites, in order: `adl:README.md` (states 59, 65 counted: root cause); `profile:README.md` (repeats the upstream value 59; update after the upstream fix)
- Investigate: `adl:README.md`, `profile:README.md`, `adl:docs/adr`

Evidence:
- **claimant** `koydas/koydas@c43e48bbb4ce` `README.md:11` — claim 'adrs' in README.md (sha256 `f76996195127`, md_line_value@1)
  ```text
  | [`autonomous-dev-loop`](https://github.com/koydas/autonomous-dev-loop) | Issue → AI PR → evidence-backed AI review → auto-fix → human merge, on GitHub Actions. Built after an agent replaced a 26-test suite with an 18-line stub; 11 of its own features were shipped by the loop. 2,400+ tests, 59 ADRs. [Dashboard](https://koydas.github.io/autonomous-dev-loop/). |
  ```
- **counted** `koydas/autonomous-dev-loop@1607f6107b1f` `docs/adr` — numbered ADR files in docs/adr/ (sha256 `ca0f805cb303`, count_numbered_adr_files@1)
  ```text
  0001-trigger-policy-and-label-gate.md
  0002-ai-provider-groq.md
  0003-safe-output-scope.md
  0004-pr-authentication-token-strategy.md
  0005-switch-groq-model-to-qwen3.md
  0006-label-driven-auto-fix-trigger.md
  0007-pr-review-trigger-strategy.md
  0008-smoke-tests-for-cross-module-integration.md
  0009-llm-agent-guardrails.md
  0010-error-taxonomy-and-retry.md
  0011-checkpoint-resume-state-persistence.md
  0012-coverage-enforcement-delegation-to-ci.md
  0013-metrics-append-only-jsonl-and-deduplication.md
  0014-anthropic-prompt-caching.md
  0015-three-tier-json-parsing.md
  0016-changelog-ci-gate.md
  0017-configurable-token-budget.md
  0018-structured-observability.md
  0019-static-verification-backstop.md
  0020-per-pr-workflow-concurrency.md
  0021-protected-write-path-denylist.md
  0022-github-api-retry-policy.md
  0023-trusted-pipeline-execution.md
  0024-tool-evidence-for-pr-review.md
  0025-groq-gpt-oss-120b.md
  0026-withhold-approval-on-unverified-evidence.md
  0027-offline-eval-harness.md
  0028-one-review-per-head-sha.md
  0029-autofix-deterministic-write-guard.md
  0030-live-pr-prompt-evals.md
  0031-eval-gates-are-a-ratchet.md
  0032-primary-provider-with-fallback.md
  0033-review-coverage-gate-reads-ci-gates.md
  0034-review-fix-loop-converges.md
  0035-stale-reference-check.md
  0036-related-adrs-in-review.md
  0037-prompt-budget-impact.md
  0038-withhold-approval-on-unreviewed-code.md
  0039-langgraph-bounded-autofix-prototype.md
  0040-run-journal-and-public-run-record.md
  ... (25 more)
  ```
- **cross_check** `koydas/autonomous-dev-loop@1607f6107b1f` `docs/adr/README.md:7-71` — ADR index entries in docs/adr/README.md (sha256 `bfd5e8f4968f`, count_adr_index@1)
  ```text
  - [ADR-0001: Trigger policy and label gate](./0001-trigger-policy-and-label-gate.md)
  ```
- **upstream** `koydas/autonomous-dev-loop@1607f6107b1f` `README.md:19` — claim 'adrs' in README.md (sha256 `6853fb3868e2`, md_line_value@1)
  ```text
  | **Decided in writing** | [59 ADRs](docs/adr/README.md), each with context, rejected alternatives and trade-offs |
  ```

## Policy clarifications (questions for the owner, not findings)

| Policy | Relationship | Subject | Reason |
|---|---|---|---|
| P6 | R5.adl | `file_identity` | koydas/autonomous-dev-loop .claude/settings.json carries hooks, permissions besides the two plugin keys; ADR-018 says 'the file is identical across repos' (Q3: file or plugin keys?) |
| P6 | R5.chat | `plugin_keys` | koydas/ollama-chat has 3 repository skill(s) but declares no .claude/settings.json; whether ADR-018 covers it is undecided (Q1) |
| P6 | R5.homelab | `plugin_keys` | koydas/gitops-homelab has 3 repository skill(s) but declares no .claude/settings.json; whether ADR-018 covers it is undecided (Q1) |

## Policy conflicts (for a human)

None.

## Intentional exceptions

None.

## Insufficient or undecidable evidence

| Policy | Relationship | Subject | Cause | Reason |
|---|---|---|---|---|
| P1 | R1.adl | `claim:tests` | `not_checkable_deterministically` | no reliable static count exists for a tests claim (coherence-model → Counting claims) |

## Collection failures

None.

## Evaluation failures

None.

## Consistent relationships

| Policy | Relationship | Subject | Reason |
|---|---|---|---|
| P5 | R1.adl | `ref:blob:adl:docs/adr/0052-isolated-grader-for-executable-evals.md` | docs/adr/0052-isolated-grader-for-executable-evals.md exists in koydas/autonomous-dev-loop@1607f6107b1f |
| P5 | R1.adt | `source:profile:README.md` | README.md links no file of koydas/ai-dev-tools |
| P5 | R1.ap | `source:profile:README.md` | README.md links no file of koydas/agent-patterns |
| P5 | R1.chat | `source:profile:README.md` | README.md links no file of koydas/ollama-chat |
| P1 | R1.homelab | `claim:adrs` | 25 adrs claimed, 25 counted (numbered_adr_files) |
| P1 | R1.homelab | `claim:incidents` | 13 incidents claimed, 13 counted (h3_under_h2) |
| P5 | R1.homelab | `ref:blob:homelab:docs/adr/0011-ollama-q4-quantization.md` | docs/adr/0011-ollama-q4-quantization.md exists in koydas/gitops-homelab@a8a9d7db5e72 |
| P2 | R2 | `key_set` | findings.json: consumer reads 6 keys, producer emits 6 |
| P2 | R2 | `schema_version` | findings.json: producer koydas/autonomous-dev-loop has schema_version 1, consumer koydas/ai-dev-tools has 1 |
| P2 | R3 | `key_set` | status.json: key sets agree |
| P2 | R3 | `schema_version` | status.json: a koydas/autonomous-dev-loop has schema_version 1, b koydas/ai-dev-tools has 1 |
| P6 | R5.adl | `adopter_table` | docs/consumer-repos.md lists exactly the 6 skill(s) of koydas/autonomous-dev-loop |
| P6 | R5.adl | `plugin_keys` | koydas/autonomous-dev-loop declares exactly the two plugin keys |
| P6 | R5.ap | `adopter_table` | docs/consumer-repos.md lists exactly the 1 skill(s) of koydas/agent-patterns |
| P6 | R5.ap | `file_identity` | the file holds only the two plugin keys |
| P6 | R5.ap | `plugin_keys` | koydas/agent-patterns declares exactly the two plugin keys |
| P6 | R5.profile | `adopter_table` | docs/consumer-repos.md lists exactly the 1 skill(s) of koydas/koydas |
| P6 | R5.profile | `file_identity` | the file holds only the two plugin keys |
| P6 | R5.profile | `plugin_keys` | koydas/koydas declares exactly the two plugin keys |
| P3 | R7 | `const:TEXT_MODEL` | TEXT_MODEL = 'llama3.1:8b-instruct-q4_0' is pulled |
| P3 | R7 | `const:VISION_MODEL` | VISION_MODEL = 'llava:7b' is pulled |
| P4 | R8 | `address:192.168.1.243` | 192.168.1.243 is inside the pool and claimed once |
| P4 | R8 | `address:192.168.1.244` | 192.168.1.244 is inside the pool and claimed once |
| P4 | R8 | `address:192.168.1.245` | 192.168.1.245 is inside the pool and claimed once |
| P5 | R9 | `ref:blob:adt:docs/adr/ADR-001-human-as-gate.md` | docs/adr/ADR-001-human-as-gate.md exists in koydas/ai-dev-tools@62f30ed88820 |
| P5 | R9 | `ref:blob:adt:docs/adr/ADR-002-three-primitive-architecture.md` | docs/adr/ADR-002-three-primitive-architecture.md exists in koydas/ai-dev-tools@62f30ed88820 |
| P5 | R9 | `ref:blob:adt:docs/adr/ADR-009-tool-grounded-review.md` | docs/adr/ADR-009-tool-grounded-review.md exists in koydas/ai-dev-tools@62f30ed88820 |

## Coverage of declared relationships

| Relationship | Baseline | Type | Source → target | Status | Policies | Note |
|---|---|---|---|---|---|---|
| R1.adl | R1 | documentary | adl → profile | evaluated | P1, P5 |  |
| R1.adt | R1 | documentary | adt → profile | evaluated | P1, P5 |  |
| R1.ap | R1 | documentary | ap → profile | evaluated | P1, P5 |  |
| R1.chat | R1 | documentary | chat → profile | evaluated | P1, P5 |  |
| R1.homelab | R1 | documentary | homelab → profile | evaluated | P1, P5 |  |
| R10 | R10 | conceptual | adl → chat | no_policy | — | declared; no M1 policy evaluates this relationship |
| R11 | R11 | conceptual | adl → orchestrator | not_analyzed | — | an endpoint is outside the M1 repository scope |
| R2 | R2 | runtime_contract | adl → adt | evaluated | P2 |  |
| R3 | R3 | runtime_contract | adl → adt | evaluated | P2 |  |
| R4.adl | R4 | conceptual | adl → ap | no_policy | — | declared; no M1 policy evaluates this relationship |
| R4.adt | R4 | conceptual | adt → ap | no_policy | — | declared; no M1 policy evaluates this relationship |
| R5.adl | R5 | tooling_adoption | adt → adl | evaluated | P6 |  |
| R5.ap | R5 | tooling_adoption | adt → ap | evaluated | P6 |  |
| R5.chat | H1 | tooling_adoption | adt → chat | evaluated | P6 |  |
| R5.fullstack-pilot | R5 | tooling_adoption | adt → fullstack-pilot | not_analyzed | P6 | an endpoint is outside the M1 repository scope |
| R5.homelab | H1 | tooling_adoption | adt → homelab | evaluated | P6 |  |
| R5.profile | R5 | tooling_adoption | adt → profile | evaluated | P6 |  |
| R5.sync-engine | R5 | tooling_adoption | adt → sync-engine | not_analyzed | P6 | an endpoint is outside the M1 repository scope |
| R6 | R6 | deployment | homelab → chat | no_policy | — | declared; no M1 policy evaluates this relationship |
| R7 | R7 | runtime_contract | homelab → chat | evaluated | P3 |  |
| R8 | R8 | shared_resource | homelab → chat | evaluated | P4 |  |
| R9 | R9 | documentary | adt → adl | evaluated | P5 |  |

## Open questions

| Id | Status | Effect in this run | Referenced by |
|---|---|---|---|
| Q1 | open | P6 reports ollama-chat and gitops-homelab as policy_clarification, never as findings. | P6/R5.chat/plugin_keys, P6/R5.homelab/plugin_keys, declaration/R5.chat, declaration/R5.homelab |
| Q2 | open | P1 counts numbered ADR files (provisional default) and cross-checks the index; a claim equal to either count is a policy_clarification, otherwise a finding that names Q2. | P1/R1.adl/claim:adrs |
| Q3 | open | P6 reports extra settings keys (permissions, hooks) as policy_clarification; the reading is the file_identity parameter. | P6/R5.adl/file_identity |
| Q4 | open | No M1 policy depends on it: accepted ADRs are never compared with living documents. | no M1 policy |
| Q5 | open | No M1 policy depends on it. | no M1 policy |
| Q6 | open | The collector refuses a private repository and reports cannot_evaluate (private_repository_out_of_scope). | no M1 policy |

## Policy authorities

| Policy | Authority | Verified | Note |
|---|---|---|---|
| P1 | `profile:CLAUDE.md` | True | resolved, quote verified |
| P2 | `adt:docs/adr/ADR-017-loop-insights-command.md` | True | resolved, quote verified |
| P2 | `adl:docs/adr/0047-findings-export-and-lever-table.md` | True | resolved, quote verified |
| P3 | `chat:docs/adr/0009-fixed-chat-mode-automatic-model-routing.md` | True | resolved, quote verified |
| P4 | `homelab:docs/adr/0008-metallb-config-in-git.md` | True | resolved, quote verified |
| P5 | `orchestrator:docs/adr/0001-cross-repository-coherence-as-product-boundary.md` | True | declared in a repository outside the analyzed set |
| P6 | `adt:docs/adr/ADR-018-consumer-repo-adoption.md` | True | resolved, quote verified |

## Pinned revisions

| Repository | Requested | Commit | Error |
|---|---|---|---|
| `koydas/autonomous-dev-loop` | 1607f6107b1f | `1607f6107b1f50f4e9baa1916a187c7bc16ca18a` |  |
| `koydas/ai-dev-tools` | 62f30ed88820 | `62f30ed888201deb713126db90975983c4d6fab0` |  |
| `koydas/agent-patterns` | 6d00841fc67d | `6d00841fc67d0dbeb3e35e7c2109d201b3979925` |  |
| `koydas/ollama-chat` | 1693c3f791a6 | `1693c3f791a6ebe8dae2cfb90ccc4b168327ae0e` |  |
| `koydas/gitops-homelab` | a8a9d7db5e72 | `a8a9d7db5e725fd47061e8ae7fbfa70d81be3603` |  |
| `koydas/koydas` | c43e48bbb4ce | `c43e48bbb4ce6b70ae70e8652ef05f64a23fcadb` |  |

Declarations digest `01ca03f47401aa24`. Everything above is a function of the pinned revisions and the declarations; run metadata is in the JSON report only.

## Bounded investigation (M3, reasoning-assisted)

Policy P7 v1.0.0 · model `claude-sonnet-5-5` · prompt `c5-1` · run `02fa9d0a1f7a1d2f` · investigation **awaiting_validation** · recorded-response digest `2b95ab0c49f020a0` · deterministic digest `48b8d80560cb88d4`

The Investigator proposes; code verifies every quote and decides. A verified quote never confirms a claim: `confirmed` needs a check or a human. Cost and tokens are in the JSON `execution` part.

| State | Count |
|---|---|
| confirmed | 0 |
| needs_validation | 1 |
| insufficient_evidence | 0 |
| dismissed | 0 |
| policy_question | 2 |

Quotes: 4 verified, 0 unverified. Observations: 0.

### Confirmed (verified quotes and a validation)

None.

### Needs validation (verified quotes, a human decides)

#### P7 · R1.ap · `claim:extracted_from`

all quotes are verified but the conclusion needs validation: the policy declares no entailment check, a human decides.

- state **needs_validation** · evidence `supported` · validation `not_validated` · impact falsifies
- Candidate `c7c7ed61fdd533a3` · evidence digest `e1108904430d`
- Investigator's claim (a proposal): The profile says 12 patterns were extracted; agent-patterns says 16 of 20 have public implementations. (finding: divergent)

Deterministic evidence (pre-collected by code):
- **claimant** `koydas/koydas@c43e48bbb4ce` `README.md:13`
  ```text
  | [`agent-patterns`](https://github.com/koydas/agent-patterns) | 17 multi-agent patterns indexed by the problem they solve — diagrams, trade-offs, failure modes, runnable code. 12 extracted from the two repos above. |
  ```
- **upstream** `koydas/agent-patterns@6d00841fc67d` `README.md:5`
  ```text
  Not a framework. Every pattern here was named after the problem it solved in a pipeline that runs: 16 of 20 have implementations in public repos, [`autonomous-dev-loop`](https://github.com/koydas/autonomous-dev-loop) (GitHub Actions, headless) and [`ai-dev-tools`](https://github.com/koydas/ai-dev-tools) (Claude Code, interactive), and every row names where. The remaining patterns are marked as reference implementations. 11 patterns also have a **Where it came from** section linking the incident or ADR that forced them — for example, `loop-with-guard` exists because an auto-fix agent replaced a 26-test suite with an 18-line stub.
  ```

Investigator quotes (checked in code against the pinned file at the cited lines):
- verified `profile@c43e48bbb4ce` `README.md:13-13`
  ```text
  12 extracted from the two repos above
  ```
- verified `ap@6d00841fc67d` `README.md:5-5`
  ```text
  16 of 20 have implementations in public repos
  ```

### Insufficient evidence (never a pass)

None.

### Policy questions with a proposed intent (outcome unchanged)

#### P7 · R5.chat · `intent:P6/plugin_keys`

the deterministic outcome is unchanged and stays a question for the owner; the intent below is only a proposal.

- state **policy_question** · evidence `supported` · validation `not_validated` · impact outdates
- Candidate `5211682f9281cbab` · evidence digest `3eaaa1f0a518`
- Investigator's claim (a proposal): The repository documents that it is not in the plugin scope. (finding: divergent)
- Proposed intent: **intentional** — ADR-018 scopes the plugin to repositories worked on with Claude Code (justification quote verified: True); not applied
- Depends on open question(s): Q1

Deterministic evidence (pre-collected by code):
- **adopter** `koydas/ollama-chat@1693c3f791a6` `.claude/skills`
  ```text
  homelab-deploy
  new-adr
  test-and-lint
  ```
- **authority** `koydas/ai-dev-tools@62f30ed88820` `docs/adr/ADR-018-consumer-repo-adoption.md:12`
  ```text
  1. **Every repository worked on with Claude Code declares the plugin** in `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`, exactly the two keys of ADR-013 §2). The file is identical across repos.
  ```

Investigator quotes (checked in code against the pinned file at the cited lines):
- verified `adt@62f30ed88820` `docs/adr/ADR-018-consumer-repo-adoption.md:12-12`
  ```text
  1. **Every repository worked on with Claude Code declares the plugin** in `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`, exactly the two keys of ADR-013 §2). The file is identical across repos.
  ```

#### P7 · R5.homelab · `intent:P6/plugin_keys`

the deterministic outcome is unchanged and stays a question for the owner; the intent below is only a proposal.

- state **policy_question** · evidence `supported` · validation `not_validated` · impact outdates
- Candidate `8de24bc11bfb996f` · evidence digest `f880c6a70aed`
- Investigator's claim (a proposal): The repository documents that it is not in the plugin scope. (finding: divergent)
- Proposed intent: **intentional** — ADR-018 scopes the plugin to repositories worked on with Claude Code (justification quote verified: True); not applied
- Depends on open question(s): Q1

Deterministic evidence (pre-collected by code):
- **adopter** `koydas/gitops-homelab@a8a9d7db5e72` `.claude/skills`
  ```text
  adr-new
  gitops-model-bump
  gpu-metrics-visibility
  ```
- **authority** `koydas/ai-dev-tools@62f30ed88820` `docs/adr/ADR-018-consumer-repo-adoption.md:12`
  ```text
  1. **Every repository worked on with Claude Code declares the plugin** in `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`, exactly the two keys of ADR-013 §2). The file is identical across repos.
  ```

Investigator quotes (checked in code against the pinned file at the cited lines):
- verified `adt@62f30ed88820` `docs/adr/ADR-018-consumer-repo-adoption.md:12-12`
  ```text
  1. **Every repository worked on with Claude Code declares the plugin** in `.claude/settings.json` (`extraKnownMarketplaces` + `enabledPlugins`, exactly the two keys of ADR-013 §2). The file is identical across repos.
  ```

### Dismissed (listed, with the reason)

None.
