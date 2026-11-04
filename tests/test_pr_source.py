"""C1 pull-request boundary: the GET-only client, validation of what it returns, and the offline record format."""

from __future__ import annotations

import json

import pytest

from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.collector.github import GitHubSource, Response
from repo_orchestrator.collector.pr import (
    FIXTURE_SCHEMA_VERSION,
    MAX_FILES,
    ChangedFile,
    PullRequest,
    fixture_json,
    parse_fixture,
    parse_pull_request,
)
from repo_orchestrator.collector.source import SourceError

BASE, HEAD = "b" * 40, "c" * 40


def meta(**kw):
    body = {
        "state": "open", "merged": False, "changed_files": 2,
        "base": {"sha": BASE, "ref": "main"}, "head": {"sha": HEAD, "ref": "feature", "repo": {"full_name": "o/r"}},
    }  # fmt: skip
    body.update(kw)
    return body


def file(path="a.txt", status="modified", **kw):
    return {"filename": path, "status": status, **kw}


class Script:
    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        return self.items.pop(0)


def ok(body):
    return Response(200, {}, json.dumps(body).encode())


def source(*items, **kw):
    t = Script(*items)
    return GitHubSource(transport=t, sleep=lambda _s: None, **kw), t


class TestGitHubPullRequests:
    def test_reads_the_pull_request_and_its_files_with_get_requests_only(self):
        s, t = source(ok(meta()), ok([file("a.txt"), file("b/c.md", "added")]), ok([]))
        pr = s.pull_request("o/r", 7)
        assert (pr.base_sha, pr.head_sha, pr.changed_files, pr.files_complete) == (BASE, HEAD, 2, True)
        assert [f.path for f in pr.files] == ["a.txt", "b/c.md"]
        assert [u.rsplit("/repos/", 1)[1] for u, _ in t.calls] == [
            "o/r/pulls/7",
            "o/r/pulls/7/files?per_page=100&page=1",
        ]
        assert all("Authorization" not in h for _, h in t.calls)

    def test_pages_until_the_declared_count_is_reached(self):
        page1 = [file(f"f{i}.txt") for i in range(100)]
        page2 = [file(f"g{i}.txt") for i in range(50)]
        s, t = source(ok(meta(changed_files=150)), ok(page1), ok(page2))
        pr = s.pull_request("o/r", 1)
        assert len(pr.files) == 150 and pr.files_complete
        assert t.calls[-1][0].endswith("page=2")

    def test_a_short_page_before_the_declared_count_leaves_the_list_incomplete(self):
        s, _ = source(ok(meta(changed_files=5)), ok([file("a.txt")]))
        pr = s.pull_request("o/r", 1)
        assert len(pr.files) == 1 and not pr.files_complete

    def test_a_pull_request_at_the_three_thousand_file_cap_is_never_complete(self):
        pr = PullRequest(
            "o/r",
            1,
            "open",
            False,
            BASE,
            HEAD,
            "o/r",
            "main",
            "f",
            MAX_FILES,
            tuple(ChangedFile(f"f{i}", "added") for i in range(MAX_FILES)),
        )
        assert not pr.files_complete  # GitHub stops listing at 3000: the real change may be larger

    def test_a_missing_pull_request_is_pull_request_not_found(self):
        s, _ = source(Response(404, {}, b"{}"))
        with pytest.raises(SourceError) as exc:
            s.pull_request("o/r", 99)
        assert exc.value.kind == "pull_request_not_found"

    def test_the_request_budget_covers_pull_request_calls(self):
        s, _ = source(ok(meta()), max_requests=1)
        with pytest.raises(SourceError) as exc:
            s.pull_request("o/r", 1)
        assert exc.value.kind == "api_budget_exhausted"

    @pytest.mark.parametrize(
        "bad",
        [
            meta(base={"sha": "short", "ref": "main"}),
            meta(head={"ref": "x"}),
            meta(changed_files="2"),
            meta(changed_files=True),
            meta(changed_files=-1),
            "not an object",
        ],
    )
    def test_malformed_pull_request_payloads_fail_closed(self, bad):
        with pytest.raises(SourceError) as exc:
            parse_pull_request("o/r", 1, bad, [])
        assert exc.value.kind == "fetch_failed"

    @pytest.mark.parametrize(
        "bad",
        [
            file("../etc/passwd"),
            file("/abs"),
            file("a\\b"),
            file("ok", "exploded"),
            file("ok", previous_filename="../x"),
            "x",
            {"status": "added"},
        ],
    )
    def test_malformed_file_entries_fail_closed(self, bad):
        with pytest.raises(SourceError) as exc:
            parse_pull_request("o/r", 1, meta(changed_files=1), [bad])
        assert exc.value.kind == "fetch_failed"

    def test_a_deleted_fork_is_read_through_the_base_repository(self):
        pr = parse_pull_request("o/r", 1, meta(head={"sha": HEAD, "ref": "x", "repo": None}), [file(), file("b")])
        assert pr.head_slug == "o/r"

    def test_a_fork_head_keeps_its_slug_for_the_report(self):
        pr = parse_pull_request(
            "o/r", 1, meta(head={"sha": HEAD, "ref": "x", "repo": {"full_name": "fork/r"}}), [file(), file("b")]
        )
        assert pr.head_slug == "fork/r"

    def test_renames_carry_the_previous_name(self):
        pr = parse_pull_request(
            "o/r", 1, meta(changed_files=1), [file("new.md", "renamed", previous_filename="old.md")]
        )
        assert pr.files == (ChangedFile("new.md", "renamed", "old.md"),)

    def test_the_files_digest_ignores_listing_order(self):
        a = parse_pull_request("o/r", 1, meta(), [file("a"), file("b")])
        b = parse_pull_request("o/r", 1, meta(), [file("b"), file("a")])
        assert a.files_digest == b.files_digest


