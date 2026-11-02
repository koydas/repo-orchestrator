"""Experiment F: deterministic replay with scripted agents; nondeterministic fields are excluded from comparison."""
import socket

from s0_spike.models import normalize_report

from conftest import cli


def test_fresh_runs_are_semantically_identical(make_orch):
    runs = []
    for i in range(3):
        o = make_orch(name=f"s{i}")
        st = o.start("r1", "baseline")
        runs.append((st["report"], st["meta"]))
    reports, metas = zip(*runs)
    assert len({r["digest"] for r in reports}) == 1
    assert all(normalize_report(r) == normalize_report(reports[0]) for r in reports)
    assert len({m["nonce"] for m in metas}) == 3 and len({m["started_at"] for m in metas}) == 3  # excluded on purpose


def test_classification_is_consistent_across_runs(make_orch):
    seen = []
    for i in range(3):
        rep = make_orch(name=f"s{i}").start("r1", "baseline")["report"]
        seen.append([(f["candidate_id"], f["outcome"], f["reason"], f["attempt"]) for f in rep["findings"]])
    assert seen[0] == seen[1] == seen[2]
    assert dict((c, o) for c, o, _, _ in seen[0]) == {
        "c01": "verified", "c02": "verified", "c03": "supported", "c04": "insufficient_evidence", "c05": "failed",
        "c06": "verified", "c07": "insufficient_evidence", "c08": "verified", "c09": "insufficient_evidence"}


def test_replay_makes_no_network_calls(orch, monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    st = orch.start("r1", "baseline")
    assert st["phase"] == "awaiting_approval"


def test_report_digest_equal_across_processes(tmp_path, orch):
    _, st = cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "baseline")
    assert st["report"]["digest"] == orch.start("r1", "baseline")["report"]["digest"]
