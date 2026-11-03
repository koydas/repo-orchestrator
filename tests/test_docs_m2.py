"""Documentation consistency for M2: what docs/m2 says is what the code, the examples and the CI do."""

from __future__ import annotations

import json
import re

from pr_cases import CASES
from repo_orchestrator import cli
from repo_orchestrator.models import Cause
from repo_orchestrator.pr_report import GATE_RULE, render_pr_markdown
from support import ROOT, analyze

DOCS = ROOT / "docs"
M2 = DOCS / "m2"


def read(path):
    return path.read_text(encoding="utf-8")


def test_the_m2_pages_exist_and_the_index_links_them_all():
    index = read(M2 / "README.md")
    for page in ("usage", "impact-model", "report-format", "evaluation", "limitations"):
        assert (M2 / f"{page}.md").exists() and f"({page}.md)" in index, page


def test_the_usage_doc_options_exist_in_the_analyze_pr_command():
    parser = cli._parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices["analyze-pr"]
    flags = {f for a in sub._actions for f in a.option_strings if f.startswith("--")}
    documented = set(
        re.findall(r"`(--[a-z-]+)", read(M2 / "usage.md").split("## `analyze-pr` options")[1].split("## Exit codes")[0])
    )
    assert documented <= flags
    assert {"--revisions", "--pin", "--head-sha", "--output-dir", "--offline", "--as-of", "--state-db"} <= documented


def test_the_exit_codes_documented_for_analyze_pr_are_the_cli_codes():
    codes = {
        int(m.group(1))
        for m in re.finditer(
            r"^\| (\d) \|", read(M2 / "usage.md").split("## Exit codes")[1].split("## The gate rule")[0], re.M
        )
    }
    assert codes == {cli.OK, cli.FINDINGS, cli.INCOMPLETE, cli.FAILURE}


def test_the_gate_rule_is_stated_in_the_code_the_docs_and_claude_md():
    assert "Pre-existing findings never fail a PR gate by themselves" in GATE_RULE
    assert "**A pre-existing finding never fails a PR gate by itself.**" in read(M2 / "usage.md")
    assert "never fails a PR gate by itself" in read(ROOT / "CLAUDE.md")
    assert "never fails a PR gate by itself" in read(DOCS / "adr" / "0012-m2-pr-impact-contracts.md")


def test_every_pr_cause_and_label_is_documented():
    fmt, model = read(M2 / "report-format.md"), read(M2 / "impact-model.md")
    for cause in (Cause.PR_NOT_FOUND, Cause.PR_FILES_TRUNCATED, Cause.PR_FILE_LIST_MISMATCH):
        assert f"`{cause}`" in fmt and f"`{cause}`" in read(DOCS / "m1" / "report-format.md"), cause
    for label in ("introduced", "pre-existing", "resolved", "undetermined"):
        assert f"`{label}`" in model, label


def test_the_documented_report_fields_exist_in_the_semantic_part():
    fields = {"schema_version", "kind", "tool", "inputs", "run_status", "gate", "summary", "changed_files", "impact",
              "value_changes", "findings", "problems", "unevaluated", "base_unevaluated", "policy_clarifications", "not_touched"}  # fmt: skip
    semantic = analyze(CASES["u1"]).semantic
    assert fields == set(semantic) - {"result_digest"}
    doc = read(M2 / "report-format.md")
    for name in fields:
        assert f"`{name}`" in doc, name


def test_the_generated_examples_are_what_the_offline_fixtures_produce():
    for case, stem in (
        ("u1", "u1-introduced"),
        ("u5_raw", "u5-raw-pre-existing"),
        ("truncated", "truncated-incomplete"),
    ):
        semantic = analyze(CASES[case]).semantic
        assert read(M2 / "examples" / f"{stem}.md") == render_pr_markdown(semantic), f"regenerate examples/{stem}.md"
    semantic = analyze(CASES["u1"]).semantic
    assert (
        read(M2 / "examples" / "u1-introduced.json")
        == json.dumps(semantic, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def test_the_ci_workflow_runs_the_m2_checks_offline_and_the_live_check_only_by_hand():
    ci = read(ROOT / ".github" / "workflows" / "m1.yml")
    assert "tests/test_pr_eval.py" in ci and "analyze-pr" in ci and "--offline" in ci
    live = ci.split("live-pr:")[1]
    assert "github.event_name == 'workflow_dispatch'" in live and "inputs.live_pr" in live
    assert "pull-requests: read" in live and "contents: read" in live
    assert "${{ inputs.live_pr }}" not in live.split("run: |")[1], (
        "the reference must reach the shell through the environment"
    )
    assert "prototypes" not in ci and "write" not in ci.replace("read-only", "")


def test_the_adr_for_m2_is_indexed_and_answers_the_two_open_questions_of_adr_0011():
    adr = read(DOCS / "adr" / "0012-m2-pr-impact-contracts.md")
    assert (
        "- **Status:** Proposed" in adr
        and "Report-schema versioning" in adr
        and "`policy_clarification` and exit codes" in adr
    )
    index = read(DOCS / "adr" / "README.md")
    assert "ADR-0012" in index and 'A12["' in index
    assert "ADR-0012" in read(DOCS / "adr" / "0011-m1-outcome-contracts-and-reproducible-reports.md")
