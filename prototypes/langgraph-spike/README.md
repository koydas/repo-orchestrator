# S0 — LangGraph validation spike

> **Throwaway design-validation spike** for [ADR-0005](../../docs/adr/0005-langgraph-for-investigation-workflows-only.md) (Proposed). It validates an orchestration runtime on **synthetic data**; it is not the Repo Orchestrator. No production capability is implemented, nothing here reads GitHub, calls an LLM or needs credentials, and no future core package may import it.

## Question

Does LangGraph give the execution guarantees the investigation and approval workflow needs: durable resume across process restarts, isolated parallel investigations, a budget that a resumed thread cannot reset, and a human approval that survives process death without duplicating an irreversible effect?

## Run

Python ≥ 3.11 (run on 3.11 and 3.13).

```bash
cd prototypes/langgraph-spike
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest            # whole suite, ~40 s; no network, no credentials
```

Drive a run by hand (one process per command; state in `--state-dir`, default `./s0-state`, git-ignored):

```bash
python -m s0_spike --state-dir /tmp/s0 start --run-id demo --scenario baseline    # pauses at the approval gate
python -m s0_spike --state-dir /tmp/s0 status --run-id demo                        # shows pending_report_digest
python -m s0_spike --state-dir /tmp/s0 resume --run-id demo --decision approve --reviewer me --report-digest <digest>
python -m s0_spike graph                                                           # Mermaid of the compiled graph
```

Scenarios: `baseline`, `small`, `too_many`, `all_fail`, `parallel`, `parallel_rev`, `crash_mid_fanout`, `crash_in_finalize`. Exit code 137 means the scripted agent killed the process on purpose; `recover` continues the run.

## Layout

```
src/s0_spike/   limits.py agent.py models.py scenarios.py ledger.py graph.py runner.py cli.py
tests/          one file per experiment (A–F) + classification, routing, framework behaviors, docs
docs/           architecture.md  experiments.md  results.md
requirements.txt  pinned
```

## Documents

| Document | Contents |
|---|---|
| [architecture](docs/architecture.md) | Nodes, edges (generated Mermaid), state and reducers, limits, checkpoint strategy, interrupt semantics, failure boundaries, operator-level APIs, sequence diagram |
| [experiments](docs/experiments.md) | Hypothesis, method, expected, observed, failure cases for experiments A–F |
| [results](docs/results.md) | Status per experiment, commands, framework limitations, cost, risks, LangGraph vs hand-written state machine, recommendation |

## Boundaries

No GitHub discovery, no policies P1–P7, no PR analysis, no remediation, no LLM, no vector store, no API server, no Docker. The deterministic parts (`agent.py`, `models.py`) are plain Python; only `graph.py` and `runner.py` import LangGraph.
