"""Investigation state in the run store file: the budget ledger, recorded responses, results and human decisions.

All tables are prefixed ``inv_``: a separate namespace. Nothing here reads or writes ``runs``, ``outcomes``,
``findings``, ``finding_events`` or ``pr_*`` (ADR-0017 §5). Every operation opens a short transaction of its own
(``BEGIN IMMEDIATE``), so concurrent workers and a restarted process see one consistent ledger, and the budget cannot be
reset by editing a graph checkpoint (ADR-0015 §5).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .budgets import BUDGET, Budget, cost_micro_usd, estimate_input_tokens
from .provider import LLMRequest, LLMResponse, Recording

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS inv_runs(run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, inputs_digest TEXT NOT NULL,"
    " status TEXT NOT NULL, config_json TEXT NOT NULL, candidates_json TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, review_note TEXT)",
    "CREATE TABLE IF NOT EXISTS inv_slots(run_id TEXT NOT NULL, cand_key TEXT NOT NULL, PRIMARY KEY(run_id, cand_key))",
    "CREATE TABLE IF NOT EXISTS inv_calls(run_id TEXT NOT NULL, cand_key TEXT NOT NULL, request_key TEXT NOT NULL, attempt INTEGER NOT NULL, est_in INTEGER NOT NULL,"
    " max_out INTEGER NOT NULL, in_tok INTEGER, out_tok INTEGER, cost_micro INTEGER, replayed INTEGER, latency_ms INTEGER,"
    " PRIMARY KEY(run_id, cand_key, request_key, attempt))",
    "CREATE TABLE IF NOT EXISTS inv_tools(run_id TEXT NOT NULL, cand_key TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(run_id, cand_key, seq))",
    "CREATE TABLE IF NOT EXISTS inv_responses(request_key TEXT PRIMARY KEY, body_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS inv_results(run_id TEXT NOT NULL, cand_key TEXT NOT NULL, body_json TEXT NOT NULL, PRIMARY KEY(run_id, cand_key))",
    "CREATE TABLE IF NOT EXISTS inv_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, cand_key TEXT NOT NULL, evidence_digest TEXT NOT NULL,"
    " action TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, decided_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS inv_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, pid INTEGER NOT NULL)",
)
TABLES = (
    "inv_runs",
    "inv_slots",
    "inv_calls",
    "inv_tools",
    "inv_responses",
    "inv_results",
    "inv_decisions",
    "inv_events",
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class InvestigationStore:
    def __init__(self, path: Path | str, budget: Budget = BUDGET) -> None:
        self.path = str(path)
        self.budget = budget
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as c:
            for stmt in _SCHEMA:
                c.execute(stmt)

    @contextmanager
    def _tx(self) -> Any:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        with closing(conn):
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # -- runs ------------------------------------------------------------------------------------------------
    def create_run(
        self, run_id: str, inputs_digest: str, config: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO inv_runs VALUES (?,?,?,?,?,?,NULL,NULL,NULL)",
                (
                    run_id,
                    _now(),
                    inputs_digest,
                    "created",
                    json.dumps(config, sort_keys=True),
                    json.dumps(candidates, sort_keys=True),
                ),
            )
            return bool(cur.rowcount == 1)

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._tx() as c:
            row = c.execute("SELECT * FROM inv_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["config"] = json.loads(d.pop("config_json"))
        d["candidates"] = json.loads(d.pop("candidates_json"))
        return d

    def runs(self) -> list[dict[str, Any]]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT run_id, created_at, status, inputs_digest, reviewed_at FROM inv_runs ORDER BY rowid"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, run_id: str, status: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE inv_runs SET status = ? WHERE run_id = ?", (status, run_id))

    def mark_reviewed(self, run_id: str, reviewer: str, note: str | None) -> bool:
        """Idempotent: the first approval wins; a replayed finalize changes nothing."""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE inv_runs SET reviewed_by = ?, reviewed_at = ?, review_note = ?, status = 'reviewed' WHERE run_id = ? AND reviewed_at IS NULL",
                (reviewer, _now(), note, run_id),
            )
            return bool(cur.rowcount == 1)

    # -- ledger ----------------------------------------------------------------------------------------------
    def reserve_slot(self, run_id: str, cand_key: str) -> str:
        """'reserved' | 'duplicate' (same candidate re-executed after a restart: no new spend) | 'denied'."""
        with self._tx() as c:
            if c.execute("SELECT 1 FROM inv_slots WHERE run_id=? AND cand_key=?", (run_id, cand_key)).fetchone():
                return "duplicate"
            n = c.execute("SELECT COUNT(*) FROM inv_slots WHERE run_id=?", (run_id,)).fetchone()[0]
            if n >= self.budget.max_investigations:
                return "denied"
            c.execute("INSERT INTO inv_slots VALUES (?,?)", (run_id, cand_key))
            return "reserved"

    def reserve_call(self, run_id: str, cand_key: str, request: LLMRequest, cached: bool) -> tuple[str, int]:
        """Reserve the worst case of one model call before it happens: ``(status, attempt)``, status ``ok`` |
        ``duplicate`` | ``denied:<reason>``.

        A reservation belongs to the *request* (its hash), never to a position. ``cached``: an answer to this exact request
        is already known (a recording, or the stored answer of an earlier attempt). Then, if the request was ever accounted
        it is a ``duplicate`` (nothing is spent twice), otherwise it is accounted once as attempt 0. Not cached: the
        provider will be called, so a *new* attempt is reserved and checked against every budget; a reservation left
        unsettled by a worker that died mid-call keeps counting at its worst case (the call may have been made)."""
        b = self.budget
        est_in = estimate_input_tokens(request.chars)
        with self._tx() as c:
            rows = c.execute(
                "SELECT attempt, in_tok FROM inv_calls WHERE run_id=? AND cand_key=? AND request_key=?",
                (run_id, cand_key, request.key),
            ).fetchall()
            if cached and rows:
                return "duplicate", int(min(r["attempt"] for r in rows))
            attempt = len(rows)
            mine = c.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(in_tok, est_in)),0) i, COALESCE(SUM(COALESCE(out_tok, max_out)),0) o"
                " FROM inv_calls WHERE run_id=? AND cand_key=?", (run_id, cand_key),
            ).fetchone()  # fmt: skip
            if mine["n"] >= b.max_llm_calls:
                return "denied:llm_calls", attempt
            if mine["i"] + est_in > b.max_input_tokens:
                return "denied:input_tokens", attempt
            if mine["o"] + request.max_tokens > b.max_output_tokens:
                return "denied:output_tokens", attempt
            spent = c.execute(
                "SELECT est_in, max_out, in_tok, out_tok, cost_micro FROM inv_calls WHERE run_id=?", (run_id,)
            ).fetchall()
            tokens = cost = 0
            for r in spent:  # a call in flight counts at its worst case, in tokens and in money
                i = r["in_tok"] if r["in_tok"] is not None else r["est_in"]
                o = r["out_tok"] if r["out_tok"] is not None else r["max_out"]
                tokens += i + o
                cost += r["cost_micro"] if r["cost_micro"] is not None else cost_micro_usd(request.model, i, o)
            if tokens + est_in + request.max_tokens > b.max_run_tokens:
                return "denied:run_tokens", attempt
            if cost + cost_micro_usd(request.model, est_in, request.max_tokens) > b.max_run_cost_micro_usd:
                return "denied:run_cost", attempt
            c.execute(
                "INSERT INTO inv_calls VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)",
                (run_id, cand_key, request.key, attempt, est_in, request.max_tokens),
            )
            return "ok", attempt

    def settle_call(
        self, run_id: str, cand_key: str, request_key: str, attempt: int, response: LLMResponse, replayed: bool
    ) -> None:
        """Fill an unsettled reservation, once. A row already settled (or released after a failed attempt) keeps what it
        recorded: a restarted worker re-reading a stored response must not charge it a second time."""
        with self._tx() as c:
            c.execute(
                "UPDATE inv_calls SET in_tok=?, out_tok=?, cost_micro=?, replayed=?, latency_ms=? WHERE run_id=? AND cand_key=? AND request_key=? AND attempt=? AND in_tok IS NULL",
                (response.input_tokens, response.output_tokens, cost_micro_usd(response.model, response.input_tokens, response.output_tokens),
                 int(replayed), response.latency_ms, run_id, cand_key, request_key, attempt),
            )  # fmt: skip

    def release_call(self, run_id: str, cand_key: str, request_key: str, attempt: int) -> None:
        """A call that failed at the provider spent nothing measurable: count it as zero tokens but keep the row (the call
        was attempted and counts against ``max_llm_calls``)."""
        with self._tx() as c:
            c.execute(
                "UPDATE inv_calls SET in_tok=0, out_tok=0, cost_micro=0, replayed=0, latency_ms=0 WHERE run_id=? AND cand_key=? AND request_key=? AND attempt=? AND in_tok IS NULL",
                (run_id, cand_key, request_key, attempt),
            )

    def reserve_tool(self, run_id: str, cand_key: str, seq: int) -> str:
        with self._tx() as c:
            if c.execute(
                "SELECT 1 FROM inv_tools WHERE run_id=? AND cand_key=? AND seq=?", (run_id, cand_key, seq)
            ).fetchone():
                return "duplicate"
            n = c.execute(
                "SELECT COUNT(*) FROM inv_tools WHERE run_id=? AND cand_key=?", (run_id, cand_key)
            ).fetchone()[0]
            if n >= self.budget.max_tool_calls:
                return "denied"
            c.execute("INSERT INTO inv_tools VALUES (?,?,?)", (run_id, cand_key, seq))
            return "ok"

    def usage(self, run_id: str) -> dict[str, int]:
        with self._tx() as c:
            r = c.execute(
                "SELECT COUNT(*) calls, COALESCE(SUM(in_tok),0) i, COALESCE(SUM(out_tok),0) o, COALESCE(SUM(cost_micro),0) k,"
                " COALESCE(SUM(replayed),0) rp, COALESCE(SUM(latency_ms),0) lat FROM inv_calls WHERE run_id=?", (run_id,),
            ).fetchone()  # fmt: skip
            tools = c.execute("SELECT COUNT(*) FROM inv_tools WHERE run_id=?", (run_id,)).fetchone()[0]
            slots = c.execute("SELECT COUNT(*) FROM inv_slots WHERE run_id=?", (run_id,)).fetchone()[0]
        return {
            "investigations": slots, "llm_calls": r["calls"], "tool_calls": tools, "input_tokens": r["i"],
            "output_tokens": r["o"], "cost_micro_usd": r["k"], "replayed_calls": r["rp"], "latency_ms": r["lat"],
        }  # fmt: skip

    def log(self, run_id: str, kind: str, key: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO inv_events(run_id, kind, key, pid) VALUES (?,?,?,?)", (run_id, kind, key, os.getpid())
            )

    def events(self, run_id: str, kind: str | None = None) -> list[tuple[str, str]]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT kind, key FROM inv_events WHERE run_id=?" + (" AND kind=?" if kind else "") + " ORDER BY seq",
                (run_id, kind) if kind else (run_id,),
            ).fetchall()
        return [(r["kind"], r["key"]) for r in rows]

    # -- recorded responses ----------------------------------------------------------------------------------
    def get_response(self, request_key: str) -> LLMResponse | None:
        with self._tx() as c:
            row = c.execute("SELECT body_json FROM inv_responses WHERE request_key=?", (request_key,)).fetchone()
        if row is None:
            return None
        e = json.loads(row["body_json"])
        return LLMResponse(
            e["text"], e["input_tokens"], e["output_tokens"], e["model"], e["stop_reason"], e.get("latency_ms", 0)
        )

    def put_response(self, request: LLMRequest, response: LLMResponse, replace: bool = False) -> None:
        """Keep an answer so a restarted worker never pays twice. ``replace``: the answer comes from the recording of a
        replay, which is the authority for its own run (a stale answer left by another run must not win)."""
        rec = Recording()
        rec.add(request, response)
        with self._tx() as c:
            c.execute(
                f"INSERT OR {'REPLACE' if replace else 'IGNORE'} INTO inv_responses VALUES (?,?)",
                (request.key, json.dumps(rec.entries[request.key], sort_keys=True)),
            )

    def export_recording(self, keys: set[str] | None = None) -> Recording:
        with self._tx() as c:
            rows = c.execute("SELECT request_key, body_json FROM inv_responses ORDER BY request_key").fetchall()
        return Recording(
            {r["request_key"]: json.loads(r["body_json"]) for r in rows if keys is None or r["request_key"] in keys}
        )

    # -- results and decisions -------------------------------------------------------------------------------
    def put_result(self, run_id: str, cand_key: str, body: dict[str, Any]) -> bool:
        """First result wins: a restarted worker never overwrites a finished investigation."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO inv_results VALUES (?,?,?)", (run_id, cand_key, json.dumps(body, sort_keys=True))
            )
            return bool(cur.rowcount == 1)

    def results(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT cand_key, body_json FROM inv_results WHERE run_id=? ORDER BY cand_key", (run_id,)
            ).fetchall()
        return {r["cand_key"]: json.loads(r["body_json"]) for r in rows}

    def add_decision(self, cand_key: str, evidence_digest: str, action: str, reason: str, actor: str) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO inv_decisions(cand_key, evidence_digest, action, reason, actor, decided_at) VALUES (?,?,?,?,?,?)",
                (cand_key, evidence_digest, action, reason, actor, _now()),
            )
            return int(cur.lastrowid or 0)

    def decisions(self, cand_key: str | None = None) -> list[dict[str, Any]]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT * FROM inv_decisions" + (" WHERE cand_key=?" if cand_key else "") + " ORDER BY id",
                (cand_key,) if cand_key else (),
            ).fetchall()
        return [dict(r) for r in rows]
