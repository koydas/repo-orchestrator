"""CLI contract: exit codes, output files, offline use, error handling."""

from __future__ import annotations

import json

import pytest

from repo_orchestrator.cli import main
from support import DECLARATIONS, FIXTURES

BASE = [
    "--declarations",
    str(DECLARATIONS),
    "--revisions",
    "baseline",
    "--offline",
    "--cache-dir",
    str(FIXTURES),
    "--as-of",
    "2026-11-02",
]


def scan_args(tmp_path, *extra):
    return ["scan", *BASE, "--output-dir", str(tmp_path / "out"), "--state-db", str(tmp_path / "s.sqlite"), *extra]


def test_validate_loads_the_declarations(capsys):
    assert main(["validate", "--declarations", str(DECLARATIONS)]) == 0
    assert "9 repositories, 22 relationships, 6 policies" in capsys.readouterr().out


def test_scan_on_the_baseline_exits_1_for_findings_and_writes_both_reports(tmp_path, capsys):
    assert main(scan_args(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "FINDING [medium] P1 R1.adl claim:adrs" in out and "fix at adl:README.md" in out
    assert "QUESTION P6 R5.chat" in out and "exit code 1 (complete, findings detected)" in out
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    assert set(body) == {"semantic", "execution"} and len(body["semantic"]["findings"]) == 2
    assert body["execution"]["persisted"] and body["execution"]["history"]["new"]
    md = (tmp_path / "out" / "report.md").read_text()
    assert "## Findings (confirmed by a deterministic check)" in md and body["semantic"]["result_digest"][:16] in md


def test_the_second_identical_scan_is_idempotent_in_the_store_and_in_the_reports(tmp_path):
    main(scan_args(tmp_path, "--quiet"))
    first = (tmp_path / "out" / "report.md").read_text()
    main(scan_args(tmp_path, "--quiet"))
    assert (tmp_path / "out" / "report.md").read_text() == first
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    assert body["execution"]["new_run"] is False


def test_format_selection_and_no_store(tmp_path):
    assert main(scan_args(tmp_path, "--format", "json", "--no-store", "--quiet")) == 1
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["report.json"]
    assert not (tmp_path / "s.sqlite").exists()


def test_scope_selection_that_contains_no_finding_exits_0(tmp_path, capsys):
    assert main(scan_args(tmp_path, "--repos", "adl,adt")) == 0
    assert "exit code 0 (complete, no findings)" in capsys.readouterr().out


def test_incomplete_evaluation_exits_2_and_takes_precedence_over_findings(tmp_path, capsys):
    # not in the offline cache: a pin that was never collected
    args = [
        "scan",
        *BASE[:4],
        "--offline",
        "--cache-dir",
        str(tmp_path / "empty"),
        "--output-dir",
        str(tmp_path / "o"),
        "--no-store",
        "--pin",
        "ap=" + "0" * 40,
    ]
    assert main(args) == 2
    out = capsys.readouterr().out
    assert "COLLECTION FAILURE" in out and "not_cached_offline" in out and "exit code 2 (incomplete evaluation)" in out


def test_a_moving_ref_offline_is_an_incomplete_run_not_a_crash(tmp_path):
    args = [
        "scan",
        "--declarations",
        str(DECLARATIONS),
        "--offline",
        "--cache-dir",
        str(tmp_path / "c"),
        "--output-dir",
        str(tmp_path / "o"),
        "--no-store",
        "--quiet",
    ]
    assert main(args) == 2


@pytest.mark.parametrize(
    "extra",
    [
        ["--revisions", "nope"],
        ["--pin", "adl"],
        ["--pin", "ghost=" + "a" * 40],
        ["--repos", "ghost"],
        ["--repos", "fullstack-pilot"],
        ["--format", "pdf"],
    ],
)
def test_configuration_errors_exit_3_with_a_message_on_stderr(tmp_path, capsys, extra):
    args = [
        "scan",
        "--declarations",
        str(DECLARATIONS),
        "--offline",
        "--cache-dir",
        str(FIXTURES),
        "--output-dir",
        str(tmp_path),
        "--no-store",
        *extra,
    ]
    assert main(args) == 3
    assert capsys.readouterr().err.startswith("error:")


def test_missing_declarations_exit_3(tmp_path, capsys):
    assert main(["validate", "--declarations", str(tmp_path / "nope")]) == 3
    assert "cannot read" in capsys.readouterr().err


def test_pin_overrides_a_named_set(tmp_path, capsys):
    args = scan_args(tmp_path, "--no-store", "--quiet", "--pin", "profile=" + "1" * 40)
    assert main(args) == 2  # that commit is not in the offline cache
    assert "profile" in (tmp_path / "out" / "report.md").read_text()
