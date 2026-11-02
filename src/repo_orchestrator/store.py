"""Run store: one local SQLite file (ADR-0008). Run records, finding fingerprints and history.

Persistence is idempotent: a run is keyed by the digest of its inputs *and* its normalized result, so recording the same
evaluation twice changes nothing but an execution counter. A finding is resolved only when the same subject is evaluated
and found consistent; ``cannot_evaluate`` never resolves anything.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Outcome, Status, digest, to_jsonable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_key TEXT PRIMARY KEY, inputs_digest TEXT NOT NULL, result_digest TEXT NOT NULL, run_status TEXT NOT NULL,
  as_of TEXT NOT NULL, tool_version TEXT NOT NULL, declarations_digest TEXT NOT NULL, revisions_json TEXT NOT NULL,
  policies_json TEXT NOT NULL, summary_json TEXT NOT NULL,
  first_executed_at TEXT NOT NULL, last_executed_at TEXT NOT NULL, executions INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_inputs ON runs(inputs_digest);
CREATE TABLE IF NOT EXISTS outcomes (
  run_key TEXT NOT NULL REFERENCES runs(run_key), ordinal INTEGER NOT NULL, policy_id TEXT NOT NULL,
  relationship_id TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT, body_json TEXT NOT NULL,
  PRIMARY KEY (run_key, ordinal)
);
CREATE TABLE IF NOT EXISTS findings (
  fingerprint TEXT PRIMARY KEY, policy_id TEXT NOT NULL, relationship_id TEXT NOT NULL, subject TEXT NOT NULL,
  state TEXT NOT NULL, first_run TEXT NOT NULL, last_seen_run TEXT NOT NULL, last_evaluated_run TEXT NOT NULL,
  regression_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finding_events (
  fingerprint TEXT NOT NULL, run_key TEXT NOT NULL REFERENCES runs(run_key), event TEXT NOT NULL,
  PRIMARY KEY (fingerprint, run_key)
);
"""

_LIVE = {Status.INCONSISTENT: "open", Status.EXCEPTED: "excepted", Status.POLICY_CONFLICT: "conflict"}


@dataclass
class RecordResult:
    run_key: str
    new: bool
    transitions: dict[str, list[str]] = field(default_factory=dict)  # event → fingerprints


class RunStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- writing -----------------------------------------------------------------------------------------
    def record(
        self, semantic: Mapping[str, Any], outcomes: Sequence[Outcome], now: datetime | None = None
    ) -> RecordResult:
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        inputs_digest = digest(semantic["inputs"])
        run_key = digest([inputs_digest, semantic["result_digest"]])[:32]
        with self._db:
            row = self._db.execute("SELECT executions FROM runs WHERE run_key = ?", (run_key,)).fetchone()
            if row is not None:
                self._db.execute(
                    "UPDATE runs SET executions = executions + 1, last_executed_at = ? WHERE run_key = ?",
                    (stamp, run_key),
                )
                return RecordResult(run_key, False, self._events(run_key))
            inputs = semantic["inputs"]
            self._db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_key, inputs_digest, semantic["result_digest"], semantic["run_status"], inputs["as_of"], semantic["tool"]["version"],
                 inputs["declarations_digest"], json.dumps(inputs["revisions"], sort_keys=True), json.dumps(inputs["policies"], sort_keys=True),
                 json.dumps(semantic["summary"], sort_keys=True), stamp, stamp, 1),
            )  # fmt: skip
            for i, o in enumerate(outcomes):
                self._db.execute(
                    "INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run_key,
                        i,
                        o.policy_id,
                        o.relationship_id,
                        o.subject,
                        o.status.value,
                        o.fingerprint,
                        json.dumps(to_jsonable(o), sort_keys=True),
                    ),
                )
            self._transition(run_key, outcomes)
        return RecordResult(run_key, True, self._events(run_key))

    def _transition(self, run_key: str, outcomes: Sequence[Outcome]) -> None:
        live = {o.fingerprint: o for o in outcomes if o.status in _LIVE and o.fingerprint}
        for fp, o in sorted(live.items()):
            state = _LIVE[o.status]
            row = self._db.execute("SELECT state FROM findings WHERE fingerprint = ?", (fp,)).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,0)",
                    (fp, o.policy_id, o.relationship_id, o.subject, state, run_key, run_key, run_key),
                )
                event = "new"
            elif row["state"] == "resolved":
                self._db.execute(
                    "UPDATE findings SET state=?, last_seen_run=?, last_evaluated_run=?, regression_count=regression_count+1 WHERE fingerprint=?",
                    (state, run_key, run_key, fp),
                )
                event = "regressed"
            else:
                changed = row["state"] != state
                self._db.execute(
                    "UPDATE findings SET state=?, last_seen_run=?, last_evaluated_run=? WHERE fingerprint=?",
                    (state, run_key, run_key, fp),
                )
                event = "state_changed" if changed else "unchanged"
            self._db.execute("INSERT INTO finding_events VALUES (?,?,?)", (fp, run_key, event))
        by_subject: dict[tuple[str, str, str], set[Status]] = {}
        for o in outcomes:
            by_subject.setdefault(o.subject_key, set()).add(o.status)
        for row in self._db.execute("SELECT * FROM findings WHERE state != 'resolved'").fetchall():
            if row["fingerprint"] in live:
                continue
            statuses = by_subject.get((row["policy_id"], row["relationship_id"], row["subject"]), set())
            if Status.CONSISTENT in statuses or statuses & set(_LIVE):
                # the subject was evaluated and this fingerprint is no longer present
                self._db.execute(
                    "UPDATE findings SET state='resolved', last_evaluated_run=? WHERE fingerprint=?",
                    (run_key, row["fingerprint"]),
                )
                event = "resolved"
            else:
                event = "not_evaluated"  # cannot_evaluate / failed / clarification / not run: nothing is reclassified
            self._db.execute("INSERT INTO finding_events VALUES (?,?,?)", (row["fingerprint"], run_key, event))

    # -- reading -----------------------------------------------------------------------------------------
    def _events(self, run_key: str) -> dict[str, list[str]]:
        events: dict[str, list[str]] = {}
        for row in self._db.execute(
            "SELECT fingerprint, event FROM finding_events WHERE run_key = ? ORDER BY fingerprint", (run_key,)
        ):
            events.setdefault(row["event"], []).append(row["fingerprint"])
        return events

    def finding(self, fingerprint: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM findings WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def findings(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute("SELECT * FROM findings ORDER BY fingerprint")]

    def history(self, fingerprint: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT e.event, e.run_key, r.first_executed_at FROM finding_events e JOIN runs r USING (run_key) "
            "WHERE e.fingerprint = ? ORDER BY r.first_executed_at, e.run_key", (fingerprint,),
        )  # fmt: skip
        return [dict(r) for r in rows]

    def run(self, run_key: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM runs WHERE run_key = ?", (run_key,)).fetchone()
        return dict(row) if row else None

    def run_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    def divergent_complete_runs(self, inputs_digest: str) -> list[str]:
        """Complete runs with identical inputs but different results: a determinism violation."""
        rows = self._db.execute(
            "SELECT DISTINCT result_digest FROM runs WHERE inputs_digest = ? AND run_status = 'complete'",
            (inputs_digest,),
        ).fetchall()
        return [r[0] for r in rows] if len(rows) > 1 else []
