# ADR-0015: M3 LLM boundary, code-constant budgets, and recorded-response replay

- **Date:** 2026-11-04
- **Status:** Accepted
- **Accepted:** 2026-11-04, by the repository owner (implemented in M3, PR #9)

## Context

[ADR-0004](0004-deterministic-checks-before-agentic-reasoning.md) allows one bounded LLM role (the Investigator, C5) and [ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md) §6 requires Investigator stages to be replayable from recorded responses. M3 is the first code that calls a model. M1 and M2 promised "no LLM call"; that promise must become a precise, testable boundary instead of being dropped. At the M3 gate the owner chose: Anthropic as provider, the API key from the environment, no SDK dependency, and a hard per-run budget.

## Decision

1. **One component calls an LLM: C5.** The code lives in `src/repo_orchestrator/investigation/`. Only `investigation/provider.py` may import `urllib.request` for a model provider; only `investigation/workflow.py` may import `langgraph` ([ADR-0017](0017-m3-investigation-state-and-langgraph-confinement.md)). Everything else, including every M1 and M2 module, stays LLM-free; `tests/test_architecture_constraints.py` states exactly who may import what.
2. **Provider.** Anthropic Messages API over HTTPS with the standard library (no SDK: no new runtime dependency; the SDK is the documented alternative if retries and typing outweigh the dependency). Model `claude-sonnet-5-5`, `output_config.effort` fixed in code, no `temperature`/`top_p`/`top_k` (rejected by 5.x models), no tool-use blocks: the tool loop is a JSON text protocol, so the provider interface is `complete(request) -> response` and replay is trivial.
3. **Credentials.** `ANTHROPIC_API_KEY`, read once when the provider is constructed, sent only in the `x-api-key` header. It is never logged, never written to the store, a recording, a fixture or a report (a test greps all of them for the key and for the header name). `ANTHROPIC_BASE_URL` is honoured so a proxy can be used. No key and no replay file: `--investigate` stops with exit 3 and never silently skips.
4. **Budgets are code constants** in `investigation/budgets.py` ([ADR-0004](0004-deterministic-checks-before-agentic-reasoning.md) §5), never inputs, never prompt content: 12 investigations per run; 8 tool calls, 6 model calls (one malformed-output retry included), 24 000 input and 4 000 output tokens per investigation; 150 000 tokens and 2.00 USD per run. Cost is `tokens × list price` from a dated constant table (Sonnet 5.5: 2.00 / 10.00 USD per million input / output tokens, source: the Claude API model table cached 2026-10-06; to be re-verified). Tokens are the primary cap; the dollar cap is derived from the same table.
5. **Authority outside the graph state.** A SQLite ledger reserves every spend (investigation slot, model call, tool call, tokens) atomically before the spend happens; the state checkpoint can be rewritten, the ledger cannot be bypassed by an input.
6. **Recording and replay.** Every model call is keyed by `sha256(canonical{provider, model, parameters, system, messages})` and its response (text, usage, model id, latency) is stored. A replay provider answers from a recording and fails closed on a missing key (`replay_miss`): no network, no key. Repository text enters the request through the tool results, which derive from pinned snapshots, so an unchanged input reproduces the same keys. The semantic report carries `recorded_digest` (digest of the sorted (request key, response text hash) pairs actually used) and the report digest covers it; tokens, cost and latency are in the execution part only.
7. **Failure handling** ([workflows.md](../workflows.md#failure-handling)): per-candidate isolation (a provider error, timeout, malformed output or budget refusal ends that candidate in `insufficient_evidence` with a cause code and never touches deterministic findings); malformed output gets one retry; a refusal (`stop_reason: refusal`) is a malformed answer; nothing is silently dropped.

## Rationale

- A text protocol and a one-method provider make recording, replay and scripted test providers exact and keep the live surface small.
- Constants in code, enforced at the worker, are the lesson of `autonomous-dev-loop` ADR-0039 and of S0 experiment E (state edits can reset counters).
- Reports must not contain the key, so the key lives in one constructor argument and one header.

## Alternatives considered

- **Anthropic SDK.** Kept as the alternative: better retries and typing, but a new runtime dependency (with `httpx2`) for one POST. Rejected by the owner at the gate.
- **Native tool-use blocks.** Rejected: ties the request key and replay to a provider-specific block format, and forced tool choice is rejected by 5.x models.
- **Budgets in configuration.** Rejected: a configurable budget is an input an attacker or a mistake can raise.
- **Live calls in CI.** Rejected: non-reproducible and costly; CI replays recordings and the live check is a manual `workflow_dispatch` step.

## Consequences

- ✅ M1/M2 code paths and tests are unchanged and provably LLM-free.
- ✅ A run is reproducible offline bit-for-bit; cost per run is reported.
- ⚠️ A recording is only valid for the exact prompts of the code version that produced it; changing a prompt invalidates recordings (they are regenerated, not edited).
- ⚠️ The price table can be stale; the report labels cost as an estimate from list prices.
- ⚠️ Recordings quote repository content; for private repositories they inherit its confidentiality.

## Open questions

- Retention of recordings ([ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md) open question).
- Whether the real model's malformed-output and `insufficient_evidence` rates justify other budgets (not measured: no live call was possible when M3 was built).
