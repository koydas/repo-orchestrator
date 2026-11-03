# Evaluation

The M2 exit criteria of the [MVP](../mvp.md) and the [evaluation set](../mvp.md#evaluation) (mutation row, replayed as pull requests), measured by the offline suite. **Everything here runs without network access** against committed fixtures: `tests/fixtures/baseline` (the pinned snapshots), `tests/fixtures/pr` (synthetic, versioned PR records, one per case) and `tests/fixtures/real-pr` (three real pull requests, [provenance](../../tests/fixtures/real-pr/README.md)). The expectations are written in the tests from [`docs/mvp.md`](../mvp.md) and the [ecosystem baseline](../research/ecosystem-baseline.md), not derived from the analyzer's output; fixtures are history and were not edited to make a test pass.

## Results

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check src tests` | pass |
| Format | `ruff format --check src tests` | pass (76 files) |
| Types | `mypy` (strict, 33 source files) | pass |
| Tests | `pytest` | **683 passed**, on Python 3.11.18 and on 3.13.18 |
| Offline baseline scan (CLI) | `repo-orchestrator scan --revisions baseline --offline …` | exit 1 (D1, D2), as before |
| Offline PR analysis (CLI) | `repo-orchestrator analyze-pr koydas/autonomous-dev-loop#101 --revisions baseline --offline …` | exit 1 (one introduced finding) |

The 683 tests are the 420 of M1 plus 263 new ones. The M1 tests are unchanged except two documentation-scope tests in `tests/test_docs.py`, which asserted "only M1 is authorized / implemented" and now assert "M1 and M2 are authorized / implemented, M3–M5 and every GitHub write are not" (the closed list is checked item by item, and C4 and M2 must no longer be on it). The M1 example report was regenerated: its two digests changed because R6 gained anchors; its content did not. The M1 evaluation results are otherwise untouched ([M1 evaluation](../m1/evaluation.md)).

| Area | Tests |
|---|---|
| PR source (GET client, validation, offline records) | `test_pr_source.py` 41, `test_pr_fixtures.py` 32 |
| C4 and delta rules | `test_impact.py` 29, `test_delta.py` 30 |
| Evaluation (U1–U5, pre-existing, resolved, missing evidence, determinism, persistence, reports) | `test_pr_eval.py` 46 |
| Real pull requests (offline replay) | `test_real_pr.py` 6 |
| CLI | `test_pr_cli.py` 28 |
| Constraints and docs | `test_architecture_constraints.py` (+ the M2 checks), `test_docs_m2.py` 11 (including the parse of every workflow file and a run of the CI step's own script) |

## M2 exit criteria

| Criterion ([MVP](../mvp.md)) | Met | Evidence |
|---|---|---|
| U1–U5 replayed as PRs against a coherent base yield the expected affected repository, impact class and `introduced` label | yes | table below |
| U5 replayed against the raw baseline (D1 already present) yields `pre-existing` | yes | `test_u5_on_the_raw_baseline_is_pre_existing_never_blamed_on_the_pr`: same fingerprint as D1, values 17 vs 21, exit 0 |
| No pre-existing finding is blamed on a PR | yes | pre-existing never changes the exit code (`test_pr_eval.py`, `test_pr_cli.py`, real PRs) |
| Missing evidence never becomes implicit success or a false `introduced` / `resolved` | yes | missing-evidence table below, every status pair of `test_delta.py` |

## U1–U5 as pull requests

Each case is a PR whose head is the pinned snapshot plus the mutation; the base is the pinned snapshot (for U5 the *other* repositories already carry the profile correction, as in the M1 evaluation).

| Case | PR in | Mutation | Affected | Impact | Severity | Finding | Label |
|---|---|---|---|---|---|---|---|
| U1 | `autonomous-dev-loop` | `FINDINGS_SCHEMA_VERSION` 1 → 2 | `adt` | breaks | high | P2 R2 `schema_version` (2 vs 1) | **introduced** |
| U2 | `ollama-chat` | `VISION_MODEL` `llava:7b` → `llava:13b` | `homelab` | breaks | high | P3 R7 `const:VISION_MODEL` | **introduced** |
| U3 | `gitops-homelab` | new Service claims `192.168.1.244` | `chat` | breaks | high | P4 R8 `address:192.168.1.244` (duplicate) | **introduced** |
| U4 | `ai-dev-tools` | `ADR-001-human-as-gate.md` renamed | `adl` | falsifies | medium | P5 R9 `ref:blob:adt:docs/adr/ADR-001-human-as-gate.md` (dangling) | **introduced** |
| U5 | `agent-patterns` | new pattern directory, README 21 | `profile` | falsifies | medium | P1 R1.ap `claim:patterns` (20 vs 21) | **introduced** |
| U5 raw | `agent-patterns` | same, on the raw baseline | `profile` | falsifies | medium | P1 R1.ap `claim:patterns` (17 vs 21) | **pre-existing**, exit 0 |

Each of U1–U5 yields exactly one finding and no other delta. U4's trigger is a `reference` (the renamed file is a link target in an `autonomous-dev-loop` document, found by reading that document), U3's a `request` (the new file matches the claim glob `apps/*/service.yaml`), the others are anchors. U1 also touches R3 (same producer file), reported with no finding.

## Resolved, no impact, and the other pre-existing finding

| Case | Result |
|---|---|
| A PR in the profile corrects the pattern count (D1) | `resolved` (P1 R1.ap `claim:patterns`); D2 remains `pre-existing`; exit 0 |
| A PR in the profile corrects the ADR count (D2) | `resolved` (P1 R1.adl `claim:adrs`); D1 remains `pre-existing`; exit 0 |
| A PR in `ollama-chat` adds a file no anchor, request, link or authority reaches | no impact, no delta, complete, exit 0; the incident relationships are listed as not touched |
| A PR in `autonomous-dev-loop` removes the dangling link on a base that already dangles | `resolved` through the vanishing rule (the subject is gone while P5 completed) |

## Missing evidence (never a pass)

| Case | Result |
|---|---|
| Base commit unavailable | `partial`, exit 2, problem side `base`; potential impact still listed |
| Head commit unavailable | `partial`, exit 2, problem side `head` |
| Base or head unavailable, no anchor touched | still exit 2 |
| The pull request cannot be read | `partial`, exit 2, `pull_request_not_found`; no impact computed |
| Changed-file list shorter than GitHub's count (`truncated` fixture) | exit 2 (`pr_files_truncated`), the introduced finding stays visible; precedence of 2 over 1 |
| File list that misses a changed file | exit 2 (`pr_file_list_mismatch`); the trees still reveal the change |
| `cannot_evaluate` on base, finding on head | `undetermined`, never `introduced`, exit 2 |
| Finding on base, `cannot_evaluate` on head (profile README deleted) | `undetermined` for both D1 and D2, never `resolved`, exit 2 |
| Consumer file deleted (nothing wrong on base) | exit 2, two unevaluated head outcomes, no finding |
| PR repository not declared / declared but not analyzed / bad reference / `--pin` of the PR repository | configuration error, exit 3 |

## Determinism and persistence

Every committed case (ten) is analyzed twice: the semantic part, its JSON serialization and the Markdown are identical, the volatile `run_id` differs. The semantic part contains no `run_id`, timestamp, duration, request count, PR state or branch name. Recording is idempotent (the second run increments a counter); two pull requests are two runs; `findings`, `finding_events` and `runs` of a baseline scan are byte-identical before and after any number of PR analyses (compared in `test_the_run_is_recorded_idempotently_and_leaves_the_baseline_history_untouched`).

## Real pull requests

Three real pull requests are replayed offline ([provenance and caveats](../../tests/fixtures/real-pr/README.md); these records come from git, not from the REST API):

| PR | Touched | Result |
|---|---|---|
| `koydas/gitops-homelab#2` (merged) | R1.homelab, R7, R8 (affects `profile` and `chat`) | Complete, exit 0, 3 `pre-existing`, nothing introduced. The changed values show what the PR did: it adds `qwen2.5vl:3b` to the model list homelab pulls, and the ADR count seen by the profile claim moves 17 → 18 |
| `koydas/gitops-homelab#1` (merged) | R1.homelab | Complete, exit 0, nothing introduced |
| `koydas/ollama-chat#12` | none (`package-lock.json`) | Complete, no impact, exit 0 |

The three `pre-existing` findings of `gitops-homelab#2` are an artefact of analyzing an **old merged** pull request against **today's** other repositories: the profile pinned at the baseline states 25 ADRs and 13 incidents (the current counts), the PR's head has 18 and 10; `ollama-chat` at the baseline hard-codes `llava:7b`, which the older homelab did not pull. They are on both sides, so they are not blamed on the pull request, which is the intended behaviour; an open pull request is analyzed against the present and does not have this anachronism.

## Live read-only analysis

**Not performed through the REST client; blocker stated.** The session that implemented M2 had anonymous git reads of the public `koydas` repositories only. Its GitHub API access was limited to this repository: `GET api.github.com/repos/koydas/<analyzed repo>/pulls` was refused ("GitHub access to this repository is not enabled for this session"), and the only grant available for the analyzed repositories without a write-capable credential was anonymous git. Attaching them with credentials was not done: the analyzed repositories are read-only inputs and the task forbids a write path. Consequences:

- Real pull requests **exist** in all six repositories (`git ls-remote 'refs/pull/*/head'`: for example 12 in `ollama-chat`, 4 in `gitops-homelab`, 10 in `koydas`, 20 in `agent-patterns`).
- The GET client for `pulls/{n}` and `pulls/{n}/files` is exercised by scripted-transport tests (pagination, truncation, 404, malformed payloads, budget) but **has not run against api.github.com**. The first live run is the manual `workflow_dispatch` input `live_pr` of `.github/workflows/m1.yml` (`contents: read`, `pull-requests: read`, GET only).
- The **request cost** of a live analysis was not measured; it is bounded by `--max-requests` (default 400).
- The three replays above use real SHAs and real content, with PR metadata derived from git as stated.

## Not measured

Live wall time and request count; analysis of an open pull request on GitHub; behaviour on a fork head; a pull request near the 3000-file cap (only the rule is tested).
