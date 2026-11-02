from __future__ import annotations

import base64
import json

import pytest

from repo_orchestrator.collector import github as gh
from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.collector.github import GitHubSource, Response, TransportError
from repo_orchestrator.collector.overlay import OverlaySource, add_file, delete_path, rename_path, replace_text
from repo_orchestrator.collector.snapshot import (
    Artifact,
    ArtifactRequest,
    Limits,
    Missing,
    RepoTarget,
    collect,
    glob_regex,
)
from repo_orchestrator.collector.source import SourceError, TreeEntry, git_blob_sha, safe_relative_path
from repo_orchestrator.models import Cause
from support import MemorySource

SHA = "a" * 40


class Script:
    """Scripted transport: returns/raises the queued items in order and records requests."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(body, **headers):
    return Response(200, headers, body if isinstance(body, bytes) else json.dumps(body).encode())


def status(code, **headers):
    return Response(code, headers, b"{}")


def source(*items, **kw):
    sleeps: list[float] = []
    t = Script(*items)
    return GitHubSource(transport=t, sleep=sleeps.append, **kw), t, sleeps


def test_resolve_returns_the_commit_sha_and_sends_only_get_requests_with_no_token_by_default():
    s, t, _ = source(ok({"default_branch": "main", "private": False}), ok(SHA.encode()))
    assert s.resolve("o/r", None) == SHA
    assert t.calls[0][0].endswith("/repos/o/r") and t.calls[1][0].endswith("/repos/o/r/commits/main")
    assert all("Authorization" not in h for _, h in t.calls)
    assert t.calls[1][1]["Accept"] == "application/vnd.github.sha"


def test_token_is_sent_as_a_bearer_header_only_when_configured():
    t = Script(ok({"default_branch": "main"}), ok(SHA.encode()))
    GitHubSource("tok", transport=t, sleep=lambda _: None).resolve("o/r", None)
    assert t.calls[0][1]["Authorization"] == "Bearer tok"


def test_explicit_pin_must_exist_and_match():
    s, _, _ = source(ok({"default_branch": "main"}), ok(("b" * 40).encode()))
    with pytest.raises(SourceError) as err:
        s.resolve("o/r", SHA)
    assert err.value.kind == Cause.REVISION_UNRESOLVED


def test_private_repository_is_refused():
    s, _, _ = source(ok({"default_branch": "main", "private": True}))
    with pytest.raises(SourceError) as err:
        s.resolve("o/r", None)
    assert err.value.kind == Cause.PRIVATE_REPOSITORY


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (status(404), Cause.REPOSITORY_NOT_FOUND),
        (status(401), Cause.PERMISSION_DENIED),
        (status(403), Cause.PERMISSION_DENIED),
    ],
)
def test_permanent_errors_are_not_retried(response, kind):
    s, t, sleeps = source(response)
    with pytest.raises(SourceError) as err:
        s.resolve("o/r", None)
    assert err.value.kind == kind and len(t.calls) == 1 and not sleeps


def test_transient_errors_are_retried_with_bounded_exponential_backoff_then_succeed():
    s, t, sleeps = source(
        status(502),
        TransportError("reset"),
        status(503),
        ok({"default_branch": "main"}),
        ok(SHA.encode()),
        backoff_seconds=1.0,
    )
    assert s.resolve("o/r", None) == SHA
    assert sleeps == [1.0, 2.0, 4.0] and len(t.calls) == 5


def test_gives_up_after_the_attempt_limit():
    s, t, sleeps = source(status(500), status(500), status(500), max_attempts=3)
    with pytest.raises(SourceError) as err:
        s.resolve("o/r", None)
    assert err.value.kind == Cause.FETCH_FAILED and len(t.calls) == 3 and len(sleeps) == 2


def test_rate_limit_honours_retry_after_up_to_a_cap():
    s, _, sleeps = source(status(429, **{"retry-after": "3"}), ok({"default_branch": "main"}), ok(SHA.encode()))
    s.resolve("o/r", None)
    assert sleeps == [3.0]
    s2, t2, sleeps2 = source(status(403, **{"x-ratelimit-remaining": "0", "retry-after": "999"}))
    with pytest.raises(SourceError) as err:
        s2.resolve("o/r", None)
    assert err.value.kind == Cause.RATE_LIMITED and not sleeps2 and len(t2.calls) == 1


def test_request_budget_is_enforced():
    s, _, _ = source(ok({"default_branch": "main"}), ok(SHA.encode()), max_requests=1)
    with pytest.raises(SourceError) as err:
        s.resolve("o/r", None)
    assert err.value.kind == Cause.BUDGET_EXHAUSTED


def test_malformed_payloads_are_fetch_failures():
    for body in (b"not json", b"[]", b'{"nope": 1}'):
        s, _, _ = source(ok(body))
        with pytest.raises(SourceError) as err:
            s.resolve("o/r", None)
        assert err.value.kind == Cause.FETCH_FAILED


def test_tree_listing_keeps_blobs_and_trees_sorted_and_reports_truncation():
    payload = {"sha": SHA, "truncated": True, "tree": [
        {"path": "b.md", "type": "blob", "sha": "1" * 40, "size": 3},
        {"path": "a", "type": "tree", "sha": "2" * 40},
        {"path": "sub", "type": "commit", "sha": "3" * 40},
    ]}  # fmt: skip
    s, _, _ = source(ok(payload))
    tree = s.tree("o/r", SHA)
    assert [(e.path, e.type) for e in tree.entries] == [("a", "tree"), ("b.md", "blob")] and tree.truncated


def test_blob_is_decoded_and_checked_against_its_git_id():
    data = b"hello\n"
    entry = TreeEntry("f", "blob", 6, git_blob_sha(data))
    body = {"encoding": "base64", "content": base64.encodebytes(data).decode()}
    s, _, _ = source(ok(body), ok(body))
    assert s.read("o/r", SHA, entry) == data
    with pytest.raises(SourceError) as err:
        s.read("o/r", SHA, TreeEntry("f", "blob", 6, "0" * 40))
    assert err.value.kind == Cause.INTEGRITY


def test_redirects_never_leave_the_api_host_so_a_token_cannot_leak():
    import urllib.request

    handler = gh._ApiOnlyRedirects()
    req = urllib.request.Request("https://api.github.com/repos/o/r", headers={"Authorization": "Bearer secret"})
    followed = handler.redirect_request(req, None, 301, "Moved", {}, "https://api.github.com/repositories/42")
    assert followed is not None and followed.full_url.endswith("/repositories/42")
    for target in ("https://evil.example/x", "http://api.github.com/x", "https://api.github.com.evil.example/x"):
        assert handler.redirect_request(req, None, 302, "Found", {}, target) is None


def test_non_https_transport_is_rejected():
    with pytest.raises(TransportError):
        gh.urllib_transport("http://api.github.com/x", {}, 1)


# ---- cache -------------------------------------------------------------------------------------------------------


def world():
    return MemorySource({"o/r": {"a.md": "A", "d/b.md": "B"}})


def test_cache_is_read_through_and_serves_the_second_read_without_the_inner_source(tmp_path):
    inner = world()
    cache = CachingSource(inner, tmp_path)
    sha = inner.sha_of("o/r")
    tree = cache.tree("o/r", sha)
    entry = next(e for e in tree.entries if e.path == "a.md")
    assert cache.read("o/r", sha, entry) == b"A"
    inner.repos.clear()
    assert cache.read("o/r", sha, entry) == b"A" and cache.tree("o/r", sha).entries == tree.entries
    assert cache.hits == 2 and cache.misses == 2
    assert (tmp_path / "o" / "r" / sha / "files" / "a.md").read_bytes() == b"A"


def test_offline_cache_reports_what_is_missing_and_refuses_moving_refs(tmp_path):
    cache = CachingSource(None, tmp_path)
    with pytest.raises(SourceError) as moving:
        cache.resolve("o/r", None)
    assert moving.value.kind == Cause.NOT_CACHED and cache.resolve("o/r", SHA) == SHA
    with pytest.raises(SourceError) as miss:
        cache.tree("o/r", SHA)
    assert miss.value.kind == Cause.NOT_CACHED


def test_a_corrupted_cache_entry_is_never_trusted(tmp_path):
    inner = world()
    cache = CachingSource(inner, tmp_path)
    sha = inner.sha_of("o/r")
    entry = next(e for e in cache.tree("o/r", sha).entries if e.path == "a.md")
    cache.read("o/r", sha, entry)
    (tmp_path / "o" / "r" / sha / "files" / "a.md").write_bytes(b"tampered")
    assert cache.read("o/r", sha, entry) == b"A"  # refetched and repaired
    offline = CachingSource(None, tmp_path)
    (tmp_path / "o" / "r" / sha / "files" / "a.md").write_bytes(b"tampered")
    with pytest.raises(SourceError) as err:
        offline.read("o/r", sha, entry)
    assert err.value.kind == Cause.INTEGRITY


def test_unsafe_paths_and_keys_never_touch_the_filesystem(tmp_path):
    cache = CachingSource(world(), tmp_path)
    for path in ("../x", "/abs", "a/../../b", "a\\b", ""):
        assert not safe_relative_path(path)
        with pytest.raises(SourceError):
            cache.read("o/r", MemorySource.sha_of("o/r"), TreeEntry(path, "blob", 1, "0" * 40))
    with pytest.raises(SourceError):
        cache.tree("../evil/r", SHA)
    assert list(tmp_path.iterdir()) == []


# ---- snapshot ------------------------------------------------------------------------------------------------------


def snap(files, requests, limits=None, **kw):
    limits = limits or Limits()
    return collect(MemorySource({"o/r": files}, **kw), [RepoTarget("r", "o/r", None)], requests, limits)


def test_snapshot_distinguishes_missing_from_not_requested_and_directories():
    s = snap(
        {"a.md": "A", "d/b.md": "B"},
        [ArtifactRequest("r", "a.md"), ArtifactRequest("r", "nope.md"), ArtifactRequest("r", "d")],
    )
    assert isinstance(s.artifact("r", "a.md"), Artifact)
    assert s.artifact("r", "nope.md").cause == Cause.NOT_FOUND  # type: ignore[union-attr]
    assert s.artifact("r", "d").cause == Cause.NOT_FOUND  # a directory is not a file  # type: ignore[union-attr]
    assert s.artifact("r", "d/b.md").cause == Cause.NOT_COLLECTED  # type: ignore[union-attr]


def test_snapshot_limits_are_enforced_without_raising():
    s = snap(
        {"big.md": "x" * 50, "a.md": "A", "b.md": "B"},
        [ArtifactRequest("r", p) for p in ("big.md", "a.md", "b.md")],
        Limits(max_file_bytes=10),
    )
    assert s.artifact("r", "big.md").cause == Cause.TOO_LARGE  # type: ignore[union-attr]
    s = snap(
        {"a.md": "AAAA", "b.md": "BBBB"},
        [ArtifactRequest("r", "a.md"), ArtifactRequest("r", "b.md")],
        Limits(max_total_bytes=4),
    )
    assert isinstance(s.artifact("r", "a.md"), Artifact) and s.artifact("r", "b.md").cause == Cause.BUDGET_EXHAUSTED  # type: ignore[union-attr]
    s = snap(
        {"a.md": "A", "b.md": "B"},
        [ArtifactRequest("r", "a.md"), ArtifactRequest("r", "b.md")],
        Limits(max_files_per_repo=1),
    )
    assert s.artifact("r", "b.md").cause == Cause.BUDGET_EXHAUSTED  # type: ignore[union-attr]


def test_failures_on_one_read_do_not_fail_the_collection():
    class Flaky(MemorySource):
        def read(self, slug, sha, entry):
            if entry.path == "bad.md":
                raise SourceError(Cause.PERMISSION_DENIED, "403")
            return super().read(slug, sha, entry)

    s = collect(
        Flaky({"o/r": {"bad.md": "x", "ok.md": "y"}}),
        [RepoTarget("r", "o/r", None)],
        [ArtifactRequest("r", "bad.md"), ArtifactRequest("r", "ok.md")],
    )
    assert s.artifact("r", "bad.md").cause == Cause.PERMISSION_DENIED and isinstance(s.artifact("r", "ok.md"), Artifact)  # type: ignore[union-attr]


def test_repository_failure_is_recorded_for_every_artifact_and_revision():
    s = collect(MemorySource({}), [RepoTarget("r", "o/gone", None)], [ArtifactRequest("r", "a.md")])
    assert s.artifact("r", "a.md").cause == Cause.REPOSITORY_NOT_FOUND  # type: ignore[union-attr]
    assert isinstance(s.revision("r"), Missing) and isinstance(s.entries("r"), Missing)


def test_glob_expansion_is_segment_aware_and_bounded():
    assert glob_regex("apps/*/service.yaml").match("apps/x/service.yaml")
    assert not glob_regex("apps/*/service.yaml").match("apps/x/y/service.yaml")
    assert glob_regex("apps/**/s.yaml").match("apps/x/y/s.yaml") and glob_regex("**/s.yaml").match("s.yaml")
    files = {f"k8s/{i}.yaml": "x" for i in range(5)}
    s = snap(files, [ArtifactRequest("r", glob="k8s/*.yaml")])
    assert len(s.glob("r", "k8s/*.yaml")) == 5  # type: ignore[arg-type]
    s = snap(files, [ArtifactRequest("r", glob="k8s/*.yaml")], Limits(max_glob_matches=2))
    assert s.glob("r", "k8s/*.yaml").cause == Cause.TOO_LARGE  # type: ignore[union-attr]
    assert s.glob("r", "other/*").cause == Cause.NOT_COLLECTED  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        ArtifactRequest("r")


def test_truncated_tree_makes_listings_and_absence_unusable():
    class Truncated(MemorySource):
        def tree(self, slug, sha):
            t = super().tree(slug, sha)
            return type(t)(t.sha, t.entries, True)

    s = collect(
        Truncated({"o/r": {"a.md": "A"}}),
        [RepoTarget("r", "o/r", None)],
        [ArtifactRequest("r", "a.md"), ArtifactRequest("r", "z.md")],
    )
    assert s.entries("r", "").cause == Cause.TREE_TRUNCATED  # type: ignore[union-attr]
    assert s.artifact("r", "z.md").cause == Cause.TREE_TRUNCATED  # absence is unprovable  # type: ignore[union-attr]


# ---- overlay ---------------------------------------------------------------------------------------------------------


def test_overlay_applies_mutations_in_memory_with_consistent_blob_ids():
    base = MemorySource({"o/r": {"a.md": "one two", "d/x.md": "X", "d/y.md": "Y"}})
    sha = base.sha_of("o/r")
    ov = OverlaySource(
        base,
        [
            replace_text("o/r", "a.md", "two", "2"),
            add_file("o/r", "n/dir/f.md", "N"),
            rename_path("o/r", "d", "e"),
            delete_path("o/r", "e/y.md"),
        ],
    )
    tree = {e.path: e for e in ov.tree("o/r", sha).entries}
    assert set(tree) == {"a.md", "e", "e/x.md", "n", "n/dir", "n/dir/f.md"}
    assert ov.read("o/r", sha, tree["a.md"]) == b"one 2" and git_blob_sha(b"one 2") == tree["a.md"].blob_sha
    assert ov.read("o/r", sha, tree["e/x.md"]) == b"X"  # renamed entries read their original content
    assert tree["n"].type == "tree" and base.repos["o/r"]["a.md"] == b"one two"  # the base is untouched
    assert (
        ov.digest
        == OverlaySource(
            base,
            [
                replace_text("o/r", "a.md", "two", "2"),
                add_file("o/r", "n/dir/f.md", "N"),
                rename_path("o/r", "d", "e"),
                delete_path("o/r", "e/y.md"),
            ],
        ).digest
    )


def test_overlay_rejects_mutations_that_do_not_apply():
    base = MemorySource({"o/r": {"a.md": "x"}})
    sha = base.sha_of("o/r")
    for mutation in (
        replace_text("o/r", "a.md", "absent", "y"),
        replace_text("o/r", "zzz", "a", "b"),
        rename_path("o/r", "zzz", "q"),
    ):
        with pytest.raises(SourceError):
            OverlaySource(base, [mutation]).tree("o/r", sha)
