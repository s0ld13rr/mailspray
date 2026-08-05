"""Workspace findings store (NetExec-style), backed by SQLite.

Findings live in ~/.mailspray/workspaces/<name>.db — outside the project tree,
so the credential-output guard in cli.py does not apply. Tables:

  runs         — one row per spray / module invocation (the "run marker")
  credentials  — valid creds discovered, tagged with the run that first found them
  loot         — module findings (cred_scan matches, gal entries, ...), run-tagged

Every write is best-effort: on any sqlite error we swallow it (optionally via a
warn hook) so a findings-store problem never aborts a live engagement.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_workspace_dir():
    return os.path.join(os.path.expanduser("~"), ".mailspray", "workspaces")


def list_workspaces(base_dir=None):
    """Return [(name, path)] for every workspace DB found."""
    base = base_dir or default_workspace_dir()
    out = []
    try:
        for fn in sorted(os.listdir(base)):
            if fn.endswith(".db"):
                out.append((fn[:-3], os.path.join(base, fn)))
    except OSError:
        pass
    return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started    TEXT NOT NULL,
    protocol   TEXT,
    target     TEXT,
    mode       TEXT,
    module     TEXT,
    users      INTEGER,
    passwords  INTEGER,
    found      INTEGER DEFAULT 0,
    loot       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS credentials (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER,
    protocol   TEXT NOT NULL,
    host       TEXT NOT NULL,
    port       INTEGER,
    username   TEXT NOT NULL,
    password   TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    UNIQUE(protocol, host, username, password)
);
CREATE TABLE IF NOT EXISTS loot (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER,
    module    TEXT NOT NULL,
    protocol  TEXT,
    host      TEXT,
    username  TEXT,
    category  TEXT,
    key       TEXT,
    value     TEXT,
    source    TEXT,
    created   TEXT NOT NULL,
    UNIQUE(module, host, username, category, key, value, source)
);
"""


class WorkspaceDB:
    """Thread-safe, best-effort SQLite findings store for a single workspace."""

    def __init__(self, name="default", warn=None, base_dir=None):
        self.name = name or "default"
        self._warn = warn or (lambda msg: None)
        self._lock = threading.Lock()
        self._conn = None
        self._run_id = None
        base = base_dir or default_workspace_dir()
        self.path = os.path.join(base, f"{self.name}.db")
        try:
            os.makedirs(base, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            self._warn(f"workspace DB unavailable ({self.path}): {e}")
            self._conn = None

    def _migrate(self):
        """Add run_id to pre-0.5.8 DBs that predate run tracking."""
        for table in ("credentials", "loot"):
            cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")]
            if "run_id" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id INTEGER")

    @property
    def available(self):
        return self._conn is not None

    # ── run tracking ────────────────────────────────────────────────

    def start_run(self, protocol, target, mode, module=None, users=None, passwords=None):
        """Open a run row; subsequent add_* calls tag rows with it. Returns run id."""
        if self._conn is None:
            return None
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO runs (started, protocol, target, mode, module, users, passwords) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (_utc_now(), protocol, target, mode, module, users, passwords),
                )
                self._conn.commit()
                self._run_id = cur.lastrowid
                return self._run_id
            except Exception as e:
                self._warn(f"failed to start run: {e}")
                return None

    def finish_run(self):
        """Backfill the current run's found/loot counts."""
        if self._conn is None or self._run_id is None:
            return
        with self._lock:
            try:
                found = self._conn.execute(
                    "SELECT COUNT(*) FROM credentials WHERE run_id=?", (self._run_id,)
                ).fetchone()[0]
                loot = self._conn.execute(
                    "SELECT COUNT(*) FROM loot WHERE run_id=?", (self._run_id,)
                ).fetchone()[0]
                self._conn.execute(
                    "UPDATE runs SET found=?, loot=? WHERE id=?",
                    (found, loot, self._run_id),
                )
                self._conn.commit()
            except Exception as e:
                self._warn(f"failed to finish run: {e}")

    # ── writes ──────────────────────────────────────────────────────

    def add_credential(self, protocol, host, port, username, password):
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO credentials "
                    "(run_id, protocol, host, port, username, password, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self._run_id, protocol, host, port, username, password, _utc_now()),
                )
                self._conn.commit()
            except Exception as e:
                self._warn(f"failed to store credential: {e}")

    def add_loot(self, module, protocol, host, username, category, key, value, source):
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO loot "
                    "(run_id, module, protocol, host, username, category, key, value, source, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self._run_id, module, protocol, host, username,
                     category, key, value, source, _utc_now()),
                )
                self._conn.commit()
            except Exception as e:
                self._warn(f"failed to store loot: {e}")

    # ── reads (viewer) ──────────────────────────────────────────────

    def get_runs(self):
        if self._conn is None:
            return []
        with self._lock:
            try:
                return self._conn.execute(
                    "SELECT id, started, protocol, target, mode, module, users, passwords, "
                    "found, loot FROM runs ORDER BY id"
                ).fetchall()
            except Exception:
                return []

    def get_credentials(self, run=None):
        if self._conn is None:
            return []
        q = ("SELECT run_id, protocol, host, port, username, password, first_seen "
             "FROM credentials")
        args = ()
        if run is not None:
            q += " WHERE run_id=?"
            args = (run,)
        q += " ORDER BY id"
        with self._lock:
            try:
                return self._conn.execute(q, args).fetchall()
            except Exception:
                return []

    def get_loot(self, run=None, module=None, category=None):
        if self._conn is None:
            return []
        q = ("SELECT run_id, module, protocol, host, username, category, key, value, source, created "
             "FROM loot")
        clauses, args = [], []
        if run is not None:
            clauses.append("run_id=?"); args.append(run)
        if module:
            clauses.append("module=?"); args.append(module)
        if category:
            clauses.append("category=?"); args.append(category)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id"
        with self._lock:
            try:
                return self._conn.execute(q, tuple(args)).fetchall()
            except Exception:
                return []

    def count(self, table):
        """Test/introspection helper: row count for a known table."""
        if self._conn is None or table not in ("credentials", "loot", "runs"):
            return 0
        with self._lock:
            try:
                return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                return 0

    def close(self):
        if self._conn is not None:
            with self._lock:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
