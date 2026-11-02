"""The pinned fixtures are history: they must stay byte-identical to what GitHub served at the baseline revisions."""

from __future__ import annotations

import json

import pytest

from repo_orchestrator.collector.source import git_blob_sha
from repo_orchestrator.engine import Engine
from support import FIXTURES, load_decl

TREES = sorted(FIXTURES.glob("*/*/*/tree.json"))


def test_there_is_one_fixture_tree_per_analyzed_repository_at_its_baseline_revision():
    decl = load_decl()
    slugs = {r.id: r.slug for r in decl.repositories}
    expected = {(slugs[rid], sha) for rid, sha in decl.pins["baseline"].revisions.items()}
    found = {(f"{t.parts[-4]}/{t.parts[-3]}", t.parts[-2]) for t in TREES}
    assert found == expected


@pytest.mark.parametrize("tree_path", TREES, ids=lambda p: p.parts[-3])
def test_every_cached_file_matches_the_git_blob_id_recorded_in_its_tree(tree_path):
    tree = json.loads(tree_path.read_text())
    assert tree["sha"] == tree_path.parts[-2] and tree["truncated"] is False
    blobs = {path: sha for path, kind, _, sha in tree["entries"] if kind == "blob"}
    files = [p for p in (tree_path.parent / "files").rglob("*") if p.is_file()]
    assert files, "an empty fixture proves nothing"
    for f in files:
        rel = f.relative_to(tree_path.parent / "files").as_posix()
        assert rel in blobs, f"{rel} is not part of the pinned tree"
        assert git_blob_sha(f.read_bytes()) == blobs[rel], (
            f"{rel} was modified: fixtures are history, never edited to make a test pass"
        )


def test_the_fixtures_hold_exactly_what_the_declared_plan_needs():
    """No stale files (they would hide a plan change) and no missing ones (the offline run would be incomplete)."""
    decl = load_decl()
    engine = Engine(decl)
    slugs = {r.id: r.slug for r in decl.repositories}
    from repo_orchestrator.collector.cache import CachingSource
    from repo_orchestrator.collector.snapshot import RepoTarget, collect

    plan = engine.plan(set(engine.graph.analyzed_ids()))
    targets = [RepoTarget(rid, slugs[rid], decl.pins["baseline"].revisions[rid]) for rid in engine.graph.analyzed_ids()]
    snap = collect(CachingSource(None, FIXTURES), targets, plan)
    wanted = {
        (slugs[rid], snap.repo(rid).sha, path)
        for rid in engine.graph.analyzed_ids()
        for path in snap.repo(rid).artifacts
    }
    on_disk = set()
    for tree in TREES:
        slug = f"{tree.parts[-4]}/{tree.parts[-3]}"
        for f in (tree.parent / "files").rglob("*"):
            if f.is_file():
                on_disk.add((slug, tree.parts[-2], f.relative_to(tree.parent / "files").as_posix()))
    assert on_disk == wanted
