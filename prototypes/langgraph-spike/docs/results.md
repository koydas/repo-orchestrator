# S0 results

Run date 2026-11-02. Everything below was executed; nothing is extrapolated unless marked **estimate** or **not measured**.

## Status

| Experiment | Tests | Status | Evidence |
|---|---|---|---|
| A Durable checkpoints | 4 | **Pass** | Resume in new OS processes; hard kill (`os._exit(137)`) mid fan-out; only the killed task re-ran; budget and metadata intact |
| B Parallel investigation | 5 | **Pass** | 6 candidates, reversed completion orders, identical digests; failure did not erase successes; 12-candidate run kept all 12 |
| C Failure isolation | 8 + 19 unit | **Pass** | Exception, malformed output, missing evidence, fabricated quote, budget exhaustion, kill: none reported as success |
| D Human-in-the-loop | 12 | **Pass**, with one unexpected hazard (below) | Pause/resume across processes, approve ≠ reject, finalized run closed, one effect despite a replayed finalize |
| E Bounded execution | 18 | **Pass**, with one documented limit (`steps` has no second store) | Caps held across resume and through finalization (step cap checked for 8 values); operator counter reset caught by the ledger |
| F Deterministic replay | 4 | **Pass** | Equal digests across fresh runs and across processes; no network call attempted |
| Framework behaviors pinned | 4 | **Pass** | Interrupt replays the node; a raising task aborts the step; `Command(update)` is ordinary input; a start input wedges a paused thread |
| Docs consistency | 2 | **Pass** | Mermaid in `architecture.md` equals the generated graph; node list matches |
| Routing | 5 | **Pass** | Node order, retry loop back to `dispatch`, input schema, invalid requests |

## Commands and results

```bash
cd prototypes/langgraph-spike
pip install -r requirements.txt
python -m pytest
```

| Interpreter | Result |
|---|---|
| CPython 3.13.18 (local) | `81 passed` in 33–35 s (after review fixes; the previous 72-test version ran five times without failure once the two racy assertions were fixed) |
| CPython 3.11 (local, clean venv from `requirements.txt`) | `81 passed in 34.53s` |
| GitHub Actions matrix 3.11 / 3.13 | Reported in the pull request checks; not claimed here |

Environment of every multi-process test: `PATH`, `PYTHONPATH`, `HOME` only. No token, no API key, no network.

## Framework limitations discovered

1. **A raising parallel task aborts the whole superstep, and its siblings' results are not reliably kept.** Isolation is the node's job (`investigate` catches every `Exception`). In 300 trials of a 3-task fan-out with one raising task, both sibling results were persisted in 270, one in 29 and none in 1 (a race with the pending-write commits; the framework also logged `cannot schedule new futures after shutdown`). A hard kill (no exception) did not lose finished siblings in the kill tests, where they had finished 0.5 s earlier; a kill that races the commit would re-run them, not lose them.
2. **An interrupted node restarts from its first line.** Any side effect above `interrupt()` runs once per resume.
3. **A start input on an existing thread wedges a paused one.** `init_run` raises, but a pending `init_run` task replaces the pending interrupt (`phase = wedged`); the pause is only recoverable by forking from an earlier checkpoint (`get_state_history`). A guard inside the graph is not enough; the facade checks the phase before invoking.
4. **`Command(update=...)` and `update_state` are unguarded state writes.** A counter can be reset through ordinary `invoke`. Budget authority therefore lives in a ledger outside the state.
5. **`input_schema` hides fields from the type, not from the thread's existing state.** Extra start keys are dropped (tested), but identity fields needed `keep_first` to stay write-once.
6. **No built-in recovery after a hard kill.** The thread is simply left with pending tasks; something must call `invoke(None)` (`recover`). Nothing detects a killed run by itself.
7. **Fan-out ordering is not observable or controllable**; determinism comes from canonical sort in `aggregate`, not from the framework.
8. **Version coupling.** Items 1–4 are pinned by `tests/test_framework_behaviors.py` for `langgraph==1.2.19`; an upgrade can change them silently otherwise.
9. **Not measured:** the default `durability="async"` under a hard kill (the facade always uses `"sync"`); concurrent writers on one SQLite file from two processes; checkpoint growth over long runs; schema stability of the checkpoint across LangGraph versions (ADR-0005 §5 asks for "checkpoint schema stable across the spike": it was, because the version never changed; migration across versions was **not tested**).

## Unexpected behavior and workarounds

| Behavior | Workaround in the spike |
|---|---|
| Budget counters can be rewritten as input | Worker-side reservation in `ledger.sqlite`; digest check for frozen candidates |
| Replayed `finalize` node | Effect and idempotency key in one SQLite transaction (`INSERT OR IGNORE`) |
| Crash between the effect and the checkpoint | Same; test kills the process exactly there |
| Parallel workers cannot see siblings' spend in the state | Router counts the batch; the worker reserves against the ledger |
| `executescript` commits implicitly (found while writing the ledger) | Statements executed one by one inside a `BEGIN IMMEDIATE` transaction |
| Wedged thread | `wedged` phase in `status`, refusal in the facade, fork-from-history repair documented |

## Dependencies and operational cost

