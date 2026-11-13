"""The incident ledger: Postgres inside the context.

Promoted memories are one-way and immutable from the agent side, so "is this
remembered fix still valid?" lives in Postgres (`fix_validity`). Every memory
hit is joined against it before an agent may act on it. The same tables record
every incident for the benchmark.

Uses Meko AMP Postgres when NIGHTSHIFT_PG_HOST is set, otherwise local SQLite
(offline demo). The SQL is the same for both.
"""
from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS services (
        run_id TEXT, name TEXT, owner_shift TEXT, PRIMARY KEY (run_id, name))""",
    """CREATE TABLE IF NOT EXISTS incidents (
        run_id TEXT, mode TEXT, inc_id TEXT, shift TEXT, night INT, cause TEXT, alert TEXT,
        first_seen INT, trap INT, resolved INT, steps INT, tokens_in INT, tokens_out INT,
        seconds REAL, used_memory TEXT, stale_fix_applied INT, match_quality TEXT,
        PRIMARY KEY (run_id, mode, inc_id))""",
    """CREATE TABLE IF NOT EXISTS fix_validity (
        run_id TEXT, memory_id TEXT, fix_id TEXT, service TEXT, symptom TEXT,
        learned_by TEXT, learned_in TEXT, status TEXT, promoted INT DEFAULT 0,
        retired_reason TEXT, replaced_by TEXT, updated_at REAL,
        PRIMARY KEY (run_id, memory_id))""",
]


class Ledger:
    def __init__(self, run_id: str, fresh: bool = True):
        self.run_id = run_id
        host = os.environ.get("NIGHTSHIFT_PG_HOST")
        if host and os.environ.get("NIGHTSHIFT_LEDGER", "pg") == "pg":
            import psycopg2
            self.db = psycopg2.connect(host=host, port=int(os.environ.get("NIGHTSHIFT_PG_PORT", 5432)),
                                       user=os.environ.get("NIGHTSHIFT_PG_USER", "admin"),
                                       password=os.environ["NIGHTSHIFT_AMP_PASSWORD"], dbname="postgres",
                                       sslmode="require", connect_timeout=20)
            self.db.autocommit = True
            self.ph, self.kind = "%s", "postgres"
        else:
            os.makedirs("results", exist_ok=True)
            self.db = sqlite3.connect("results/ledger.sqlite", check_same_thread=False, isolation_level=None)
            self.ph, self.kind = "?", "sqlite"
        for s in SCHEMA:
            self._x(s)
        # review columns (added after the first release; older ledgers get them here)
        for col in ("approval TEXT DEFAULT 'pending'", "approved_by TEXT"):
            try:
                self._x(f"ALTER TABLE fix_validity ADD COLUMN {'IF NOT EXISTS ' if self.kind == 'postgres' else ''}{col}")
            except Exception:  # sqlite: the column already exists
                pass
        if fresh:  # a benchmark re-run with the same id starts clean
            for t in ("services", "incidents", "fix_validity"):
                self._x(f"DELETE FROM {t} WHERE run_id=?", (run_id,))

    @classmethod
    def open(cls, run_id: str) -> "Ledger":
        """Attach to an existing ledger without clearing it (review queue, webhook, handback)."""
        return cls(run_id, fresh=False)

    def _x(self, sql: str, args: tuple = ()):
        cur = self.db.cursor()
        cur.execute(sql.replace("?", self.ph), args)
        try:
            return cur.fetchall()
        except Exception:
            return []

    def upsert_services(self, services: dict[str, str]):
        for name, shift in services.items():
            self._x("DELETE FROM services WHERE run_id=? AND name=?", (self.run_id, name))
            self._x("INSERT INTO services VALUES (?,?,?)", (self.run_id, name, shift))

    def record_incident(self, mode: str, row: dict):
        cols = ["inc_id", "shift", "night", "cause", "alert", "first_seen", "trap", "resolved", "steps",
                "tokens_in", "tokens_out", "seconds", "used_memory", "stale_fix_applied", "match_quality"]
        self._x("DELETE FROM incidents WHERE run_id=? AND mode=? AND inc_id=?", (self.run_id, mode, row["inc_id"]))
        self._x(f"INSERT INTO incidents (run_id, mode, {', '.join(cols)}) VALUES ({', '.join(['?'] * (len(cols) + 2))})",
                (self.run_id, mode, *[row.get(c) for c in cols]))

    def learn(self, memory_id, fix_id, service, symptom, learned_by, learned_in):
        self._x("INSERT INTO fix_validity (run_id, memory_id, fix_id, service, symptom, learned_by, learned_in, status, updated_at)"
                " VALUES (?,?,?,?,?,?,?,'valid',?)",
                (self.run_id, memory_id, fix_id, service, symptom, learned_by, learned_in, time.time()))

    def status(self, memory_id) -> dict | None:
        r = self._x("SELECT memory_id, fix_id, service, symptom, status, learned_by, learned_in, promoted, retired_reason, replaced_by"
                    " FROM fix_validity WHERE run_id=? AND memory_id=?", (self.run_id, memory_id))
        if not r:
            return None
        k = ["memory_id", "fix_id", "service", "symptom", "status", "learned_by", "learned_in", "promoted", "retired_reason", "replaced_by"]
        return dict(zip(k, r[0]))

    def retire(self, memory_id, reason, replaced_by=None):
        self._x("UPDATE fix_validity SET status='retired', retired_reason=?, replaced_by=?, updated_at=?"
                " WHERE run_id=? AND memory_id=?", (reason, replaced_by, time.time(), self.run_id, memory_id))

    def replace(self, old_memory_id, new_memory_id):
        self._x("UPDATE fix_validity SET replaced_by=? WHERE run_id=? AND memory_id=?",
                (new_memory_id, self.run_id, old_memory_id))

    def unpromoted_valid(self) -> list[str]:
        """Fixes the curator may share: still valid, not yet promoted, approved by a reviewer."""
        return [r[0] for r in self._x("SELECT memory_id FROM fix_validity WHERE run_id=? AND status='valid' AND promoted=0"
                                      " AND approval='approved'", (self.run_id,))]

    def pending_review(self) -> list[dict]:
        k = ["memory_id", "fix_id", "service", "symptom", "learned_by", "learned_in"]
        rows = self._x("SELECT memory_id, fix_id, service, symptom, learned_by, learned_in FROM fix_validity"
                       " WHERE run_id=? AND status='valid' AND promoted=0 AND (approval IS NULL OR approval='pending')"
                       " ORDER BY updated_at", (self.run_id,))
        return [dict(zip(k, r)) for r in rows]

    def approve(self, memory_id, who: str):
        self._x("UPDATE fix_validity SET approval='approved', approved_by=?, updated_at=? WHERE run_id=? AND memory_id=?",
                (who, time.time(), self.run_id, memory_id))

    def reject(self, memory_id, who: str, reason: str):
        """A reviewer says this fix is wrong: it is never shared, and the Fixer treats it as retired."""
        self._x("UPDATE fix_validity SET approval='rejected', approved_by=?, status='retired', retired_reason=?, updated_at=?"
                " WHERE run_id=? AND memory_id=?", (who, f"rejected by {who}: {reason}", time.time(), self.run_id, memory_id))

    def mark_promoted(self, ids):
        for i in ids:
            self._x("UPDATE fix_validity SET promoted=1, updated_at=? WHERE run_id=? AND memory_id=?",
                    (time.time(), self.run_id, i))

    def table(self, name):
        return self._x(f"SELECT * FROM {name} WHERE run_id=?", (self.run_id,))
