# ADR-0016: M3 investigation state: LangGraph confined to one module, checkpoints, ledger and a separate decision namespace

- **Date:** 2026-11-04
- **Status:** Proposed

## Context

[ADR-0005](0005-langgraph-for-investigation-workflows-only.md) proposes LangGraph for the investigation workflow; the S0 spike ([results](../../prototypes/langgraph-spike/docs/results.md)) recommends adopting it with seven constraints. At the M3 gate the owner chose "LangGraph, confined". ADR-0005 itself stays Proposed; this record states what is built.

## Decision

1. **LangGraph is imported by exactly one module**, `investigation/workflow.py`, plus its facade `investigation/runner.py` through that module. The deterministic core, C5, the verification code and the CLI's M1/M2 paths do not import it; the `--investigate` CLI path imports it lazily so the default CLI never pays its import. Removing LangGraph must leave everything else intact.
2. **The production graph is new code reusing the S0 design**, not S0 files, and nothing under `prototypes/` is imported. The S0 constraints are applied: (a) one facade (`start`, `resume`, `recover`, `status`, `decide`) is the only entry point and never forwards `Command(update|goto)` or `update_state`; (b) budget authority and effect idempotency live in the ledger/store, not in the checkpoint; (c) no side effect before `interrupt()`; (d) every worker catches every exception; (e) `durability="sync"`; (f) dependencies pinned in an `investigation` extra and `tests/test_workflow_framework.py` pins the framework behaviours relied on; (g) the core stays framework-free.
3. **Graph.** `init_run → freeze → dispatch → (Send) investigate → collect → … → aggregate → triage[interrupt] → approve[interrupt] → finalize`. Fan-out over candidates, capped by `MAX_INVESTIGATIONS`; one retry loop is inside C5, not in the graph. Triage interrupts only for `breaks` claims in `needs_validation`; approval always interrupts and records the run as reviewed. A run is therefore resumable and never publishes anything outside local files and the store.
4. **Snapshots are content-addressed and verified on resume.** The run records `{repo: sha}` and, per candidate, the `(repo, path, blob id, content sha256)` of its evidence; resume rebuilds the snapshot offline from the cache and fails closed (`snapshot_changed`) if any hash differs. A resumed run never re-resolves a ref and never repeats a finished investigation.
5. **Storage.** Checkpoints are LangGraph's SQLite saver in their own file (`investigations.sqlite` next to the state DB); the ledger, recorded responses, results and human decisions are `inv_*` tables in the run store file. `findings`, `finding_events` and `pr_*` are neither read nor written by M3; a test compares their schema and contents before and after an investigation.
6. **Scope.** `scan --investigate` only. `analyze-pr` is unchanged (owner decision d): extending W2 needs a verdict delta by claim fingerprint and a base-side investigation, both left to a later ADR.
7. **CLI.** `scan --investigate` (with `--replay FILE` offline or `--record FILE`), plus `investigations list|show|resume|decide|approve`. The default `scan` is byte-identical to M1.

## Rationale

- A confined module keeps the lock-in and the 87 MB / ~1 s import cost off every path that does not need it.
- Putting authority in SQLite outside the checkpoint closes the counter-reset and double-effect hazards S0 measured.

## Alternatives considered

- **Hand-written resumable state machine over SQLite** (ADR-0005 fallback). Credible and dependency-free; not chosen by the owner. It stays the fallback if a pinned framework behaviour changes.
- **LangGraph in the core pipeline.** Rejected as in ADR-0005.
- **Decisions in `findings`.** Rejected: M3 states are not the deterministic lifecycle and must not alter fingerprint history.

## Consequences

- ✅ Durable fan-out and interrupts without writing them by hand.
- ⚠️ A pinned optional dependency set (`investigation` extra) with upgrade-sensitive behaviour; checkpoint-schema stability across LangGraph versions is not tested.
- ⚠️ Operator access to the SQLite files bypasses every guard (documented in the M3 docs).

## Open questions

- Should insufficient-evidence candidates ever interrupt (currently batched into the report)?
- Checkpoint retention and cleanup.
