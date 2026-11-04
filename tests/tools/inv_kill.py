"""Helper process of tests/test_inv_workflow.py: run the investigation on the pinned baseline and die hard (os._exit) in
the middle of one candidate's first model call. A second invocation without the kill resumes from the checkpoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from support_inv import Scripted, baseline_run, h2_script, subject_of  # noqa: E402


def _wait_for_a_finished_investigation(state: Path) -> None:
    """Die only after another candidate stored its result, so the test can show finished work is not redone."""
    import time

    from repo_orchestrator.investigation.store import InvestigationStore

    store = InvestigationStore(state / "state.sqlite")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for run in store.runs():
            if store.results(run["run_id"]):
                return
        time.sleep(0.05)


def main(tmp: str, kill_subject: str) -> int:
    state = Path(tmp)
    calls = state / "calls.log"

    script = h2_script()

    def fn(req, n):
        subject = subject_of(req)
        with calls.open("a") as fh:
            fh.write(subject + "\n")
        if subject == kill_subject:
            _wait_for_a_finished_investigation(state)
            os._exit(137)  # a hard kill: no cleanup, no checkpoint of the running task
        return script(req, n)

    inv, _ = baseline_run(state, Scripted(fn))
    print(inv.phase, inv.exit_code)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
