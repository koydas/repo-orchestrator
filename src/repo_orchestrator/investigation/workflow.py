"""The W3 workflow as a LangGraph graph — the ONLY module that imports LangGraph (ADR-0017 §1).

Design from the S0 spike (reused, not its files): nodes call plain functions; budget authority and the effect live in the
store, never in the checkpoint; no side effect above ``interrupt()`` (a resumed node restarts from its first line); every
worker catches every exception; ``durability="sync"``; one facade (``runner.Orchestrator``) is the only caller and never
forwards ``Command(update|goto)`` or ``update_state``.

    init_run → dispatch ─Send→ investigate ─→ aggregate → triage[interrupt] → approve[interrupt] → finalize
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from ..models import digest, to_jsonable
from .budgets import Budget
from .decisions import pending_triage
from .domain import Candidate, ICause, Result, candidate_from_json
from .store import InvestigationStore
from .verify import failure_result

RUN_ID_RE = re.compile(r"^[a-f0-9]{16}$")
RECURSION_LIMIT = 60


class WorkflowError(Exception):
    """A request the workflow refuses (bad run id, run already holds state)."""


def _merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


def _keep_first(a: str | None, b: str | None) -> str:
    return a or b or ""


class RunInput(TypedDict):
    run_id: str  # the only start input: candidates, budgets and outcome are never settable through it


class RunState(TypedDict, total=False):
    run_id: Annotated[str, _keep_first]
    phase: str
    input_digest: str
    candidate_keys: list[str]
    batch: list[dict[str, str]]
    results: Annotated[dict[str, Any], _merge]
    triage_pending: list[str]
    approval: dict[str, Any]


@dataclass
class Deps:
    store: InvestigationStore
    investigate: Callable[[Candidate], Result]
    budget: Budget


def result_json(r: Result) -> dict[str, Any]:
    out = to_jsonable(r)
    assert isinstance(out, dict)
    return out


def build_graph(checkpointer: Any, deps: Deps) -> Any:
    store = deps.store

    def _candidates(run_id: str) -> list[dict[str, Any]]:
        run = store.run(run_id)
        if run is None:
            raise WorkflowError(f"unknown run {run_id}")
        return list(run["candidates"])

    def init_run(state: RunState) -> dict[str, Any]:
        if state.get("phase"):
            raise WorkflowError(f"run {state.get('run_id')} already initialized (phase {state['phase']})")
        run_id = str(state.get("run_id", ""))
        if not RUN_ID_RE.match(run_id):
            raise WorkflowError("run_id is missing or invalid")
        run = store.run(run_id)
        if run is None:
            raise WorkflowError(f"unknown run {run_id}")
        cands = run["candidates"]
        if digest(cands) != run["config"].get("candidates_digest"):
            raise WorkflowError(f"run {run_id}: the frozen candidates were modified")
        store.set_status(run_id, "running")
        return {"phase": "running", "input_digest": run["inputs_digest"], "candidate_keys": [c["key"] for c in cands]}

    def dispatch(state: RunState) -> dict[str, Any]:
        run_id = state["run_id"]
        if digest(_candidates(run_id)) != (store.run(run_id) or {}).get("config", {}).get("candidates_digest"):
            raise WorkflowError(f"run {run_id}: the frozen candidates were modified")
        done = store.results(run_id)
        used = store.usage(run_id)["investigations"]
        batch: list[dict[str, str]] = []
        for c in _candidates(run_id):
            if c["key"] in done or c.get("prefilter_failure"):
                continue
            if used + len(batch) >= deps.budget.max_investigations:
                break  # first check, at routing; the authoritative one is the ledger in the worker
            batch.append({"run_id": run_id, "key": c["key"]})
        return {"batch": batch, "phase": "investigating"}

    def fan_out(state: RunState) -> Any:
        return [Send("investigate", item) for item in state.get("batch", [])] or "aggregate"

    def investigate(item: dict[str, str]) -> dict[str, Any]:
        run_id, key = item["run_id"], item["key"]
        cand = candidate_from_json(next(c for c in _candidates(run_id) if c["key"] == key))
        if store.reserve_slot(run_id, key) == "denied":  # second, authoritative budget check
            res = failure_result(cand, ICause.BUDGET, "budget exhausted (investigations)")
        else:
            store.log(run_id, "investigate", key)
            try:
                res = deps.investigate(cand)
            except Exception as exc:  # isolation is ours: a raising parallel task would abort the whole superstep
                res = failure_result(cand, ICause.INTERNAL, f"{type(exc).__name__} during the investigation")
        store.put_result(run_id, key, result_json(res))
        return {"results": {key: {"state": res.state.value, "cause": res.cause}}}

    def aggregate(state: RunState) -> dict[str, Any]:
        run_id = state["run_id"]
        done = store.results(run_id)
        for c in _candidates(run_id):
            if c["key"] in done:
                continue
            cand = candidate_from_json(c)
            if c.get("prefilter_failure"):
                res = failure_result(cand, c["prefilter_failure"][0], c["prefilter_failure"][1])
            else:  # not investigated: the budget (or a routing cut) left it; never silently dropped
                res = failure_result(cand, ICause.BUDGET, "not investigated: the investigation budget is exhausted")
            store.put_result(run_id, c["key"], result_json(res))
        store.set_status(run_id, "investigated")
        return {"phase": "aggregated", "triage_pending": pending_triage(store, run_id)}

    def triage(state: RunState) -> dict[str, Any]:
        run_id = state["run_id"]
        remaining = pending_triage(store, run_id)  # no side effect before interrupt(): a resumed node restarts here
        while remaining:
            store.set_status(run_id, "awaiting_triage")
            interrupt({"run_id": run_id, "awaiting": "triage", "candidates": remaining})
            remaining = pending_triage(store, run_id)  # decisions were recorded by the facade before the resume
        return {"phase": "triaged", "triage_pending": []}

    def approve(state: RunState) -> dict[str, Any]:
        run_id = state["run_id"]
        store.set_status(run_id, "awaiting_approval")  # idempotent bookkeeping, no external effect
        payload: dict[str, Any] = {"run_id": run_id, "awaiting": "approval"}
        while True:
            answer = interrupt(payload)
            if (
                isinstance(answer, dict)
                and answer.get("decision") == "approve"
                and isinstance(answer.get("reviewer"), str)
                and answer["reviewer"].strip()
            ):
                return {"approval": {"reviewer": answer["reviewer"].strip(), "note": answer.get("note")}}
            payload = {**payload, "error": "an approval needs decision='approve' and a reviewer"}

    def finalize(state: RunState) -> dict[str, Any]:
        a = state["approval"]
        store.mark_reviewed(state["run_id"], a["reviewer"], a.get("note"))  # idempotent: the first approval wins
        return {"phase": "reviewed"}

    g = StateGraph(RunState, input_schema=RunInput)
    g.add_node("init_run", init_run)
    g.add_node("dispatch", dispatch)
    g.add_node("investigate", investigate)  # type: ignore[arg-type]  # a Send payload, not the graph state
    g.add_node("aggregate", aggregate)
    g.add_node("triage", triage)
    g.add_node("approve", approve)
    g.add_node("finalize", finalize)
    g.add_edge(START, "init_run")
    g.add_edge("init_run", "dispatch")
    g.add_conditional_edges("dispatch", fan_out, ["investigate", "aggregate"])
    g.add_edge("investigate", "aggregate")
    g.add_edge("aggregate", "triage")
    g.add_edge("triage", "approve")
    g.add_edge("approve", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


class Workflow:
    """Graph + SQLite checkpointer behind five methods; the facade (runner.Orchestrator) is its only caller."""

    def __init__(self, checkpoint_path: Path, deps: Deps) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self._graph = build_graph(SqliteSaver(self._conn), deps)

    def close(self) -> None:
        self._conn.close()

    def _cfg(self, run_id: str) -> dict[str, Any]:
        if not RUN_ID_RE.match(run_id):
            raise WorkflowError("run_id is invalid")
        return {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}

    def _run(self, inp: Any, run_id: str) -> None:
        # durability="sync": each checkpoint is committed before the next step starts (S0 constraint 5).
        self._graph.invoke(inp, self._cfg(run_id), durability="sync")

    def status(self, run_id: str) -> dict[str, Any]:
        snap = self._graph.get_state(self._cfg(run_id))
        if not snap.values:
            return {"phase": "not_found"}
        v = snap.values
        paused = any(t.interrupts for t in snap.tasks)
        nxt = list(snap.next)
        if v.get("phase") == "reviewed":
            phase = "reviewed"
        elif paused:
            phase = "awaiting_triage" if nxt == ["triage"] else "awaiting_approval"
        elif nxt == ["init_run"]:
            phase = "wedged"
        else:
            phase = "incomplete" if nxt else str(v.get("phase"))
        return {"phase": phase, "next": nxt, "results": sorted(v.get("results", {}))}

    def start(self, run_id: str) -> None:
        self._run({"run_id": run_id}, run_id)

    def recover(self, run_id: str) -> None:
        self._run(None, run_id)

    def resume(self, run_id: str, value: Any) -> None:
        self._run(Command(resume=value), run_id)
