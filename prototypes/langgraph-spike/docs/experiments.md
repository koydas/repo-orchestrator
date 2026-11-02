# Experiments

Six experiments, each with its automated test file. "Observed" is what the tests assert and what was seen when they ran (see [results](results.md) for commands and counts). Scenario names refer to `src/s0_spike/scenarios.py`.

Multi-process claims use one OS process per CLI command (`tests/conftest.py::cli`), with an environment reduced to `PATH`, `PYTHONPATH` and `HOME`.

## A. Durable checkpoints — `tests/test_exp_a_persistence.py`

**Hypothesis.** A SQLite checkpointer lets a run survive process death: a new process resumes the same thread, finished work is not redone, metadata and budgets are not reset.

**Method.** `baseline` is started in process 1, which exits at the approval gate; process 2 reads the status; process 3 resumes. Separately, `crash_mid_fanout` kills the process (`os._exit(137)`) inside one parallel investigation while its four siblings have finished; a new process runs `recover`.

**Expected.** Status identical across processes; approve works in a third process; each investigation executes once; after the kill only the killed task re-runs; the budget counts it once.

**Observed.** As expected. After the kill the checkpoint held the four sibling results and a pending `investigate` task. `recover` re-ran only `c03` (ledger: six agent calls, five distinct attempts), `spent` stayed 5, run metadata (nonce, start time) unchanged, no report existed until the run completed. The run needed no credentials or network.

**Failure cases covered.** Kill mid fan-out. Not covered: kill during a checkpoint write itself (the facade uses `durability="sync"`; the default was not measured), a corrupted SQLite file.

## B. Parallel investigation — `tests/test_exp_b_parallel.py`

**Hypothesis.** `Send` fan-out keeps results attached to their candidate, aggregates deterministically whatever the completion order, and one failure does not erase the others.

**Method.** `parallel` (6 candidates, `c01` slowest, `c03` always raising) and `parallel_rev` (completion order reversed) with fixed delays of 0.08 s steps. The ledger records start and completion order.

**Expected.** Six findings, one per candidate; `c03` failed, five verified; both runs yield the same normalized report and digest although completion order differs.

**Observed.** As expected. Completion order was not the canonical order in either run and differed between them; digests equal. 7 agent calls (6 + the retry of `c03`), 7 distinct `candidate#attempt` keys. `too_many` (12 candidates) returned 12 findings. `merge_results` unit tests: identical replay is idempotent, a conflicting duplicate raises `ReducerConflict`, inputs are not mutated.

**Failure cases covered.** A failing sibling; duplicate keys. Ordering relies on delays, not a barrier: a barrier would deadlock when tasks outnumber worker threads (not run, reasoned from the thread-pool model; delays are 80 ms apart).

## C. Failure isolation — `tests/test_exp_c_failures.py`, `tests/test_classification.py`

**Hypothesis.** Each injected fault yields a typed, non-successful result for that candidate only, with an explicit recovery rule.

**Recovery rules (asserted).**

| Fault | Candidate | Recovery | Final |
|---|---|---|---|
| Agent exception, transient | `c06` | Retried (attempt 2) | `verified` |
| Agent exception, persistent | `c05` | Retried up to `max_attempts` | `failed / agent_exception` |
| Malformed structured result | `c08` | Retried | `verified` at attempt 2; the failed attempt stays in `results` |
| Missing evidence | `c07` | Not retried | `insufficient_evidence / missing_evidence` |
| Fabricated quote + self-declared `verified` | `c04` | Not retried | `insufficient_evidence / unverified_quote` |
| Budget exhaustion (6 units > 5) | `c09` | Not retried | `insufficient_evidence / budget_exhausted` |
| Qualitative claim + self-declared `verified` | `c03` | — | `supported`, never `verified` |
| Process killed | `crash_mid_fanout` | `recover` | see A |

**Observed.** Counts on `baseline`: verified 4, supported 1, insufficient_evidence 3, failed 1 (9 findings). Every `verified`/`supported` finding has verified evidence and `attempt ≥ 1`; every `failed` has none. Eleven malformed-output shapes are rejected by `validate_output`; a wrong SHA or an altered quote is `unverified_quote`; a claim that contradicts the code-run check is `insufficient_evidence`.

**Not covered.** An agent that returns plausible quotes for the wrong claim on a qualitative candidate: `supported` is as strong as a quote check can be, which is why ADR-0006 keeps a separate validation status.

