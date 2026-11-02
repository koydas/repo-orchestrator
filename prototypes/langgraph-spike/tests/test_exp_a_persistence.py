"""Experiment A: durable SQLite checkpoints across real process restarts (one OS process per command)."""
from s0_spike.ledger import Ledger

from conftest import cli


def test_state_survives_process_restart_and_resume_uses_same_thread(tmp_path):
    code, st1 = cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "baseline")  # process 1 exits after pausing
    assert code == 0 and st1["phase"] == "awaiting_approval" and st1["thread_id"] == "run-a"
    code, st2 = cli(tmp_path, "status", "--run-id", "run-a")  # process 2: state reconstructed from SQLite only
    assert code == 0 and st2["phase"] == "awaiting_approval"
    for k in ("meta", "spent", "rounds", "steps", "results", "report"):
        assert st2[k] == st1[k], k
    code, st3 = cli(tmp_path, "resume", "--run-id", "run-a", "--decision", "approve", "--reviewer", "r",
                    "--report-digest", st2["pending_report_digest"])  # process 3
    assert code == 0 and st3["phase"] == "approved"
    # resume did not reset run metadata, budget counters or the report
    assert st3["meta"] == st1["meta"] and st3["spent"] == st1["spent"] == 12 and st3["rounds"] == 2
    assert st3["report"] == st1["report"]


def test_completed_stages_do_not_run_again_after_restart(tmp_path):
    cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "small")
    _, st = cli(tmp_path, "status", "--run-id", "run-a")
    cli(tmp_path, "resume", "--run-id", "run-a", "--decision", "approve", "--reviewer", "r",
        "--report-digest", st["pending_report_digest"])
    led = Ledger(tmp_path / "ledger.sqlite")
    assert sorted(k for _, k in led.calls("run-a", "agent")) == ["c01#1", "c02#1", "c03#1"]  # each ran once, in process 1
    assert led.spent("run-a") == 3
    assert len(led.calls("run-a", "publish_performed")) == 1


def test_hard_kill_mid_fanout_then_recover_in_new_process(tmp_path):
    code, _ = cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "crash_mid_fanout")
    assert code == 137  # process died inside an investigation: no exception, no cleanup
    code, mid = cli(tmp_path, "status", "--run-id", "run-a")
    assert mid["phase"] == "incomplete" and mid["report"] is None
    assert "c03#1" not in mid["results"] and len(mid["results"]) == 4  # finished siblings were checkpointed
    led = Ledger(tmp_path / "ledger.sqlite")
    before = led.calls("run-a", "agent")
    code, end = cli(tmp_path, "recover", "--run-id", "run-a")
    assert code == 0 and end["phase"] == "awaiting_approval"
    after = led.calls("run-a", "agent")
    assert after[:len(before)] == before and after[len(before):] == [("agent", "c03#1")]  # only the killed task re-ran
    assert end["spent"] == 5 == led.spent("run-a")  # re-execution of the same attempt is not a new spend
    assert end["meta"] == mid["meta"]
    assert all(f["outcome"] == "verified" for f in end["report"]["findings"])


def test_no_credentials_or_network_needed(tmp_path):
    # cli() runs with an environment holding only PATH, PYTHONPATH and HOME: no token, no API key.
    code, st = cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "small")
    assert code == 0 and st["phase"] == "awaiting_approval"
