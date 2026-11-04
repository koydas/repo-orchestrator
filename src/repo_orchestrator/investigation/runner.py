"""Facade over the W3 workflow: the only normal entry point (S0 constraint 1). ``advance`` / ``resume_triage`` /
``approve`` / ``status`` — nothing else. It checks the phase BEFORE touching the graph, so a start input can never wedge a
paused run, and it never forwards ``Command(update|goto)`` or ``update_state`` (operator-level LangGraph APIs)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .budgets import BUDGET, Budget
from .domain import Candidate, Result
from .store import InvestigationStore
from .workflow import Deps, Workflow, WorkflowError

TERMINAL = frozenset({"reviewed"})
PAUSED = frozenset({"awaiting_triage", "awaiting_approval"})


class InvalidPhase(WorkflowError):
    """The requested transition is not allowed in the run's current phase."""


class Orchestrator:
    def __init__(
        self,
        store: InvestigationStore,
        checkpoint_path: Path,
        investigate: Callable[[Candidate], Result],
        budget: Budget = BUDGET,
    ) -> None:
        self.store = store
        self._wf = Workflow(checkpoint_path, Deps(store, investigate, budget))

    def close(self) -> None:
        self._wf.close()

    def status(self, run_id: str) -> dict[str, Any]:
        if self.store.run(run_id) is None:
            return {"phase": "unknown_run", "run_id": run_id}
        st = self._wf.status(run_id)
        if st["phase"] == "not_found":
            st = {**st, "phase": "not_started"}
        return {"run_id": run_id, **st}

    def advance(self, run_id: str) -> dict[str, Any]:
        """Start a new run, recover an interrupted one, leave a paused or finished one alone. Idempotent."""
        st = self.status(run_id)
        phase = st["phase"]
        if phase == "unknown_run":
            raise InvalidPhase(f"unknown run {run_id}: it must be created in the store first")
        if phase == "wedged":
            raise InvalidPhase(
                f"run {run_id} is wedged (a start input hit a paused thread); see docs/m3/README.md → operator notes"
            )
        if phase in TERMINAL or phase in PAUSED:
            return st
        if phase == "not_started":
            self._wf.start(run_id)
        else:
            self._wf.recover(run_id)
        return self.status(run_id)

    def resume_triage(self, run_id: str) -> dict[str, Any]:
        """After the decisions were recorded in the store: continue past the triage pause."""
        st = self.status(run_id)
        if st["phase"] != "awaiting_triage":
            raise InvalidPhase(f"run {run_id} is {st['phase']}, not awaiting triage")
        self._wf.resume(run_id, {"decisions_recorded": True})
        return self.status(run_id)

    def approve(self, run_id: str, reviewer: str, note: str | None = None) -> dict[str, Any]:
        st = self.status(run_id)
        if st["phase"] in TERMINAL:
            raise InvalidPhase(f"run {run_id} is already reviewed")
        if st["phase"] != "awaiting_approval":
            raise InvalidPhase(f"run {run_id} is {st['phase']}, not awaiting approval (decide pending triage first)")
        self._wf.resume(run_id, {"decision": "approve", "reviewer": reviewer, "note": note})
        return self.status(run_id)
