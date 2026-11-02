"""Side-effect ledger: a SQLite file next to the checkpoints, outside the graph state.

It holds what must stay true even if a node is re-executed after a crash or an interrupt:
the budget spent (authoritative at the worker), an execution log (to count re-executions),
one-shot markers (fault injection) and the idempotent publication record.
"""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend(run_id TEXT, candidate_id TEXT, attempt INTEGER,
  PRIMARY KEY(run_id, candidate_id, attempt));
CREATE TABLE IF NOT EXISTS calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, key TEXT, pid INTEGER);
CREATE TABLE IF NOT EXISTS markers(run_id TEXT, key TEXT, PRIMARY KEY(run_id, key));
CREATE TABLE IF NOT EXISTS publications(run_id TEXT PRIMARY KEY, decision TEXT, report_digest TEXT,
  reviewer TEXT, note TEXT, payload TEXT);
"""


class Ledger:
    def __init__(self, path):
        self.path = str(Path(path))
        with self._tx() as c:
            for stmt in _SCHEMA.split(';'):
                if stmt.strip():
                    c.execute(stmt)

    def _tx(self):
        return _Tx(self.path)

    def reserve_spend(self, run_id, candidate_id, attempt, cap):
        """'reserved' | 'duplicate' (same attempt re-executed after a restart: no new spend) | 'denied'."""
        with self._tx() as c:
            if c.execute("SELECT 1 FROM spend WHERE run_id=? AND candidate_id=? AND attempt=?",
                         (run_id, candidate_id, attempt)).fetchone():
                return "duplicate"
            if c.execute("SELECT COUNT(*) FROM spend WHERE run_id=?", (run_id,)).fetchone()[0] >= cap:
                return "denied"
            c.execute("INSERT INTO spend VALUES (?,?,?)", (run_id, candidate_id, attempt))
            return "reserved"

    def spent(self, run_id):
        with self._tx() as c:
            return c.execute("SELECT COUNT(*) FROM spend WHERE run_id=?", (run_id,)).fetchone()[0]

    def log(self, run_id, kind, key):
        with self._tx() as c:
            c.execute("INSERT INTO calls(run_id, kind, key, pid) VALUES (?,?,?,?)", (run_id, kind, key, os.getpid()))

    def calls(self, run_id, kind=None):
        q, a = "SELECT kind, key FROM calls WHERE run_id=?", [run_id]
        if kind:
            q, a = q + " AND kind=?", a + [kind]
        with self._tx() as c:
            return [tuple(r) for r in c.execute(q + " ORDER BY seq", a)]

    def mark_once(self, run_id, key):
        """True only the first time; used for one-shot fault injection."""
        with self._tx() as c:
            return c.execute("INSERT OR IGNORE INTO markers VALUES (?,?)", (run_id, key)).rowcount == 1

    def publish(self, run_id, decision, report_digest, reviewer, note, payload):
        """The irreversible effect. Key and effect commit in one transaction: a replay is a no-op."""
        with self._tx() as c:
            return c.execute("INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?,?)",
                             (run_id, decision, report_digest, reviewer, note, payload)).rowcount == 1

    def publication(self, run_id):
        with self._tx() as c:
            r = c.execute("SELECT decision, report_digest, reviewer, note, payload FROM publications WHERE run_id=?",
                          (run_id,)).fetchone()
            return None if r is None else dict(zip(("decision", "report_digest", "reviewer", "note", "payload"), r))


class _Tx:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        with closing(self.conn):
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
