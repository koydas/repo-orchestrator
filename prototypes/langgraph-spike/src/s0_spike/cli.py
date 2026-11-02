"""CLI used by the multi-process tests. One command per process; JSON on stdout."""
import argparse
import json
import sys

from .errors import SpikeError
from .runner import Orchestrator


def main(argv=None):
    p = argparse.ArgumentParser(prog="s0_spike")
    p.add_argument("--state-dir", default="s0-state")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--run-id", required=True); s.add_argument("--scenario", required=True)
    for n in ("recover", "status"):
        sub.add_parser(n).add_argument("--run-id", required=True)
    r = sub.add_parser("resume")
    r.add_argument("--run-id", required=True); r.add_argument("--decision", required=True)
    r.add_argument("--reviewer", required=True); r.add_argument("--report-digest", required=True)
    r.add_argument("--note")
    sub.add_parser("graph", help="print the Mermaid flowchart of the compiled graph (no state dir needed)")
    a = p.parse_args(argv)
    if a.cmd == "graph":  # no state directory is created or touched
        from langgraph.checkpoint.memory import InMemorySaver

        from .graph import build_graph
        from .ledger import Ledger
        print(build_graph(InMemorySaver(), Ledger(":memory:")).get_graph().draw_mermaid())
        return 0
    orch = Orchestrator(a.state_dir)
    try:
        if a.cmd == "start":
            out = orch.start(a.run_id, a.scenario)
        elif a.cmd == "recover":
            out = orch.recover(a.run_id)
        elif a.cmd == "status":
            out = orch.status(a.run_id)
        else:
            out = orch.resume(a.run_id, a.decision, a.reviewer, a.report_digest, a.note)
    except SpikeError as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}))
        return 2
    finally:
        orch.close()
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
