"""Pins the LangGraph behaviours the M3 workflow relies on (S0 results, constraint 6): an upgrade that changes one fails
here instead of silently changing the workflow."""

from __future__ import annotations

import operator
from importlib.metadata import version
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")

import tomllib

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from repo_orchestrator.investigation.pipeline import open_orchestrator
from repo_orchestrator.investigation.runner import InvalidPhase
from repo_orchestrator.investigation.store import InvestigationStore
from support import ROOT, baseline_revisions, load_decl
from support_inv import Scripted, baseline_run, h2_script


class S(TypedDict, total=False):
    items: Annotated[list[int], operator.add]


def test_installed_versions_equal_the_pins_of_the_investigation_extra():
    extra = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["investigation"]
    for pin in extra:
        name, _, wanted = pin.partition("==")
        assert version(name) == wanted, f"{name}: installed {version(name)}, pinned {wanted}"


def test_an_interrupted_node_restarts_from_its_first_line_so_nothing_may_precede_interrupt():
    runs = []

    def node(state: S):
        runs.append("enter")  # a side effect above interrupt() would repeat on resume
        answer = interrupt("question")
        return {"items": [answer]}

    g = StateGraph(S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke({"items": []}, cfg)
    graph.invoke(Command(resume=7), cfg)
    assert runs == ["enter", "enter"] and graph.get_state(cfg).values["items"] == [7]


def test_a_raising_parallel_task_aborts_the_superstep_so_every_worker_must_catch_its_own_exceptions():
    def fan(state: S):
        return [Send("w", {"n": 1}), Send("w", {"n": 2})]

    def worker(item: dict):
        if item["n"] == 2:
            raise RuntimeError("boom")
        return {"items": [item["n"]]}

    g = StateGraph(S)
    g.add_node("start", lambda s: {})
    g.add_node("w", worker)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", fan, ["w"])
    g.add_edge("w", END)
    with pytest.raises(RuntimeError, match="boom"):
        g.compile(checkpointer=InMemorySaver()).invoke({"items": []}, {"configurable": {"thread_id": "t"}})


def test_a_start_input_on_a_paused_thread_wedges_it_and_the_facade_refuses_to_forward_one(tmp_path):
    inv, _ = baseline_run(tmp_path, Scripted(h2_script()))
    store = InvestigationStore(tmp_path / "state.sqlite")
    orch = open_orchestrator(store, _opts(tmp_path))
    try:
        assert orch.status(inv.run_id)["phase"] == "awaiting_approval"
        assert orch.advance(inv.run_id)["phase"] == "awaiting_approval", (
            "the facade checks the phase first and does not forward a start"
        )
        # what the facade protects against: a raw start input on the paused thread
        with pytest.raises(Exception, match="already initialized"):
            orch._wf.start(inv.run_id)
        assert orch.status(inv.run_id)["phase"] == "wedged"
        with pytest.raises(InvalidPhase, match="wedged"):
            orch.advance(inv.run_id)
    finally:
        orch.close()


def _opts(tmp):
    from repo_orchestrator.investigation.pipeline import InvestigationOptions
    from support import DECLARATIONS, FIXTURES

    d = load_decl()
    return InvestigationOptions(
        DECLARATIONS,
        FIXTURES,
        tmp / "state.sqlite",
        tmp / "checkpoints.sqlite",
        None,
        None,
        d.digest,
        baseline_revisions(d),
        True,
    )
