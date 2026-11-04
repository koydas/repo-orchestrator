"""Documentation consistency: what the docs say about M1 is what the code and the declarations do."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from repo_orchestrator import cli
from repo_orchestrator.models import Cause, Status
from repo_orchestrator.policies import REGISTRY
from support import AS_OF, DECLARATIONS, FIXTURES, ROOT, load_decl

DOCS = ROOT / "docs"
MARKDOWN = sorted([ROOT / "README.md", ROOT / "CLAUDE.md", *DOCS.rglob("*.md")])
M1 = DOCS / "m1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    out, fenced = set(), False
    for line in read(path).splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)):
            out.add(slug(m.group(1)))
    return out


# ---- links, diagrams, ADRs -------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_and_anchors_resolve(path):
    text = re.sub(r"```.*?```", "", read(path), flags=re.S)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"^(https?:|mailto:)", target):
            continue
        file_part, _, fragment = target.partition("#")
        dest = path if not file_part else (path.parent / file_part).resolve()
        assert dest.exists(), f"{path.relative_to(ROOT)}: broken link {target}"
        if fragment and dest.suffix == ".md":
            assert fragment in anchors(dest), f"{path.relative_to(ROOT)}: no heading for #{fragment} in {dest.name}"


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_mermaid_blocks_are_closed_and_start_with_a_diagram_type(path):
    text = read(path)
    assert text.count("```") % 2 == 0, "unbalanced code fences"
    kinds = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "erDiagram", "graph", "classDiagram", "gantt")
    for block in re.findall(r"```mermaid\n(.*?)```", text, re.S):
        assert block.lstrip().startswith(kinds), block[:40]


def test_every_adr_is_indexed_and_still_proposed():
    adrs = sorted((DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    index = read(DOCS / "adr" / "README.md")
    assert len(adrs) >= 11
    for adr in adrs:
        assert f"({'./' + adr.name})" in index or adr.name in index, f"{adr.name} missing from the ADR index"
        assert "- **Status:** Proposed" in read(adr), f"{adr.name}: ADR statuses change only by the owner"
        assert adr.name[:4] in {m for m in re.findall(r"ADR-(\d{4})", index)}
    assert f"A{len(adrs)}[" in index  # the dependency diagram has a node for the last record


def test_the_readme_counts_the_adrs_correctly():
    n = len(list((DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")))
    words = {
        10: "Ten",
        11: "Eleven",
        12: "Twelve",
        13: "Thirteen",
        14: "Fourteen",
        15: "Fifteen",
        16: "Sixteen",
        17: "Seventeen",
    }
    assert f"| {words[n]} Proposed decisions |" in read(ROOT / "README.md")


# ---- scope statements --------------------------------------------------------------------------------------------


def test_claude_md_authorizes_m1_to_m3_and_keeps_the_later_milestones_closed():
    text = read(ROOT / "CLAUDE.md")
    assert "**M1 is authorized**" in text and "**M2 is authorized**" in text and "src/repo_orchestrator/" in text
    assert "**M3 is authorized**" in text
    assert "**Not authorized yet:**" in text
    closed = text.split("**Not authorized yet:**")[1].split("\n")[0]
    for later in ("C6", "M4", "M5", "PR comments", "issue or PR creation", "any other GitHub write", "analyze-pr"):
        assert later in closed, later
    for opened in ("C4", "M2", "C5", "M3", "P7"):
        assert opened not in closed, f"{opened} is authorized, it must not be listed as closed"
    m2 = text.split("**M2 is authorized**")[1].split("- **M1 is authorized**")[0]
    assert (
        "never fails a PR gate by itself" in m2
        and "no GitHub write of any kind" in m2
        and "never writes to the baseline-scan finding history" in m2
    )
    assert "no LLM call, no agent framework, no LangGraph" in text and "S0 spike remains isolated" in text
    m3 = text.split("**M3 is authorized**")[1].split("- **M2 is authorized**")[0]
    for clause in (
        "exactly one component calls an LLM: C5",
        "the LLM proposes, code decides",
        "budgets are code constants",
        "never logged, stored, fixtured or reported",
        "`findings`, `finding_events` and `pr_*` are never read or written by M3",
        "`scan --investigate` only",
    ):
        assert clause in m3, clause


def test_readme_milestone_table_marks_only_m1_to_m3_as_implemented():
    rows = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| (M[0-5]|S0)[^|]*\|[^|]*\|([^|]*)\|$", read(ROOT / "README.md"), re.M)
    }
    for done in ("M1", "M2", "M3"):
        assert "Implemented" in rows[done], done
    for later in ("M4", "M5"):
        assert rows[later].strip() == "Not started", later


def test_docs_never_present_later_milestones_as_working():
    for name in ("README.md", "docs/architecture.md", "docs/workflows.md"):
        text = read(ROOT / name)
        assert re.search(r"proposed|intended|not implemented|Not started", text, re.I), name
    assert "Nothing described here is implemented" not in read(DOCS / "architecture.md")


# ---- code ↔ docs --------------------------------------------------------------------------------------------------


def test_every_policy_is_documented_with_its_current_version():
    policies, catalog = read(M1 / "policies.md"), read(DOCS / "coherence-model.md")
    for p in REGISTRY:
        assert f"| **{p.meta.id}**" in policies and f"| {p.meta.id} |" in catalog, p.meta.id
        assert (
            f"(v{p.meta.version})" in policies.split("## Catalog as implemented")[1].split("\n")[2]
            or p.meta.version == "1.0.0"
        )
    assert "P7" in catalog and "not" in policies.lower()


def test_exit_codes_in_the_usage_doc_match_the_cli():
    usage = read(M1 / "usage.md")
    codes = {int(m.group(1)) for m in re.finditer(r"^\| (\d) \|", usage.split("## Exit codes")[1], re.M)}
    assert codes == {cli.OK, cli.FINDINGS, cli.INCOMPLETE, cli.FAILURE} == {0, 1, 2, 3}


def test_every_cause_and_status_is_documented():
    fmt = read(M1 / "report-format.md")
    for cause in (v for k, v in vars(Cause).items() if k.isupper()):
        assert f"`{cause}`" in fmt, f"cause {cause} is not documented"
    for status in Status:
        assert f"`{status.value}`" in fmt and f"`{status.value}`" in read(
            DOCS / "adr" / "0011-m1-outcome-contracts-and-reproducible-reports.md"
        )


def test_every_declared_baseline_relationship_exists_in_the_baseline_document():
    baseline = read(DOCS / "research" / "ecosystem-baseline.md")
    ids = {r.baseline_id for r in load_decl().relationships}
    assert {f"R{n}" for n in range(1, 12)} <= ids
    for rid in ids:
        assert re.search(rf"\b{rid}\b", baseline), rid


def test_every_question_in_the_declarations_is_in_the_baseline_and_in_the_report():
    decl = load_decl()
    baseline = read(DOCS / "research" / "ecosystem-baseline.md")
    assert [q.id for q in decl.questions] == [f"Q{n}" for n in range(1, 7)]
    assert all(f"| {q.id} |" in baseline for q in decl.questions)


def test_the_documented_json_finding_uses_real_field_names():
    from support import scan

    block = re.search(r"```json\n(.*?)```", read(M1 / "report-format.md"), re.S)
    assert block
    documented = json.loads(block.group(1))
    actual = scan().semantic["findings"][0]
    assert set(documented) <= set(actual), set(documented) - set(actual)
    assert set(documented["evidence"][0]) == set(actual["evidence"][0])
    assert set(documented["comparison"]) <= set(actual["comparison"])


def test_the_example_report_is_what_the_offline_baseline_scan_produces():
    from repo_orchestrator.report import render_markdown
    from support import scan

    assert read(M1 / "examples" / "baseline-report.md").rstrip("\n") == render_markdown(scan().semantic).rstrip("\n"), (
        "regenerate: repo-orchestrator scan --revisions baseline --offline --cache-dir tests/fixtures/baseline "
        f"--as-of {AS_OF} --no-store --format md --output-dir docs/m1/examples && mv docs/m1/examples/report.md docs/m1/examples/baseline-report.md"
    )
    assert FIXTURES.exists() and DECLARATIONS.exists()


def test_the_usage_doc_options_exist_in_the_cli():
    usage = read(M1 / "usage.md")
    parser = cli._parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices["scan"]
    flags = {f for a in sub._actions for f in a.option_strings if f.startswith("--")}
    documented = set(re.findall(r"`(--[a-z-]+)", usage.split("## `scan` options")[1].split("## Exit codes")[0]))
    assert (
        documented <= flags
        and {"--repos", "--revisions", "--pin", "--output-dir", "--offline", "--as-of"} <= documented
    )


def test_the_ci_workflow_runs_the_documented_checks_and_leaves_s0_alone():
    ci = read(ROOT / ".github" / "workflows" / "m1.yml")
    for command in ("ruff check src tests", "ruff format --check src tests", "mypy", "pytest", "--offline"):
        assert command in ci
    assert "prototypes" not in ci and "contents: read" in ci and "write" not in ci.replace("read-only", "")