## D. Human-in-the-loop — `tests/test_exp_d_human.py`

**Hypothesis.** The approval boundary is durable, approve and reject differ, a finalized run is closed to normal calls, and an interrupted/re-executed node causes no duplicate irreversible effect.

**Method.** Pause in one process, answer in another; approve and reject on separate runs; invalid answers; every normal call on a finalized run plus a raw `graph.invoke` bypassing the facade; `crash_in_finalize` kills the process after the publication committed and before the checkpoint.

**Observed.**
- Paused state has no publication and no `publish_attempt`.
- Approve: status `approved`, publication with payload. Reject: `rejected`, publication row with no payload. Distinct phases and outcomes.
- A stale digest, an unknown decision, an empty reviewer, a non-object: the gate pauses again, nothing is published, a later valid answer works.
- `start`, `recover`, `resume` on a finalized run raise `RunAlreadyFinalized`; state and publication unchanged. A raw start input is refused by `init_run` and identity fields are unchanged (`keep_first`).
- Kill in `finalize_approved`: `publish_attempt` ×2, `publish_performed` ×1.
- LangGraph reruns an interrupted node from its first line (pinned in `test_framework_behaviors.py`); the design places no side effect before `interrupt()`.

**Unexpected.** A raw start input on a **paused** thread is refused by `init_run` but leaves a pending `init_run` task: the interrupt is superseded and the thread is wedged until an operator forks from an earlier checkpoint. The facade checks the phase first. The graph-level guard alone is not sufficient.

## E. Bounded execution — `tests/test_exp_e_limits.py`

**Hypothesis.** Caps enforced in code hold across resume, input and counter resets.

**Observed.**

| Cap | Scenario | Result |
|---|---|---|
| `max_candidates` 10 | `too_many` (12) | 10 investigated, `c11`/`c12` `insufficient_evidence/candidate_cap`, attempt 0, listed in the report |
| `max_attempts` 2, `max_investigations` 15 | `all_fail` (10 failing) | Round 1: 10 spends; round 2: 5 retries; `c06`–`c10` denied (`investigation_cap`); ledger spent 15; all 10 still `failed`, none successful |
| `max_attempts` 1 | `baseline` | 9 spends, no retry |
| `max_steps` 8–24 | `baseline` | Total steps **after approval and finalization** ≤ `max_steps` for every value tested (an earlier reserve of 4 overshot by 1–2: found by review, fixed). At 16: 14 steps paused, 16 final, `c09` never investigated (`step_cap`, attempt 0), retries of `c05`/`c06`/`c08` denied, 9 findings. `max_steps` < 8 raises `ValueError` |
| Resume cannot buy budget | `all_fail` | `start` on the thread → `RunExists`; after approval `spent` still 15; extra keys (`spent`, `max_investigations`, `limits`) in a start input are dropped |
| Operator `update_state({"spent": 0})` | paused before retry dispatch | The router dispatches again; the **ledger** reserves the third spend and denies the fourth (`investigation_cap` at the worker): ledger spent 3 = cap |
| Operator edit of `candidates` | same | `FrozenInputTampered` at `dispatch` |

**Limitation.** The step counter lives only in the checkpoint: an operator reset of `steps` is not caught by a second store. `Command(update=...)` is accepted as ordinary invoke input (observed), so the boundary is "the facade never forwards it", not a framework guarantee.

## F. Deterministic replay — `tests/test_exp_f_replay.py`

**Hypothesis.** Fixed inputs and scripted responses give semantically identical results, independent of any external service.

**Method.** Three fresh `baseline` runs in separate state directories; one CLI run in another process; a run with `socket.connect` and `getaddrinfo` patched to raise.

**Observed.** Identical digests and normalized reports; per-candidate outcome, reason and attempt identical. `meta.nonce` and `meta.started_at` differ in every run and are excluded by `normalize_report` (which also drops `run_id`). The CLI process produces the same digest as the in-process run. No network call attempted.

## Framework behaviors pinned — `tests/test_framework_behaviors.py`

| Behavior | Consequence for the design |
|---|---|
| An interrupted node restarts from its first line | No side effect before `interrupt()`; effect in the next node, idempotent |
| A raising parallel task aborts the superstep; finished siblings' writes are racy (270/300 kept both) | Catch inside the worker |
| `Command(update=...)` is accepted as input | Never expose it; budget authority outside the state |
| A start input on a paused thread wedges it; forking from history repairs it | Facade checks the phase first; operator runbook |