class TestOfflineRecords:
    def pr(self, head=HEAD, files=None):
        return parse_pull_request(
            "o/r", 7, meta(head={"sha": head, "ref": "f", "repo": {"full_name": "o/r"}}), files or [file(), file("b")]
        )

    def test_a_live_answer_is_recorded_under_its_head_sha_and_replays_offline(self, tmp_path):
        s, _ = source(ok(meta()), ok([file(), file("b")]))
        live = CachingSource(s, tmp_path).pull_request("o/r", 7)
        recorded = tmp_path / "o" / "r" / "pulls" / "7" / f"{HEAD}.{BASE}.json"
        assert recorded.is_file()
        assert CachingSource(None, tmp_path).pull_request("o/r", 7) == live

    def test_a_live_pull_request_is_never_served_from_the_cache(self, tmp_path):
        """A PR is mutable: the live source is asked every time, the record is only for offline replay."""
        s, t = source(ok(meta()), ok([file(), file("b")]), ok(meta()), ok([file(), file("b")]))
        c = CachingSource(s, tmp_path)
        c.pull_request("o/r", 7)
        c.pull_request("o/r", 7)
        assert len(t.calls) == 4

    def test_several_recorded_heads_need_an_explicit_choice(self, tmp_path):
        d = tmp_path / "o" / "r" / "pulls" / "7"
        d.mkdir(parents=True)
        for head in (HEAD, "d" * 40):
            (d / f"{head}.{BASE}.json").write_text(fixture_json(self.pr(head)))
        offline = CachingSource(None, tmp_path)
        with pytest.raises(SourceError, match="several recorded inputs"):
            offline.pull_request("o/r", 7)
        assert offline.pull_request("o/r", 7, "d" * 40).head_sha == "d" * 40
        with pytest.raises(SourceError) as exc:
            offline.pull_request("o/r", 7, "e" * 40)
        assert exc.value.kind == "not_cached_offline"

    def test_an_unrecorded_pull_request_is_not_cached_never_empty(self, tmp_path):
        with pytest.raises(SourceError) as exc:
            CachingSource(None, tmp_path).pull_request("o/r", 7)
        assert exc.value.kind == "not_cached_offline"

    def test_the_record_must_describe_the_pull_request_it_is_filed_under(self, tmp_path):
        d = tmp_path / "o" / "r" / "pulls" / "8"
        d.mkdir(parents=True)
        (d / f"{HEAD}.{BASE}.json").write_text(fixture_json(self.pr()))  # a record of #7 filed as #8
        with pytest.raises(SourceError) as exc:
            CachingSource(None, tmp_path).pull_request("o/r", 8)
        assert exc.value.kind == "integrity_mismatch"

    def test_the_file_name_must_be_the_recorded_head(self, tmp_path):
        d = tmp_path / "o" / "r" / "pulls" / "7"
        d.mkdir(parents=True)
        (d / f"{'d' * 40}.{BASE}.json").write_text(fixture_json(self.pr()))
        with pytest.raises(SourceError) as exc:
            CachingSource(None, tmp_path).pull_request("o/r", 7)
        assert exc.value.kind == "integrity_mismatch"

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.update(schema_version=FIXTURE_SCHEMA_VERSION + 1),
            lambda d: d.update(kind="commit"),
            lambda d: d.update(files="nope"),
            lambda d: d.pop("changed_files"),
            lambda d: d["head"].update(sha="zz"),
        ],
    )
    def test_unsupported_or_damaged_records_fail_closed(self, mutate):
        data = json.loads(fixture_json(self.pr()))
        mutate(data)
        with pytest.raises(SourceError):
            parse_fixture("o/r", 7, json.dumps(data))

    def test_records_that_are_not_json_fail_closed(self):
        with pytest.raises(SourceError):
            parse_fixture("o/r", 7, "{broken")

    def test_unsafe_keys_never_touch_the_filesystem(self, tmp_path):
        offline = CachingSource(None, tmp_path)
        for slug, number in (("../x/y", 1), ("o/..", 1), ("o/r", 0)):
            with pytest.raises(SourceError):
                offline.pull_request(slug, number)

    def test_the_record_format_round_trips(self):
        pr = self.pr(files=[file("n", "renamed", previous_filename="o"), file("m")])
        assert parse_fixture("o/r", 7, fixture_json(pr, synthetic="x")) == pr

    def test_a_source_without_pull_request_support_is_refused(self, tmp_path):
        class Plain:
            def resolve(self, slug, ref):
                return HEAD

        with pytest.raises(SourceError):
            CachingSource(Plain(), tmp_path).pull_request("o/r", 1)  # type: ignore[arg-type]


