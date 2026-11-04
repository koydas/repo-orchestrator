"""M3 documentation, CI and example consistency: what the docs and the workflow say is what the code does."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from repo_orchestrator import cli
from repo_orchestrator.investigation import budgets
from repo_orchestrator.investigation.domain import CandState, EvStatus, ICause, Validation
from repo_orchestrator.investigation.report import EXIT_AWAITING_HUMAN
from support import ROOT

DOCS = ROOT / "docs"
M3 = DOCS / "m3"
WF = ROOT / ".github" / "workflows" / "m1.yml"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_ci_runs_the_m3_evaluation_offline_and_the_live_investigation_only_by_hand():
    wf = yaml.safe_load(read(WF))
    names = [s.get("name", "") for s in wf["jobs"]["test"]["steps"]]
    assert any(n.startswith("Offline M3 evaluation") for n in names) and any(
        n.startswith("Offline investigation") for n in names
    )
    install = next(s["run"] for s in wf["jobs"]["test"]["steps"] if "pip install" in s.get("run", ""))
    assert "investigation" in install, "the default gate installs the pinned LangGraph set, so no M3 test is skipped"
    text = read(WF)
    gate = json.dumps(wf["jobs"]["test"])
    assert "ANTHROPIC_API_KEY" not in gate and "secrets." not in gate, "the default gate never sees the key"
    live = wf["jobs"]["live-investigation"]
    assert live["if"] == "github.event_name == 'workflow_dispatch' && inputs.live_investigation"
    assert live["permissions"] == {"contents": "read"}
    assert [s for s in live["steps"] if "secrets.ANTHROPIC_API_KEY" in json.dumps(s)] and text.count(
        "secrets.ANTHROPIC_API_KEY"
    ) == 1
    assert "write" not in text.replace("read-only", "")


def test_the_ci_investigation_smoke_step_runs_as_written(tmp_path):
    wf = yaml.safe_load(read(WF))
    step = next(s for s in wf["jobs"]["test"]["steps"] if s.get("name", "").startswith("Offline investigation"))
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("ANTHROPIC_API_KEY", None)
    done = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "exit code: 1" in done.stdout and "investigation: awaiting_validation" in done.stdout


def test_claude_md_and_readme_describe_m3_as_implemented_and_m4_m5_as_not():
    rows = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| (M[0-5]|S0)[^|]*\|[^|]*\|([^|]*)\|$", read(ROOT / "README.md"), re.M)
    }
    assert "Implemented" in rows["M3"] and rows["M4"].strip() == "Not started" and rows["M5"].strip() == "Not started"
    assert "**implemented**" in read(DOCS / "mvp.md").split("**M3 Bounded investigation**")[1].split("\n")[0]
    claude = read(ROOT / "CLAUDE.md")
    assert "M3 (bounded investigation) are implemented; M4–M5 are design only" in claude
    for m4 in ("remediation plans", "optimization opportunities"):
        assert m4 in read(ROOT / "README.md")


def test_the_budgets_in_the_docs_are_the_budgets_in_the_code():
    doc = read(M3 / "budgets.md") if (M3 / "budgets.md").exists() else read(M3 / "README.md")
    b = budgets.BUDGET
    for label, value in (
        ("investigations per run", b.max_investigations),
        ("tool calls per investigation", b.max_tool_calls),
        ("model calls per investigation", b.max_llm_calls),
        ("input tokens per investigation", f"{b.max_input_tokens:,}"),
        ("output tokens per investigation", f"{b.max_output_tokens:,}"),
        ("tokens per run", f"{b.max_run_tokens:,}"),
    ):
        assert re.search(rf"\|\s*{re.escape(label)}\s*\|\s*{re.escape(str(value))}\s*\|", doc), (label, value)
    assert str(b.max_run_cost_micro_usd / 1_000_000) in doc or "2.00" in doc
    assert budgets.MODEL in doc and budgets.PRICE_TABLE_DATE in doc


def test_every_state_cause_and_status_is_documented_in_the_m3_docs():
    doc = read(M3 / "README.md") + read(M3 / "report-format.md")
    for enum in (CandState, EvStatus, Validation):
        for member in enum:
            assert f"`{member.value}`" in doc, member
    for cause in (v for k, v in vars(ICause).items() if k.isupper()):
        assert f"`{cause}`" in doc, f"cause {cause} is not documented"


def test_the_documented_exit_codes_and_options_exist_in_the_cli():
    usage = read(M3 / "usage.md")
    assert EXIT_AWAITING_HUMAN == 4 and "| 4 |" in usage
    parser = cli._parser()
    scan = next(a for a in parser._actions if a.dest == "command").choices["scan"]
    flags = {f for a in scan._actions for f in a.option_strings}
    assert {"--investigate", "--replay", "--record"} <= flags
    documented = set(re.findall(r"`(--[a-z-]+)", usage))
    assert {"--investigate", "--replay", "--record"} <= documented
    inv = next(a for a in parser._actions if a.dest == "command").choices["investigations"]
    subs = next(a for a in inv._actions if a.dest == "inv_command").choices
    for name in subs:
        assert f"investigations {name}" in usage or f"`{name}`" in usage, name


def test_the_example_reports_are_what_the_committed_recording_replays_to(tmp_path):
    out = tmp_path / "out"
    code = cli.main(
        ["scan", "--revisions", "baseline", "--offline", "--cache-dir", str(ROOT / "tests/fixtures/baseline"), "--as-of", "2026-11-02",
         "--no-store", "--quiet", "--output-dir", str(out), "--investigate", "--replay", str(ROOT / "tests/fixtures/investigation/scripted-baseline-recording.json")]
    )  # fmt: skip
    assert code == 1
    md = read(out / "report.md")
    assert read(M3 / "examples" / "investigation-report.md").rstrip("\n") == md.rstrip("\n"), (
        "regenerate: repo-orchestrator scan --revisions baseline --offline --cache-dir tests/fixtures/baseline --as-of 2026-11-02 "
        "--no-store --quiet --output-dir /tmp/m3 --investigate --replay tests/fixtures/investigation/scripted-baseline-recording.json "
        "&& cp /tmp/m3/report.md docs/m3/examples/investigation-report.md (and the investigation section of report.json into investigation-section.json)"
    )
    semantic = json.loads(read(out / "report.json"))["semantic"]
    section = json.loads(read(M3 / "examples" / "investigation-section.json"))
    assert section == {
        "result_digest": semantic["result_digest"],
        "deterministic_digest": semantic["deterministic_digest"],
        "investigation": semantic["investigation"],
    }


@pytest.mark.parametrize("name", ["README.md", "usage.md", "report-format.md", "evaluation.md", "limitations.md"])
def test_the_m3_pages_exist(name):
    assert (M3 / name).is_file()
