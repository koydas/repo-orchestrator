# M1 — deterministic baseline

> **Status: implemented.** M1 is the first production-capable slice: a read-only, on-demand scan of the six declared repositories with six deterministic policies, evidence-backed reports and a local run store. It uses no LLM and no agent framework. M2 (PR impact analysis) builds on it ([M2 docs](../m2/README.md)); M3–M5 are **not** implemented ([MVP](../mvp.md)).

| Page | Contents |
|---|---|
| [Usage](usage.md) | Installation, running a scan, options, exit codes, offline use, permissions |
| [Declarations](declarations.md) | Repositories, relationships, contracts, locators, pins, exceptions, open questions |
| [Policies](policies.md) | P1–P6 as implemented, the policy interface, how to add or extend one |
| [Report format](report-format.md) | Outcomes, causes, evidence, fingerprints, the semantic/execution split |
| [Evaluation](evaluation.md) | The offline suite against the pinned baseline, results, live scan record |
| [Limitations](limitations.md) | What M1 does not do and where it can be wrong |
| [Decision record](../adr/0011-m1-outcome-contracts-and-reproducible-reports.md) | ADR-0011 (Proposed) |
| [M2: PR impact](../m2/README.md) | `analyze-pr`, C4, the PR report, [ADR-0012](../adr/0012-m2-pr-impact-contracts.md) (Proposed) |

## Data flow as implemented

```mermaid
flowchart LR
    decl[("declarations/*.yaml<br/>repositories · relationships ·<br/>policies · exceptions · pins")] --> load["load + validate<br/>(fail fast)"]
    load --> graph["C2 Relationship graph<br/>declared edges only"]
    graph --> eng
    load --> eng["C3 Policy engine<br/>P1-P6 · precedence · exceptions"]
    eng -- "collection plan<br/>(declared artifacts only)" --> col["C1 Collector"]
    gh[("GitHub REST<br/>read-only: trees + blobs")] --> cache[("artifact cache<br/>repo / SHA / path<br/>= offline fixtures")]
    cache --> col
    col -- "frozen Snapshot" --> eng
    eng --> out["Outcomes<br/>consistent · inconsistent ·<br/>cannot_evaluate · policy_clarification ·<br/>policy_conflict · excepted · failed"]
    out --> rep["Report<br/>semantic part (digest) +<br/>execution metadata"]
    rep --> md["report.md"]
    rep --> js["report.json"]
    out --> store[("SQLite run store<br/>runs · outcomes · fingerprints · history")]
```

One Python process, one SQLite file, no service. Policy evaluation reads only the frozen snapshot; all network access is inside the collector package.

## Package map

| Module | Role |
|---|---|
| `declarations.py`, `graph.py`, `locators.py` | C2: load, validate and hold the declared graph; closed set of locator kinds |
| `collector/` | C1: `github.py` (REST client, retries, budget), `cache.py` (artifact cache), `snapshot.py` (collection, limits), `overlay.py` (in-memory mutations for evaluation) |
| `extract.py`, `evidence.py` | Narrow safe extractors; evidence records and their verification |
| `policies/`, `engine.py` | C3: P1–P6, applicability and precedence, exceptions, repository authorities, fingerprints |
| `report.py`, `store.py`, `runner.py`, `cli.py` | C7 (M1 subset): report, run store, pipeline, command line |

## CI

`.github/workflows/m1.yml` (the S0 spike keeps `spike-langgraph.yml`, untouched) runs on Python 3.11 and 3.13, fully offline: `ruff check`, `ruff format --check`, `mypy` (strict), `pytest` (unit tests, the M1 and M2 evaluations against committed fixtures, documentation and architecture checks), an offline baseline scan (`exit 1` expected: D1 and D2) and an **offline PR analysis** (the M2 evaluation tests, run as their own step, then `analyze-pr` on a recorded pull request, `exit 1` expected). Two manual `workflow_dispatch` inputs add read-only live checks: `live_scan` (six repositories) and `live_pr` (`owner/repo#n`, GET only, `contents: read` and `pull_requests: read`); neither is part of the default gate, and an exit code of 0, 1 or 2 is a result, 3 a failure.
