"""Plain-text findings store — no database.

Findings are appended to flat, grep-friendly files under ~/.mailspray/<workspace>/:

  credentials.txt   valid creds, one `proto://user:pass@host:port` per line
  gal.dump          GAL entries, `email<TAB>displayName` per line
  cred_scan.dump    module hits, `category<TAB>key<TAB>source` per line
  <module>.dump     generic module loot

Files are created lazily on the first finding — a run that finds nothing writes
nothing. Lines are de-duplicated (within the run and against what is already on
disk). All writes are best-effort and never abort a run.
"""

import os
import threading


def default_root():
    return os.path.join(os.path.expanduser("~"), ".mailspray")


def list_workspaces(base_dir=None):
    """Return [(name, path)] for every workspace directory found."""
    base = base_dir or default_root()
    out = []
    try:
        for entry in sorted(os.listdir(base)):
            full = os.path.join(base, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                out.append((entry, full))
    except OSError:
        pass
    return out


class FindingsStore:
    CREDS_FILE = "credentials.txt"

    def __init__(self, workspace="default", warn=None, base_dir=None):
        self.workspace = workspace or "default"
        self._warn = warn or (lambda m: None)
        self._lock = threading.Lock()
        root = base_dir or default_root()
        self.dir = os.path.join(root, self.workspace)
        self._seen = {}    # filename -> set(existing+written lines) for dedup
        self._counts = {}  # filename -> count of NEW lines written this run

    # ── internals ───────────────────────────────────────────────────

    def _load_existing(self, filename):
        s = set()
        try:
            with open(os.path.join(self.dir, filename)) as f:
                for ln in f:
                    ln = ln.rstrip("\n")
                    if ln:
                        s.add(ln)
        except OSError:
            pass
        return s

    def _write(self, filename, line):
        with self._lock:
            seen = self._seen.get(filename)
            if seen is None:
                seen = self._load_existing(filename)
                self._seen[filename] = seen
            if line in seen:
                return
            seen.add(line)
            try:
                os.makedirs(self.dir, exist_ok=True)
                with open(os.path.join(self.dir, filename), "a") as f:
                    f.write(line + "\n")
                self._counts[filename] = self._counts.get(filename, 0) + 1
            except Exception as e:
                self._warn(f"failed to write {filename}: {e}")

    # ── run tracking (no-op for plain-text store) ──────────────────

    def start_run(self, protocol, target, mode, module=None, users=None, passwords=None):
        pass

    def finish_run(self):
        pass

    # ── writes ──────────────────────────────────────────────────────

    def add_credential(self, protocol, host, port, username, password):
        hostport = f"{host}:{port}" if port else host
        self._write(self.CREDS_FILE, f"{protocol}://{username}:{password}@{hostport}")

    def add_loot(self, module, protocol, host, username, category, key, value, source):
        if module == "gal":
            line = f"{key}\t{value}"
        else:
            line = f"{category}\t{key}\t{source}"
        self._write(f"{module}.dump", line)

    # ── reads (for --creds / --loot viewers) ────────────────────────

    def get_credentials(self):
        """Return list of credential lines from the text file."""
        path = os.path.join(self.dir, self.CREDS_FILE)
        try:
            with open(path) as f:
                return [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError:
            return []

    def get_loot_files(self):
        """Return [(filename, line_count)] for all .dump files."""
        out = []
        try:
            for fn in sorted(os.listdir(self.dir)):
                if fn.endswith(".dump"):
                    path = os.path.join(self.dir, fn)
                    try:
                        with open(path) as f:
                            lines = sum(1 for ln in f if ln.strip())
                        out.append((fn, lines))
                    except OSError:
                        pass
        except OSError:
            pass
        return out

    def get_loot(self, filename):
        """Return lines from a specific .dump file."""
        path = os.path.join(self.dir, filename)
        try:
            with open(path) as f:
                return [ln.rstrip("\n") for ln in f if ln.strip()]
        except OSError:
            return []

    # ── reporting ───────────────────────────────────────────────────

    def saved(self):
        """[(path, new_line_count)] for files that received new findings this run."""
        return [(os.path.join(self.dir, fn), c) for fn, c in self._counts.items() if c > 0]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
