"""Docs match the implementation: the Mermaid flowchart is the one LangGraph generates from the compiled graph."""
import re
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from s0_spike.graph import build_graph
from s0_spike.ledger import Ledger

DOCS = Path(__file__).resolve().parents[1] / "docs"


def generated_mermaid(tmp_path):
    return build_graph(InMemorySaver(), Ledger(tmp_path / "l.sqlite")).get_graph().draw_mermaid().strip()


def test_architecture_doc_embeds_the_generated_graph(tmp_path):
    text = (DOCS / "architecture.md").read_text()
    blocks = [b.strip() for b in re.findall(r"```mermaid\n(.*?)```", text, re.S)]
    assert generated_mermaid(tmp_path) in blocks, "regenerate: python -m s0_spike graph, paste into docs/architecture.md"


def test_graph_nodes_are_the_documented_ones(tmp_path):
    nodes = set(build_graph(InMemorySaver(), Ledger(tmp_path / "l.sqlite")).get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"init_run", "freeze_input", "dispatch", "investigate", "collect", "aggregate",
                     "approval_gate", "finalize_approved", "finalize_rejected"}
    text = (DOCS / "architecture.md").read_text()
    assert all(f"`{n}`" in text for n in nodes)
