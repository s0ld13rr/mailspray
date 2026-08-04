"""Workspace findings store (NetExec-style), backed by SQLite.

Findings live in ~/.mailspray/workspaces/<name>.db — outside the project tree,
so the credential-output guard in cli.py does not apply. Two tables:

  credentials  — valid creds discovered (spray hits + successful -M auths)
  loot         — generic module findings (cred_scan matches, gal entries, ...)

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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
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
        base = base_dir or default_workspace_dir()
        self.path = os.path.join(base, f"{self.name}.db")
        try:
            os.makedirs(base, exist_ok=True)
            # check_same_thread=False: guarded by our own lock, shared across spray threads
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            self._warn(f"workspace DB unavailable ({self.path}): {e}")
            self._conn = None

    @property
    def available(self):
        return self._conn is not None

    def add_credential(self, protocol, host, port, username, password):
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO credentials "
                    "(protocol, host, port, username, password, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (protocol, host, port, username, password, _utc_now()),
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
                    "(module, protocol, host, username, category, key, value, source, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (module, protocol, host, username, category, key, value, source, _utc_now()),
                )
                self._conn.commit()
            except Exception as e:
                self._warn(f"failed to store loot: {e}")

    def count(self, table):
        """Test/introspection helper: row count for 'credentials' or 'loot'."""
        if self._conn is None or table not in ("credentials", "loot"):
            return 0
        with self._lock:
            try:
                cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
                return cur.fetchone()[0]
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
