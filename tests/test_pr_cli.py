"""`repo-orchestrator analyze-pr`: exit codes, files, offline replay of recorded pull requests, error handling."""

from __future__ import annotations

import json

import pytest

from pr_cases import CASES
from repo_orchestrator.cli import main, parse_pr_ref
from repo_orchestrator.models import ConfigError
from support import DECLARATIONS, materialize_pr_cache


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return materialize_pr_cache(tmp_path_factory.mktemp("pr-cache"), CASES.values())


def args(cache, tmp_path, ref, *extra):
    return [
        "analyze-pr", ref, "--declarations", str(DECLARATIONS), "--revisions", "baseline", "--offline",
        "--cache-dir", str(cache), "--as-of", "2026-11-02", "--output-dir", str(tmp_path / "out"),
        "--state-db", str(tmp_path / "s.sqlite"), *extra,
    ]  # fmt: skip


class TestReference:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("koydas/ollama-chat#12", ("koydas/ollama-chat", 12)),
            ("koydas/koydas#1", ("koydas/koydas", 1)),
            ("https://github.com/koydas/gitops-homelab/pull/7", ("koydas/gitops-homelab", 7)),
            ("https://github.com/koydas/gitops-homelab/pull/7/", ("koydas/gitops-homelab", 7)),
        ],
    )
    def test_accepted_forms(self, text, expected):
        assert parse_pr_ref(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "koydas/ollama-chat",
            "koydas/ollama-chat#",
            "koydas/ollama-chat#0",
            "#4",
            "a/b/c#1",
            "koydas/x#1;rm",
            "http://github.com/a/b/pull/1",
        ],
    )
    def test_everything_else_is_a_configuration_error(self, text):
        with pytest.raises(ConfigError):
            parse_pr_ref(text)


def test_an_introduced_finding_exits_1_and_writes_both_reports(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/autonomous-dev-loop#101")) == 1
    out = capsys.readouterr().out
    assert "INTRODUCED [high] P2 R2 schema_version" in out and "IMPACT R2 runtime_contract → adt [breaks]" in out
    assert "gate fail · exit code 1 (complete, introduced finding(s))" in out
    body = json.loads((tmp_path / "out" / "pr-impact.json").read_text())
    assert set(body) == {"semantic", "execution"} and body["semantic"]["kind"] == "pr_impact"
    assert body["execution"]["persisted"] and body["execution"]["pull_request"]["state"] == "open"
    md = (tmp_path / "out" / "pr-impact.md").read_text()
    assert (
        md.startswith("# Pull request impact: `koydas/autonomous-dev-loop#101`")
        and body["semantic"]["result_digest"][:16] in md
    )


def test_a_pre_existing_finding_exits_0(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/agent-patterns#106")) == 0
    out = capsys.readouterr().out
    assert (
        "PRE-EXISTING [medium] P1 R1.ap claim:patterns" in out
        and "exit code 0 (complete, no introduced finding)" in out
    )


def test_a_pr_that_resolves_a_finding_exits_0_and_says_so(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/koydas#107")) == 0
    assert "RESOLVED [medium] P1 R1.ap claim:patterns" in capsys.readouterr().out


def test_a_pr_with_no_impact_exits_0(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/ollama-chat#109")) == 0
    assert "touched relationships 0" in capsys.readouterr().out


def test_an_incomplete_analysis_exits_2(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/autonomous-dev-loop#110")) == 2
    assert "PROBLEM change_set [pr_files_truncated]" in capsys.readouterr().out


def test_an_unrecorded_pull_request_is_incomplete_not_a_failure(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/ollama-chat#4242")) == 2
    assert "PROBLEM pull_request [not_cached_offline]" in capsys.readouterr().out


def test_the_url_form_works(cache, tmp_path):
    assert main(args(cache, tmp_path, "https://github.com/koydas/ollama-chat/pull/102", "--quiet")) == 1


def test_a_repository_outside_the_declared_set_exits_3(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "someone/else#1")) == 3
    assert "not a declared repository" in capsys.readouterr().err


def test_a_declared_repository_outside_the_analyzed_scope_exits_3(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "koydas/repo-orchestrator#1")) == 3
    assert "outside the analyzed scope" in capsys.readouterr().err


def test_pinning_the_pr_repository_is_refused(cache, tmp_path, capsys):
    pin = "chat=" + "a" * 40
    assert main(args(cache, tmp_path, "koydas/ollama-chat#102", "--pin", pin)) == 3
    assert "read at its base and head" in capsys.readouterr().err


def test_a_bad_reference_or_format_exits_3(cache, tmp_path, capsys):
    assert main(args(cache, tmp_path, "not-a-pr")) == 3
    assert main(args(cache, tmp_path, "koydas/ollama-chat#102", "--format", "xml")) == 3


def test_format_selects_the_files(cache, tmp_path):
    assert main(args(cache, tmp_path, "koydas/ollama-chat#102", "--quiet", "--format", "json")) == 1
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["pr-impact.json"]


def test_the_second_identical_run_is_idempotent_in_the_store_and_in_the_reports(cache, tmp_path):
    main(args(cache, tmp_path, "koydas/gitops-homelab#103", "--quiet"))
    first = (tmp_path / "out" / "pr-impact.md").read_text()
    first_json = json.loads((tmp_path / "out" / "pr-impact.json").read_text())
    main(args(cache, tmp_path, "koydas/gitops-homelab#103", "--quiet"))
    second_json = json.loads((tmp_path / "out" / "pr-impact.json").read_text())
    assert (tmp_path / "out" / "pr-impact.md").read_text() == first
    assert first_json["semantic"] == second_json["semantic"]
    assert first_json["execution"]["new_run"] is True and second_json["execution"]["new_run"] is False
    assert first_json["execution"]["run_key"] == second_json["execution"]["run_key"]


def test_no_store_leaves_no_database(cache, tmp_path):
    main(args(cache, tmp_path, "koydas/ollama-chat#102", "--quiet", "--no-store"))
    assert not (tmp_path / "s.sqlite").exists()


def test_a_scan_and_a_pr_analysis_share_a_store_without_sharing_history(cache, tmp_path):
    from repo_orchestrator.store import RunStore

    scan = ["scan", "--declarations", str(DECLARATIONS), "--revisions", "baseline", "--offline", "--cache-dir", str(cache),
            "--as-of", "2026-11-02", "--output-dir", str(tmp_path / "scan"), "--state-db", str(tmp_path / "s.sqlite"), "--quiet"]  # fmt: skip
    assert main(scan) == 1
    store = RunStore(tmp_path / "s.sqlite")
    before = store.findings()
    store.close()
    assert main(args(cache, tmp_path, "koydas/ollama-chat#102", "--quiet")) == 1
    store = RunStore(tmp_path / "s.sqlite")
    assert store.findings() == before and store.run_count() == 1 and len(store.pr_runs()) == 1
    store.close()


def test_the_scan_command_is_unchanged(cache, tmp_path):
    scan = ["scan", "--declarations", str(DECLARATIONS), "--revisions", "baseline", "--offline", "--cache-dir", str(cache),
            "--as-of", "2026-11-02", "--output-dir", str(tmp_path / "scan"), "--no-store", "--quiet"]  # fmt: skip
    assert main(scan) == 1
