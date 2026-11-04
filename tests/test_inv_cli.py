"""`scan --investigate`, `--replay`, `--record` and `investigations ...` through the real CLI entry point, offline."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from repo_orchestrator import cli
from repo_orchestrator.investigation.provider import LLMRequest
from support import FIXTURES, ROOT
from support_inv import scripted_baseline, subject_of
from tools.record_investigation import OUT as RECORDING
from tools.record_investigation import build

BASE = ["scan", "--revisions", "baseline", "--offline", "--cache-dir", str(FIXTURES), "--as-of", "2026-11-02"]
KEY = "sk-ant-api03-SECRET-must-never-appear-anywhere"


def run(tmp: Path, *extra: str, capsys) -> tuple[int, str, str]:
    code = cli.main([*BASE, "--output-dir", str(tmp / "out"), "--state-db", str(tmp / "state.sqlite"), *extra])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_the_default_scan_is_untouched_by_m3(tmp_path, capsys):
    code, out, _err = run(tmp_path, "--no-store", capsys=capsys)
    assert code == 1 and "investigation" not in out
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    assert "investigation" not in body["semantic"] and "deterministic_digest" not in body["semantic"]
    assert "investigation" not in body["execution"]
    assert "Bounded investigation" not in (tmp_path / "out" / "report.md").read_text()


def test_the_committed_recording_matches_what_the_scripted_investigator_asks_today():
    """If a prompt, the tool protocol or a fixture changes, regenerate: python tests/tools/record_investigation.py"""
    assert json.loads(RECORDING.read_text())["entries"].keys() == json.loads(build())["entries"].keys(), (
        "stale recording: run `python tests/tools/record_investigation.py` and review the diff"
    )


def test_replay_runs_the_whole_investigation_offline_without_a_key(tmp_path, capsys):
    code, out, _err = run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    assert code == 1, out  # M1's two baseline findings outrank a pending human validation
    assert "investigation awaiting_validation" in out and "NEEDS_VALIDATION R1.ap claim:extracted_from" in out
    assert "POLICY_QUESTION R5.chat intent:P6/plugin_keys" in out and "exit code 1 (complete, findings detected)" in out
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    inv = body["semantic"]["investigation"]
    assert inv["summary"]["by_state"]["needs_validation"] == 1 and inv["summary"]["by_state"]["policy_question"] == 2
    assert (
        body["execution"]["investigation"]["mode"] == "replay"
        and body["execution"]["investigation"]["usage"]["replayed_calls"] == 3
    )
    md = (tmp_path / "out" / "report.md").read_text()
    assert (
        "## Bounded investigation (M3, reasoning-assisted)" in md
        and "Needs validation (verified quotes, a human decides)" in md
    )
    assert md.startswith("# Cross-repository coherence report") and "UNVERIFIED" not in md


def test_a_repeated_command_is_idempotent_and_reports_the_same_digest(tmp_path, capsys):
    run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    first = json.loads((tmp_path / "out" / "report.json").read_text())["semantic"]["result_digest"]
    run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    assert json.loads((tmp_path / "out" / "report.json").read_text())["semantic"]["result_digest"] == first


def test_investigate_without_a_key_or_a_recording_fails_closed_with_exit_3_and_no_report(tmp_path, capsys):
    code, _out, err = run(tmp_path, "--no-store", "--investigate", capsys=capsys)
    assert code == 3 and "ANTHROPIC_API_KEY" in err and "--replay" in err
    assert not (tmp_path / "out" / "report.json").exists()


@pytest.mark.parametrize("flags", [["--replay", "x.json"], ["--record", "x.json"]])
def test_replay_and_record_need_investigate(tmp_path, capsys, flags):
    code, _out, err = run(tmp_path, "--no-store", *flags, capsys=capsys)
    assert code == 3 and "need --investigate" in err


def test_replay_and_record_are_mutually_exclusive_and_a_bad_recording_is_rejected(tmp_path, capsys):
    assert (
        run(
            tmp_path,
            "--no-store",
            "--investigate",
            "--replay",
            str(RECORDING),
            "--record",
            str(tmp_path / "r.json"),
            capsys=capsys,
        )[0]
        == 3
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    code, _out, err = run(tmp_path, "--no-store", "--investigate", "--replay", str(bad), capsys=capsys)
    assert code == 3 and "unreadable recording" in err


def test_no_store_keeps_the_investigation_state_temporary(tmp_path, capsys):
    code, out, _err = run(tmp_path, "--no-store", "--investigate", "--replay", str(RECORDING), capsys=capsys)
    assert code == 1 and "--no-store: the investigation state is temporary" in out
    assert not (tmp_path / "state.sqlite").exists() and not list(tmp_path.glob("*.investigations.sqlite"))


def test_the_investigations_commands_list_show_decide_and_approve(tmp_path, capsys):
    run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    db = ["--state-db", str(tmp_path / "state.sqlite")]
    assert cli.main(["investigations", *db, "list"]) == 0
    listing = capsys.readouterr().out
    run_id = listing.split()[0]
    assert "awaiting_approval" in listing
    assert cli.main(["investigations", *db, "show", run_id, "--json"]) == 0
    section = json.loads(capsys.readouterr().out)
    key = next(c["key"] for c in section["candidates"] if c["subject"] == "claim:extracted_from")
    # W4: a human validates the H2 claim; the next report shows confirmed / validated_by_human with the decision attached
    assert (
        cli.main(
            [
                "investigations",
                *db,
                "decide",
                run_id,
                key,
                "--action",
                "validate",
                "--reason",
                "read both READMEs",
                "--actor",
                "alice",
            ]
        )
        == 0
    )
    assert "confirmed" in capsys.readouterr().out
    _code, out, _err = run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    body = json.loads((tmp_path / "out" / "report.json").read_text())["semantic"]["investigation"]
    h2 = next(c for c in body["candidates"] if c["subject"] == "claim:extracted_from")
    assert (
        h2["state"] == "confirmed" and h2["validation"] == "validated_by_human" and h2["decision"]["actor"] == "alice"
    )
    assert body["inputs"]["decisions_digest"] and "CONFIRMED R1.ap" in out
    # approval, then a second approval is refused (exit 3)
    assert cli.main(["investigations", *db, "approve", run_id, "--reviewer", "carol"]) == 0
    assert "reviewed" in capsys.readouterr().out
    assert cli.main(["investigations", *db, "approve", run_id, "--reviewer", "carol"]) == 3
    assert "already reviewed" in capsys.readouterr().err
    # refused decisions are exit 3 with the rule named
    assert (
        cli.main(
            ["investigations", *db, "decide", run_id, key, "--action", "validate", "--reason", "again", "--actor", "a"]
        )
        == 3
    )
    assert "cannot validate" in capsys.readouterr().err
    assert cli.main(["investigations", *db, "show", "f" * 16]) == 3


def test_resume_continues_a_stored_run_offline(tmp_path, capsys):
    run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    run_id = json.loads((tmp_path / "out" / "report.json").read_text())["semantic"]["investigation"]["inputs"]["run_id"]
    assert cli.main(["investigations", "--state-db", str(tmp_path / "state.sqlite"), "resume", run_id]) == 0
    assert f"run {run_id}: awaiting_approval" in capsys.readouterr().out


# ---- the live path through real HTTP against a local fake of the Messages API --------------------------------------------


class _Api(BaseHTTPRequestHandler):
    script = scripted_baseline()
    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"key": self.headers.get("x-api-key"), "body": body})
        req = LLMRequest(
            body["model"],
            body["system"],
            tuple((m["role"], m["content"]) for m in body["messages"]),
            body["max_tokens"],
            body["output_config"]["effort"],
        )
        out = type(self).script.fn(req, 1)
        assert subject_of(req)
        raw = json.dumps(
            {
                "model": body["model"],
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": out}],
                "usage": {"input_tokens": 700, "output_tokens": 160},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_the_live_path_sends_the_key_only_in_the_header_and_never_writes_it_anywhere(tmp_path, capsys, monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    server = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Api.seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        code, out, err = run(tmp_path, "--investigate", "--record", str(tmp_path / "rec.json"), capsys=capsys)
    finally:
        server.shutdown()
        server.server_close()
    assert code == 1 and len(_Api.seen) == 3 and {s["key"] for s in _Api.seen} == {KEY}
    assert not any("temperature" in s["body"] or "tools" in s["body"] for s in _Api.seen)
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    assert (
        body["execution"]["investigation"]["mode"] == "live"
        and body["execution"]["investigation"]["usage"]["input_tokens"] == 2100
    )
    assert body["execution"]["investigation"]["usage"]["replayed_calls"] == 0
    # nothing under tmp_path, nothing printed, holds the key
    assert KEY not in out and KEY not in err
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    # the recording written by --record replays the same run offline
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    (tmp_path / "again").mkdir()
    code2, _out2, _err2 = run(
        tmp_path / "again", "--investigate", "--replay", str(tmp_path / "rec.json"), capsys=capsys
    )
    again = json.loads((tmp_path / "again" / "out" / "report.json").read_text())
    assert code2 == code and again["semantic"]["result_digest"] == body["semantic"]["result_digest"]


def test_environment_helpers_exist():
    assert os.path.isdir(ROOT / "tests" / "tools")


def test_two_state_databases_in_one_directory_never_share_checkpoints(tmp_path, capsys):
    """Same inputs give the same run id: a shared checkpoint file made the second database skip its investigation."""
    results = []
    for name in ("a.sqlite", "b.sqlite"):
        cli.main(
            [
                *BASE,
                "--output-dir",
                str(tmp_path / ("out-" + name)),
                "--state-db",
                str(tmp_path / name),
                "--investigate",
                "--replay",
                str(RECORDING),
                "--quiet",
            ]
        )
        capsys.readouterr()
        body = json.loads((tmp_path / ("out-" + name) / "report.json").read_text())["semantic"]["investigation"]
        results.append(body["summary"]["by_state"])
    assert results[0] == results[1] and results[1]["needs_validation"] == 1 and results[1]["policy_question"] == 2
    assert sorted(p.name for p in tmp_path.glob("*.investigations.sqlite")) == [
        "a.investigations.sqlite",
        "b.investigations.sqlite",
    ]


def test_a_missing_recording_is_a_concise_error_and_exit_3_never_a_traceback(tmp_path, capsys):
    code, _out, err = run(
        tmp_path, "--no-store", "--investigate", "--replay", str(tmp_path / "nope.json"), capsys=capsys
    )
    assert code == 3 and err.startswith("error:") and "Traceback" not in err


def test_a_filesystem_failure_is_exit_3_for_every_command(tmp_path, capsys):
    """Exit 1 means 'findings detected': an OSError must never be reported as one (the OSError handler comes first)."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    for extra in ([], ["--investigate", "--replay", str(RECORDING)]):
        code = cli.main([*BASE, "--no-store", "--quiet", "--output-dir", str(blocker / "sub"), *extra])
        assert code == 3
        assert "Traceback" not in capsys.readouterr().err


def test_a_stored_investigated_report_keeps_a_run_key_the_dashboard_accepts(tmp_path, capsys):
    from repo_orchestrator.dashboard import validate_report

    run(tmp_path, "--investigate", "--replay", str(RECORDING), capsys=capsys)
    body = json.loads((tmp_path / "out" / "report.json").read_text())
    assert body["execution"]["run_key"] is not None
    validate_report(body)  # the augmented identity is the one the dashboard recomputes
    assert body["execution"]["investigation"]["deterministic_run_key"] != body["execution"]["run_key"]