| Item | Measured |
|---|---|
| Runtime packages (`langgraph` + SQLite checkpointer, transitive) | 42 distributions, 87 MB in `site-packages` (CPython 3.13); `requirements.txt` adds `pytest` |
| Import cost | ≈ 1.1 s per process (`import langgraph.graph, langgraph.checkpoint.sqlite`) against 0.03 s for the standard library alone; every CLI command in the tests pays it |
| Spike code | ~820 lines of source (`graph.py` + `runner.py` ≈ 290 are LangGraph-specific; `ledger.py`, `agent.py`, `models.py` are plain Python), ~720 lines of tests |
| LLM, network, credentials | None |
| Transitive packages unrelated to this use | `langchain-core`, `langsmith`, `httpx`, `requests`, `websockets`, `langgraph-sdk` are pulled in; none is called by the spike |

## LangGraph vs a hand-written state machine on SQLite

The alternative (ADR-0005 §5 fallback) was **not implemented**; the table compares measured S0 behavior with what the fallback would have to build (**estimate**).

| Criterion | LangGraph (measured in S0) | Hand-written (estimate) |
|---|---|---|
| Complexity | ~290 lines of graph and facade, but 3 framework hazards to learn and pin; 140 lines of ledger still needed | A status column per candidate and run, a loop, a thread pool: probably 150–250 lines for the same shape; no framework hazards, all hazards ours |
| State management | Typed state with reducers; parallel writes merge safely; unreduced fields are single-writer by convention | Explicit rows; merges are SQL; reducers become transactions |
| Checkpoint semantics | Superstep checkpoints plus per-task pending writes (this is the valuable part: finished siblings survive a kill); history gives fork-based repair | Must write per-candidate result rows on completion; equivalent and simpler to reason about, but ours to get right |
| Failure recovery | Nothing automatic after a kill; `invoke(None)` continues; one raising task aborts the step | Same need for a recovery command; per-candidate try/except is natural |
| Human interrupts | `interrupt()` + `Command(resume)` with typed values and re-interrupt on invalid answers; node replays from the top | A `awaiting_approval` status and a resume command; no replay semantics to misuse |
| Parallel execution | `Send` fan-out with correct association (tested); concurrency and order uncontrolled | `ThreadPoolExecutor` / asyncio over pending rows; order controllable |
| Testability | Excellent once a facade exists: graph, subprocess and framework-behavior tests all ran offline | Equal or better: plain functions over a SQLite file |
| Operational dependencies | 42 packages, 87 MB, ~1.1 s import, upgrade-sensitive behaviors | `sqlite3` from the standard library |
| Lock-in | State schema, interrupt protocol and checkpoint format are LangGraph's; the core stays free (`agent.py`/`models.py` do not import it) | None |

**What LangGraph provided that S0 would otherwise have written:** per-task pending writes after a kill, interrupt/resume plumbing, conditional routing, checkpoint history, a generated diagram. **What it did not provide:** budget authority, effect idempotency, exception isolation, kill detection, input-surface safety. These were written in the spike (`ledger.py`, the worker's `try/except`, `runner.py`, the digest checks) and are framework-independent.

## Recommendation (for the owner to decide; ADR-0005 stays Proposed)

**Adopt LangGraph for M3 with explicit constraints.** The two things that are costly to hand-write correctly — durable fan-out with per-task recovery and interrupt/resume across processes — were demonstrated; none of the failures found is disqualifying, and each has a code-level mitigation that is independent of LangGraph. The hand-written alternative is credible, not rejected: it is the fallback if any constraint below proves unworkable.

Constraints:

1. **One facade is the only entry point** (`start`, `recover`, `resume`, `status`). It checks the run phase *before* touching the graph and never forwards `Command(update|goto)`, `update_state` or history forks. Those are operator tools, documented as such.
2. **Budget authority and effect idempotency live outside the checkpoint** (ledger or run store, ADR-0008), reserved at the worker and committed atomically with their keys.
3. **No side effect before `interrupt()`**; the effect sits in the next node and is idempotent.
4. **Catch inside every worker**; treat a kill as a normal event handled by `recover`.
5. **`durability="sync"`**, and measure the default before ever relaxing it.
6. **Pin the dependency set and keep `tests/test_framework_behaviors.py`** (and the kill tests) as an upgrade gate.
7. **Deterministic core stays framework-free** (ADR-0005 §1, §3).

Not decided by S0: whether the Investigator's tool loop is a subgraph or one node (no LLM here); whether insufficient-evidence candidates interrupt (S0 batches them, as ADR-0005 proposes).

## Remaining architectural risks

| Risk | State |
|---|---|
| Checkpoint schema stability across LangGraph versions | **Not tested**; pin and test an upgrade with a populated checkpoint database before M3 |
| Durability under the default `async` mode and multi-process access to one SQLite file | **Not measured** |
| External effects that cannot be keyed (no write exists before ADR-0007 level 2) | Modeled with a keyed ledger only |
| Operator access to `checkpoints.sqlite` bypasses every guard | By design; needs an operator runbook at M3 |
| `steps` counter has no second store | Open; resolve by counting ledger log rows if the step cap must survive operator edits |
| Scripted agent hides real model nondeterminism, latency and malformed-output rates | S0 proves the runtime, not the Investigator; M3 measures it |
| Snapshots are scenario code, not stored artifacts | M3 must store them content-addressed and verify them on resume |
| Pinned versions resolved on 3.13, verified on 3.11 and 3.13 locally | Matrix runs in CI |
