import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from s0_spike.runner import Orchestrator

SRC = str(Path(__file__).resolve().parents[1] / "src")


def cli(state_dir, *args):
    """Runs one CLI command in a fresh Python process with a scrubbed environment (no tokens, no API keys)."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": SRC, "HOME": str(state_dir)}
    p = subprocess.run([sys.executable, "-m", "s0_spike", "--state-dir", str(state_dir), *args],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, out


@pytest.fixture
def orch(tmp_path):
    o = Orchestrator(tmp_path / "state")
    yield o
    o.close()


@pytest.fixture
def make_orch(tmp_path):
    made = []

    def make(limits=None, name="state"):
        o = Orchestrator(tmp_path / name, *( [limits] if limits else []))
        made.append(o)
        return o
    yield make
    for o in made:
        o.close()


def approve(o, run_id, decision="approve"):
    return o.resume(run_id, decision, "tester", o.status(run_id)["pending_report_digest"], note="test")


def by_id(report):
    return {f["candidate_id"]: f for f in report["findings"]}
