# ADR-0005: LangGraph orchestrates the investigation and approval workflow; the deterministic core stays plain Python

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

The brief names Python and LangGraph as the initial hypothesis. The question is where a stateful graph framework earns its cost.

The workflow ([workflows.md](../workflows.md)) has two very different halves:

- **Deterministic core**: collect snapshots, build the relationship graph, evaluate deterministic policies, compute impact. Linear, fast (seconds to a minute for six repositories), idempotent given pinned revisions. A function pipeline does this well.
- **Investigation and approval**: per-candidate LLM investigations that can fail or run out of budget, routing on their result (supported / insufficient evidence / refuted), and **pauses for a human** (classify intent, approve the report) that may last days. This needs durable state, conditional routing, fan-out and resume.

`autonomous-dev-loop` ADR-0039 already prototypes LangGraph, for a different shape: a single-repository write loop (generate → evaluate → fix). It is not wired to production and does not answer whether LangGraph suits human-in-the-loop, read-only, multi-repository investigations.

## Decision

1. **The deterministic core is plain Python modules with typed inputs and outputs**, callable and testable without LangGraph.
2. **LangGraph is the candidate engine for the investigation and approval workflow** (milestone M3): conditional routing, `Send`-based fan-out over candidates, `interrupt()` for human triage and report approval, and a SQLite checkpointer ([ADR-0008](0008-git-declarations-and-local-sqlite-state.md)).
3. Graph nodes **call** core functions; they do not reimplement them. Removing LangGraph must leave the core intact.
4. Lessons carried over from `autonomous-dev-loop` ADR-0039:
   - caps are module constants, checked in code at the routing function **and** in the node that spends the budget;
   - the graph's input schema exposes only the run request (scope, PR reference), so a resumed thread cannot reset its budget or outcome through `invoke`;
   - no node lets an LLM decide a verdict.
5. **Early, isolated validation spike (S0).** The first implementation activity, before or alongside M1, is a time-boxed, throwaway design-validation spike in a directory separate from the core package and never imported by it. It uses a scripted Investigator stand-in (no provider call, as `autonomous-dev-loop` ADR-0039 does) on synthetic candidates shaped like the baseline cases, and must show all of:
   - resume after an `interrupt()` across process restarts, with the checkpoint as the only state;
   - fan-out of at least 5 investigations with per-investigation failure isolation;
   - the budget guard of §4 holding across a resume (a resumed thread cannot buy extra investigations);
   - a generated diagram that matches the documented workflow;
   - pinned dependency set and checkpoint schema stable across the spike.
   The outcome is recorded in this ADR before M1 completes. If it passes, the LangGraph-backed Investigator is built at M3 on the validated design. If it fails, the fallback is a hand-written state machine over the same SQLite store (the M1/M2 pipeline extended with a `status` column and a resume command). Either way, no M1/M2 code depends on the outcome.

## Rationale

- The value LangGraph offers (durable interrupts, checkpointed fan-out, explicit routing) matches the investigation half exactly and the deterministic half not at all.
- Keeping the core framework-free contains the lock-in and keeps M1/M2 shippable with no orchestration dependency.
- A distinct use from ADR-0039 (read-only, multi-repository, human-paused) produces evidence that ADR-0039 cannot.
- Demonstrating real LangGraph use is an explicit goal of this repository. Discovering checkpoint, interrupt or fan-out limits at M3 would force late rework; S0 moves that risk to the start, at the cost of a throwaway spike.

## Alternatives considered

- **LangGraph for the whole pipeline.** Rejected: the deterministic stages gain nothing and become harder to test and run in isolation.
- **Plain deterministic pipeline only, human steps as separate CLI commands.** Viable, and the fallback. Its cost: resume, fan-out isolation and interrupt state are written by hand. Not rejected; kept as the measured baseline.
- **A workflow engine (Temporal, Prefect, Airflow).** Rejected: a server to operate for a single-user, on-demand tool.
- **GitHub Actions as the orchestrator (labels as state, as in `autonomous-dev-loop`).** Rejected for the core: state in labels requires write access to the analyzed repositories (contradicts [ADR-0007](0007-read-only-operation-and-human-gated-remediation.md)) and is the implicit-state problem ADR-0039 set out to examine.
- **Other agent frameworks (CrewAI, AutoGen, Claude Agent SDK loop).** Rejected as the orchestrator: they center on multi-agent conversation, which [ADR-0004](0004-deterministic-checks-before-agentic-reasoning.md) excludes. The Claude Agent SDK remains a candidate for the Investigator's tool loop inside a node.

## Consequences

- ✅ M1 and M2 ship without LangGraph; the framework decision is taken on S0 evidence before M1 completes, and M3 builds on a validated design.
- ⚠️ S0 is spent before the product's first value (M1) is shown; it is time-boxed and throwaway.
- ✅ Human pauses are durable and resumable.
- ⚠️ A second Python dependency surface (LangGraph, checkpointer) with its own release cadence; pinned.
- ⚠️ Two ways of running code (core functions directly, the graph for investigations) must stay consistent; the graph is the only entry point that writes finding state.

## S0 evidence (added; status unchanged)

The S0 spike was executed in [`prototypes/langgraph-spike`](../../prototypes/langgraph-spike/README.md). Against the criteria of §5: resume after `interrupt()` across process restarts, fan-out of 6 with failure isolation, and the budget guard holding across resume all passed (81 tests on Python 3.11 and 3.13); the generated diagram matches the documentation by test; dependencies are pinned. Not shown: checkpoint schema stability across LangGraph *versions* (the version never changed). Findings that refine §4: the budget must live outside the checkpoint (`Command(update=...)` and `update_state` can rewrite counters), a worker must catch its own exceptions, an interrupted node restarts from its first line, and a start input on a paused thread wedges it, so a facade must be the only entry point. The spike's **proposed** outcome is *adopt LangGraph for M3 with explicit constraints* ([results](../../prototypes/langgraph-spike/docs/results.md#recommendation-for-the-owner-to-decide-adr-0005-stays-proposed)). This record is still **Proposed**: the decision belongs to the owner.

## Open questions

- Is the Investigator's own tool loop a LangGraph subgraph or a single node wrapping an SDK tool loop? Decided by S0.
- Should insufficient-evidence candidates interrupt for a human immediately, or be batched into the final report? Default proposed: batched ([workflows.md](../workflows.md#insufficient-evidence)).
