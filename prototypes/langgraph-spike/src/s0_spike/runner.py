"""Application facade: the only normal entry point. Exposes start / recover / resume / status, nothing else.

Deliberately absent: update_state, Command(update=...), Command(goto=...), time travel. Those are operator-level
LangGraph APIs (see docs/architecture.md) and can override any state, counters included.
"""
import re
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .errors import (InvalidRunRequest, NotAwaitingApproval, RunAlreadyFinalized, RunExists, RunNotFound)
from .graph import RUN_ID_RE, build_graph
from .ledger import Ledger
from .limits import DEFAULT_LIMITS
from .models import TERMINAL_STATUSES


class Orchestrator:
    def __init__(self, state_dir, limits=DEFAULT_LIMITS):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits
        self.conn = sqlite3.connect(self.dir / "checkpoints.sqlite", check_same_thread=False)
        self.ledger = Ledger(self.dir / "ledger.sqlite")
        self.graph = build_graph(SqliteSaver(self.conn), self.ledger, limits)

    def close(self):
        self.conn.close()

    def _cfg(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise InvalidRunRequest("run_id invalid")
        return {"configurable": {"thread_id": run_id}, "recursion_limit": self.limits.recursion_limit}

    def _run(self, inp, run_id):
        # durability="sync": each checkpoint is committed before the next step starts. The default ("async")
        # may lose the last checkpoint on a hard kill.
        self.graph.invoke(inp, self._cfg(run_id), durability="sync")
        return self.status(run_id)

    def status(self, run_id):
        snap = self.graph.get_state(self._cfg(run_id))
        if not snap.values:
            return {"run_id": run_id, "phase": "not_found"}
        v = snap.values
        paused = any(t.interrupts for t in snap.tasks)
        phase = v.get("status") if v.get("status") in TERMINAL_STATUSES else (
            "awaiting_approval" if paused else ("incomplete" if snap.next else v.get("status")))
        if phase == "incomplete" and snap.next == ("init_run",):
            phase = "wedged"  # a start input hit an existing thread: see docs/architecture.md, "Operator-level APIs"
        out = {"run_id": run_id, "thread_id": run_id, "phase": phase, "next": list(snap.next), "spent": v.get("spent"),
               "rounds": v.get("rounds"), "steps": v.get("steps"), "meta": v.get("meta"),
               "results": sorted(v.get("results", {})), "report": v.get("report"), "outcome": v.get("outcome")}
        if paused:
            out["pending_report_digest"] = v["report"]["digest"]
        return out

    def start(self, run_id, scenario):
        st = self.status(run_id)
        if st["phase"] in TERMINAL_STATUSES:
            raise RunAlreadyFinalized(f"{run_id} is {st['phase']}")
        if st["phase"] != "not_found":
            raise RunExists(f"{run_id} exists in phase {st['phase']}; use recover or resume")
        return self._run({"run_id": run_id, "scenario": scenario}, run_id)

    def recover(self, run_id):
        """Continue an incomplete run (e.g. after a crash) from its checkpoint. Takes no input."""
        st = self.status(run_id)
        if st["phase"] == "not_found":
            raise RunNotFound(run_id)
        if st["phase"] in TERMINAL_STATUSES:
            raise RunAlreadyFinalized(f"{run_id} is {st['phase']}")
        if st["phase"] != "incomplete":
            raise NotAwaitingApproval(f"{run_id} is {st['phase']}, nothing to recover")
        return self._run(None, run_id)

    def resume(self, run_id, decision, reviewer, report_digest, note=None):
        st = self.status(run_id)
        if st["phase"] == "not_found":
            raise RunNotFound(run_id)
        if st["phase"] in TERMINAL_STATUSES:
            raise RunAlreadyFinalized(f"{run_id} is {st['phase']}")
        if st["phase"] != "awaiting_approval":
            raise NotAwaitingApproval(f"{run_id} is {st['phase']}")
        answer = {"decision": decision, "reviewer": reviewer, "report_digest": report_digest, "note": note}
        return self._run(Command(resume=answer), run_id)
