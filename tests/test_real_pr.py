"""Offline replay of three REAL pull requests (tests/fixtures/real-pr, provenance in its README).

The expectations are facts about those pull requests and about the pinned baseline, written down independently of the
analyzer's output: which files the pull request changed, which relationships those files belong to, that nothing is
introduced, and which values moved.
"""

from __future__ import annotations

import json
import shutil

import pytest

from repo_orchestrator.cli import main
from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.engine import Engine
from repo_orchestrator.pr_report import render_pr_markdown
from repo_orchestrator.pr_runner import analyze_pull_request
from support import DECLARATIONS, FIXTURES, ROOT, load_decl, pr_options

REAL = ROOT / "tests" / "fixtures" / "real-pr"


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-pr-cache")
    shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
    shutil.copytree(REAL, root, dirs_exist_ok=True)
    return root


def run(cache, slug, number):
    return analyze_pull_request(Engine(load_decl()), CachingSource(None, cache), slug, number, pr_options())


def test_every_real_record_states_where_it_came_from():
    records = sorted(REAL.rglob("pulls/*/*.json"))
    assert len(records) == 3
    for path in records:
        data = json.loads(path.read_text())
        assert data["provenance"].startswith("recorded from a local git clone, not from the REST API")
        assert "synthetic" not in data and data["schema_version"] == 1


def test_homelab_2_changes_a_model_list_and_an_adr_and_is_not_blamed_for_the_rest(cache):
    a = run(cache, "koydas/gitops-homelab", 2)
    s = a.semantic
    assert [(f["path"], f["status"]) for f in s["changed_files"]] == [
        ("README.md", "modified"),
        ("apps/ollama/application.yaml", "modified"),
        ("docs/adr/0018-ghcr-public-visibility-no-pull-secret.md", "added"),
        ("docs/adr/README.md", "modified"),
    ]
    assert {e["relationship_id"] for e in s["impact"]} == {"R1.homelab", "R7", "R8"}
    assert {tuple(e["affected_repos"]) for e in s["impact"] if e["relationship_id"] in ("R7", "R8")} == {("chat",)}
    assert s["summary"]["deltas"] == {"introduced": 0, "pre-existing": 3, "resolved": 0, "undetermined": 0}
    assert a.exit_code == 0 and s["gate"]["result"] == "pass" and s["run_status"] == "complete"
    changed = {(v["relationship_id"], v["subject"]): v for v in s["value_changes"]}
    models = changed[("R7", "const:TEXT_MODEL")]
    assert (
        "qwen2.5vl:3b" not in models["base"]["right"] and "qwen2.5vl:3b" in models["head"]["right"]
    )  # what the PR added
    adrs = changed[("R1.homelab", "claim:adrs")]
    assert (adrs["base"]["right"], adrs["head"]["right"]) == (17, 18)  # the new ADR file is counted


def test_homelab_1_adds_an_adr_and_touches_only_the_documentary_relationship(cache):
    a = run(cache, "koydas/gitops-homelab", 1)
    assert {e["relationship_id"] for e in a.semantic["impact"]} == {"R1.homelab"}
    assert a.semantic["summary"]["deltas"]["introduced"] == 0 and a.exit_code == 0


def test_a_lockfile_only_pull_request_touches_no_relationship(cache):
    a = run(cache, "koydas/ollama-chat", 12)
    assert a.semantic["impact"] == [] and a.semantic["summary"]["affected_repositories"] == []
    assert a.exit_code == 0 and [f["path"] for f in a.semantic["changed_files"]] == ["package-lock.json"]


def test_the_real_analyses_are_deterministic(cache):
    for slug, number in (("koydas/gitops-homelab", 2), ("koydas/gitops-homelab", 1), ("koydas/ollama-chat", 12)):
        first, second = run(cache, slug, number), run(cache, slug, number)
        assert first.semantic == second.semantic
        assert render_pr_markdown(first.semantic) == render_pr_markdown(second.semantic)


def test_the_cli_replays_a_real_pull_request_offline(cache, tmp_path, capsys):
    args = ["analyze-pr", "koydas/gitops-homelab#2", "--declarations", str(DECLARATIONS), "--revisions", "baseline", "--offline",
            "--cache-dir", str(cache), "--as-of", "2026-11-02", "--no-store", "--output-dir", str(tmp_path)]  # fmt: skip
    assert main(args) == 0
    assert "PRE-EXISTING [high] P3 R7 const:VISION_MODEL" in capsys.readouterr().out
