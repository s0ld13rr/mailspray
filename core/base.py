import requests
import urllib3
import random
import threading
from urllib.parse import urlparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Per-thread HTTP adapters — reuse TCP/TLS connections across requests within the same thread.
# Each thread keeps one HTTPS and one HTTP adapter so the underlying urllib3 connection pool
# stays alive between spray attempts, avoiding a full TCP+TLS handshake per credential pair.
_thread_local = threading.local()


def _get_thread_adapters():
    if not hasattr(_thread_local, "https_adapter"):
        _thread_local.https_adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
        )
        _thread_local.http_adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
        )
    return _thread_local.https_adapter, _thread_local.http_adapter


class BaseModule:
    def __init__(self, target):
        self.timeout = 10
        self.port = None       # subclasses set default
        self.scheme = None     # http or https
        self.host = None       # hostname without scheme/port

        # Parse target: can be URL (http://host:port) or plain host
        parsed = urlparse(target if "://" in target else f"parse://{target}")
        if "://" in target:
            self.scheme = parsed.scheme
            self.host = parsed.hostname
            if parsed.port:
                self.port = parsed.port
        else:
            self.host = parsed.hostname or target

        self.target = self.host  # keep for backward compat (IMAP/SMTP use self.target)

    def base_url(self):
        """Build base URL from scheme/host/port. Web modules use this."""
        scheme = self.scheme or "https"
        port = self.port or (443 if scheme == "https" else 80)
        # omit port if it's the default for the scheme
        if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
            return f"{scheme}://{self.host}"
        return f"{scheme}://{self.host}:{port}"

    def _new_session(self):
        """Create a fresh session per request (no cookie carryover between attempts),
        but mount per-thread adapters so the underlying TCP/TLS connection is reused."""
        s = requests.Session()
        s.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        s.verify = False

        https_adapter, http_adapter = _get_thread_adapters()
        s.mount("https://", https_adapter)
        s.mount("http://", http_adapter)

        return s

    def login(self, username, password) -> bool:
        raise NotImplementedError
