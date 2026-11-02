# Evaluation

M1 is evaluated **offline**, on the pinned ecosystem baseline ([`research/ecosystem-baseline.md`](../research/ecosystem-baseline.md)), against expectations taken from that document. The default test suite makes no network call and needs no credential.

```bash
pip install -e '.[dev]'
pytest                       # whole suite (about 12 s)
pytest tests/test_baseline_eval.py -v   # the D / C / N / U / Q / collection-failure datasets
ruff check src tests && ruff format --check src tests && mypy   # static checks (strict typing)
```

## Fixtures

`tests/fixtures/baseline/<owner>/<repo>/<sha>/` is the artifact cache format: one `tree.json` per repository (the full recursive listing at the pinned commit) and the 35 files the declared plan needs. They were captured by running the collector itself against GitHub at the baseline revisions, then **compared byte for byte with `git show <sha>:<path>`** in the local clones of the six repositories (35 files identical; every tree equal to `git ls-tree -r -t`).

- `tests/test_fixtures.py` re-verifies every file against the git blob id in its tree, requires exactly one tree per repository at its baseline SHA, and requires that the files on disk are exactly those the declared plan collects (a plan change cannot silently leave stale or missing fixtures).
- Fixtures are history. A failing test is investigated in the implementation, or the expectation is corrected with evidence from the pinned revision in the same PR; a fixture is never edited to make a test pass.
- Refresh after a plan change: `repo-orchestrator scan --revisions baseline --cache-dir tests/fixtures/baseline --no-store` (online; only missing artifacts are fetched), then re-run the tests.

Mutations (U1–U5) are applied in memory through `OverlaySource`; no fixture is touched.

## Datasets and where they are tested

| Dataset | Required behaviour | Tests (`tests/test_baseline_eval.py`) |
|---|---|---|
| **D1, D2** | Detect and attribute | `TestSeededPositives`: exactly the two findings; D1 17 vs 20, fix at `profile`; D2 59 vs 65, `attribution: upstream`, first fix site `adl README.md`, names Q2; ADR-0006 fields; stable fingerprints |
| **C1–C8 (+C9)** | No false finding | `TestNegativeControls`: 25 ADRs, 13 incidents, `findings.json` and `status.json` agree, both model tags pulled, three addresses unique and in the pool, three `adt` ADR links resolve, adopter tables match; C9 (`2,400+ tests`) is `cannot_evaluate` by design and does not make the run incomplete |
| **N1–N4** | Preserve intentional divergence and ambiguity | `TestLookAlikes`: both ADR naming formats read, N2 is a `policy_clarification` (Q3) with no finding against `adl`, no opportunity concept in M1 (N3), accepted ADRs are never planned for collection (N4) |
| **U1–U5** | Detect mutations with the expected relationship and impact | `TestMutations`: U1 R2 `schema_version` breaks/high, consumer first; U2 R7 `VISION_MODEL` breaks/high; U3 R8 duplicate `.244` (and out-of-pool variant) breaks/high; U4 R9 dangling ADR link falsifies/medium; U5 on a coherent base (`introduced`), on the raw baseline (same fingerprint as D1: `pre-existing`), and with the upstream README not updated (attribution moves upstream) |
| **Q1–Q6** | Explicit handling | `TestOpenQuestions`: Q1 questions not accusations; Q2 named; Q3 settled only by a parameter; Q4/Q5 referenced by no policy; Q6 private repository refused; every question stays `open` in the report |
| **Collection failures** | Incomplete evidence, never success | `TestCollectionFailures`: a missing repository, a missing artifact, a scope selection; exit code 2, no `consistent` for what could not be read |

The U-cases keep the corrected U5 distinction: M1 does not implement PR analysis, so "introduced" and "pre-existing" are computed in the test (`support.classify`, by fingerprint) from a base and a head scan; on the raw baseline D1 and D2 are `pre-existing`, never blamed on the mutation.

Further suites (all offline):

