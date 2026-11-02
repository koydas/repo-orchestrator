"""LangGraph behaviors the design must account for. These pin OBSERVED behavior of the pinned version
(requirements.txt); if an upgrade changes one, the test fails and the docs must be re-read."""
import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from s0_spike.errors import NotAwaitingApproval, RunAlreadyInitialized

from conftest import approve


def test_interrupted_node_restarts_from_its_first_line():
    class S(TypedDict, total=False):
        n: Annotated[int, operator.add]
    calls = []

    def gate(s):
        calls.append("before")  # a side effect placed here would run twice
        interrupt("approve?")
        calls.append("after")
        return {"n": 1}

    b = StateGraph(S)
    b.add_node("gate", gate); b.add_edge(START, "gate"); b.add_edge("gate", END)
    g, cfg = b.compile(checkpointer=InMemorySaver()), {"configurable": {"thread_id": "t"}}
    g.invoke({"n": 0}, cfg)
    g.invoke(Command(resume=True), cfg)
    assert calls == ["before", "before", "after"]


def test_raising_parallel_task_aborts_the_superstep():
    class S(TypedDict, total=False):
        r: Annotated[list, operator.add]

    def worker(p):
        if p["i"] == 1:
            raise RuntimeError("boom")
        return {"r": [p["i"]]}

    b = StateGraph(S)
    b.add_node("start", lambda s: {}); b.add_node("w", worker)
    b.add_edge(START, "start")
    b.add_conditional_edges("start", lambda s: [Send("w", {"i": i}) for i in range(3)], ["w"])
    b.add_edge("w", END)
    g, cfg = b.compile(checkpointer=InMemorySaver()), {"configurable": {"thread_id": "t"}}
    with pytest.raises(RuntimeError):
        g.invoke({"r": []}, cfg)
    s = g.get_state(cfg)
    assert "w" in s.next  # the step did not complete: isolation must be done inside the node (graph.py: investigate)
    # Siblings' results are NOT reliably kept: over 300 trials the persisted siblings were {0, 2} in 270, one of them
    # in 29 and neither in 1 (a race between the failing task and the other tasks' pending-write commits).
    assert set(s.values.get("r", [])) <= {0, 2}


def test_command_update_is_accepted_as_ordinary_input_and_resets_counters(orch):
    orch.start("r1", "all_fail")
    assert orch.status("r1")["spent"] == 15
    orch.graph.invoke(Command(update={"spent": 0, "rounds": 0}), orch._cfg("r1"))  # no error, no guard
    assert orch.status("r1")["spent"] == 0  # hence: Command(update/goto) must never be reachable from run input
    assert orch.ledger.spent("r1") == 15  # the ledger is the authoritative budget


def test_start_input_on_a_paused_thread_wedges_it_and_history_fork_repairs_it(orch):
    orch.start("r1", "small")
    with pytest.raises(RunAlreadyInitialized):
        orch.graph.invoke({"run_id": "r1", "scenario": "small"}, orch._cfg("r1"))  # the guard raised, yet:
    st = orch.status("r1")
    assert st["phase"] == "wedged" and st["next"] == ["init_run"]  # the pending interrupt was superseded
    with pytest.raises(NotAwaitingApproval):
        approve_digest = orch.graph.get_state(orch._cfg("r1")).values["report"]["digest"]
        orch.resume("r1", "approve", "x", approve_digest)
    # Operator repair: fork from the checkpoint taken before the approval gate.
    hist = list(orch.graph.get_state_history(orch._cfg("r1")))
    target = next(h for h in hist if h.next == ("approval_gate",))
    orch.graph.invoke(None, target.config)
    assert orch.status("r1")["phase"] == "awaiting_approval"
