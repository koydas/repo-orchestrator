"""Regenerate tests/fixtures/investigation/scripted-baseline-recording.json.

The recording holds the SCRIPTED Investigator's answers keyed by the hash of the exact request (prompt, model, parameters),
not answers of a live model: no live call was made to produce it. Run it when a prompt, the tool protocol or a pinned
fixture changes (a stale recording makes tests/test_inv_cli.py fail with `replay_miss`):

    python tests/tools/record_investigation.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from repo_orchestrator.investigation.store import InvestigationStore  # noqa: E402
from support_inv import baseline_run, scripted_baseline  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "investigation" / "scripted-baseline-recording.json"


def build() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        inv, _ = baseline_run(Path(tmp), scripted_baseline())
        store = InvestigationStore(Path(tmp) / "state.sqlite")
        used = {k for r in store.results(inv.run_id).values() for k in r["response_keys"]}
        recording = store.export_recording(used)
        for entry in recording.entries.values():
            entry["latency_ms"] = 0  # a scripted answer has no meaningful latency
        return recording.dumps()


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