| File | Covers |
|---|---|
| `test_policies.py` | Variants of every policy by mutation: malformed YAML/JSON, invalid locators, ambiguous matches, unreadable sources, `true` vs `1`, directory vs file links, historical sources, undetermined scope, alternative counting rules |
| `test_engine.py` | Precedence and same-scope conflicts, policy conflicts (repository authority), exceptions (valid, expired, review date, unresolved authority, scope of an exception), duplicate fingerprints, unverifiable evidence, a crashing policy |
| `test_declarations.py` | 40 declaration errors fail fast with file and field; proposals are never evaluated; coverage reasons |
| `test_collector.py`, `test_extract.py` | Retries and backoff, rate limits, budget, permission and missing-repository errors, integrity, truncated trees, cache corruption and unsafe paths, limits, overlay; extractor edge cases, YAML alias bombs, unsafe tags |
| `test_store_and_reproducibility.py` | Identical inputs give an identical report; idempotent persistence; finding lifecycle; `cannot_evaluate` never resolves; determinism violation |
| `test_cli.py` | Exit codes 0–3, outputs, offline use, configuration errors |
| `test_docs.py`, `test_architecture_constraints.py` | Links, anchors, diagrams, ADR index and statuses, scope statements, documented options, exit codes, causes, JSON field names, the example report; no S0/LangGraph/LLM import, no dynamic execution, GET only, I/O only in the collector |

## Results

Recorded on 2026-11-02, Python 3.13 (CI also runs 3.11):

| Check | Result |
|---|---|
| `pytest` | **395 passed**, 0 failed, 0 skipped (about 12 s, offline) |
| Offline baseline scan (exit code) | **1**: complete, exactly the two expected findings |
| `ruff check` / `ruff format --check` | clean |
| `mypy` (strict, 27 source files) | no issues |
| Seeded positives D1, D2 (recall) | 2 / 2, attribution correct for both |
| Mutations U1–U5 (recall) | 5 / 5 (+ 3 variants), expected relationship, impact and severity |
| False findings on C1–C8 and N1–N4 | **0** |
| Evidence validity | every evidence record of every outcome is re-verified in code before it is reported |
| Reproducibility | two scans of the same pinned inputs: identical normalized result, Markdown and digest |

The baseline scan's outcomes: 27 consistent, 2 inconsistent (D1, D2), 3 policy questions (N2: `file_identity` of `adl`; H1: `plugin_keys` of `ollama-chat` and `gitops-homelab`), 1 not checkable by design (C9); 0 cannot-evaluate in the incomplete sense, 0 failures. [Full report](examples/baseline-report.md).

## Real read-only scan

`repo-orchestrator scan` against the six public repositories on 2026-11-02 (default-branch HEADs, GitHub REST through the session proxy, `GET` only): **53 API requests, 15.8 s, run complete, exit code 1**. Report: [`examples/live-scan-2026-11-02.md`](examples/live-scan-2026-11-02.md).

| Repository | HEAD scanned |
|---|---|
| `autonomous-dev-loop` | `1607f6107b1f` (unchanged since the baseline) |
| `ai-dev-tools` | `6c1c1abcaf24` |
| `agent-patterns` | `f5172a941b2f` |
| `ollama-chat` | `1693c3f791a6` (unchanged) |
| `gitops-homelab` | `a8a9d7db5e72` (unchanged) |
| `koydas` | `75304a4586c7` |

Same 27 / 2 / 3 / 1 split as the baseline, with one real change worth noting: the profile now says **20** patterns (it was corrected since the baseline) while `agent-patterns` now has **21** pattern directories. That is the situation U5 describes, found on real commits: the finding is the **same fingerprint as D1** (`5de44d75ee04c4f0`), attributed to the profile, and D2 is unchanged (59 vs 65, root cause in `autonomous-dev-loop`'s README). A second, independent live scan (empty cache, 53 requests again) produced a byte-identical semantic report and was recorded idempotently in the run store (`new_run: false`). Nothing was written to any analyzed repository; those findings are for the owner to act on.

## Metrics ([MVP](../mvp.md#metrics)) for M1

| Metric | Target | Measured |
|---|---|---|
| Precision (deterministic policies, evaluation set) | 1.0 | 1.0 (no false finding; both findings are the documented ones) |
| Recall (seeded and mutation cases) | 1.0 | 1.0 |
| False positives on controls and look-alikes | 0 | 0 |
| Evidence validity | 1.0 | 1.0 (verified before reporting) |
| Attribution (D2-type) | 1.0 | 1.0 |
| Reproducibility | 100 % | 100 % (offline suite and repeated scans) |
| Operational cost | reported | 53 GitHub requests, 15.8 s, 0 LLM tokens (live); 0 requests offline |

Precision against a human judge, missed inconsistencies found by audit, and history replay need a longer evaluation period and are not claimed.
