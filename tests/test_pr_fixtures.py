"""The committed PR metadata fixtures (tests/fixtures/pr) are valid, versioned, and describe the trees they claim to."""

from __future__ import annotations

import json

import pytest

from pr_cases import CASES, PR_FIXTURES, fixture_path
from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.collector.pr import FIXTURE_SCHEMA_VERSION, fixture_json
from repo_orchestrator.pr_runner import tree_changes
from support import PRWorld


def committed():
    return sorted(PR_FIXTURES.rglob("*.json"))


def test_there_is_one_fixture_per_case_and_nothing_else():
    assert committed() == sorted(fixture_path(c) for c in CASES.values())


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.name)
def test_every_fixture_is_versioned_pinned_and_flagged_synthetic(case):
    data = json.loads(fixture_path(case).read_text())
    assert data["schema_version"] == FIXTURE_SCHEMA_VERSION and data["kind"] == "pull_request"
    assert data["base"]["sha"] == case.base_sha and data["head"]["sha"] == case.head_sha == fixture_path(case).stem
    assert data["synthetic"]  # a replay of a mutation, never presented as a real pull request


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.name)
def test_the_committed_fixture_is_what_the_case_definition_writes(case):
    assert fixture_path(case).read_text() == fixture_json(case.pull_request(), synthetic=case.note)


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.name)
def test_the_listed_files_explain_the_difference_between_the_trees(case):
    """Independent of the analyzer: the base→head tree difference is a subset of what the fixture lists, and every listed
    file exists (or not) on head as its status says."""
    pr = CachingSource(None, PR_FIXTURES).pull_request(case.slug, case.number)
    world = PRWorld(case)
    base, head = world.tree(case.slug, pr.base_sha), world.tree(case.slug, pr.head_sha)
    listed = {f.path for f in pr.files} | {f.previous_path for f in pr.files if f.previous_path}
    assert tree_changes(base, head) <= listed
    on_head = {e.path for e in head.entries}
    for f in pr.files:
        assert (f.path in on_head) == (f.status != "removed"), f
        if f.previous_path:
            assert f.previous_path not in on_head


def test_the_truncated_fixture_really_lists_fewer_files_than_it_declares():
    pr = CachingSource(None, PR_FIXTURES).pull_request("koydas/autonomous-dev-loop", 110)
    assert pr.changed_files == 7 and len(pr.files) == 1 and not pr.files_complete