class TestHeadAndBase:
    """The same head can be analyzed against another base (the base branch moved): both inputs must survive."""

    def record(self, root, base):
        pr = parse_pull_request("o/r", 7, meta(base={"sha": base, "ref": "main"}), [file(), file("b")])
        d = root / "o" / "r" / "pulls" / "7"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{HEAD}.{base}.json").write_text(fixture_json(pr))
        return pr

    def test_a_moved_base_does_not_overwrite_the_earlier_record(self, tmp_path):
        other = "e" * 40
        for base in (BASE, other):
            s, _ = source(ok(meta(base={"sha": base, "ref": "main"})), ok([file(), file("b")]))
            CachingSource(s, tmp_path).pull_request("o/r", 7)
        names = sorted(p.name for p in (tmp_path / "o" / "r" / "pulls" / "7").iterdir())
        assert names == sorted([f"{HEAD}.{BASE}.json", f"{HEAD}.{other}.json"])

    def test_both_inputs_replay_offline_when_chosen_by_base(self, tmp_path):
        other = "e" * 40
        first, second = self.record(tmp_path, BASE), self.record(tmp_path, other)
        offline = CachingSource(None, tmp_path)
        with pytest.raises(SourceError, match="several recorded inputs"):
            offline.pull_request("o/r", 7, HEAD)  # the head alone is ambiguous now
        assert offline.pull_request("o/r", 7, HEAD, BASE) == first
        assert offline.pull_request("o/r", 7, base_sha=other) == second
        assert first.base_sha != second.base_sha

    def test_an_invalid_base_selector_is_refused(self, tmp_path):
        with pytest.raises(SourceError):
            CachingSource(None, tmp_path).pull_request("o/r", 7, base_sha="nope")
