import imaplib
import socket
import ssl
from mailspray.core.base import BaseModule

# Ascending: plain IMAP+STARTTLS when offered, then IMAPS
IMAP_DISCOVERY_PORTS = (143, 993)


def _imap_plain_new(host, port, timeout_sec):
    return imaplib.IMAP4(host, port, timeout=timeout_sec)


def _imap_plain_after_starttls_attempt(host, port, timeout_sec, ctx):
    """Plain IMAP; if STARTTLS fails, open a fresh plain connection (old socket may be unusable)."""
    server = _imap_plain_new(host, port, timeout_sec)
    try:
        server.starttls(ssl_context=ctx)
        return server
    except Exception:
        try:
            server.shutdown()
        except Exception:
            pass
        return _imap_plain_new(host, port, timeout_sec)


def discover_imap_port(host, timeout_sec):
    """First reachable port; tries plain+STARTTLS and SSL per port when ambiguous."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for port in IMAP_DISCOVERY_PORTS:
        if _imap_discover_one_port(host, port, timeout_sec, ctx):
            return port
    return None


def _imap_discover_one_port(host, port, timeout_sec, ctx):
    if port == 993:
        order = ("ssl", "plain")
    else:
        order = ("plain", "ssl")
    for mode in order:
        server = None
        try:
            if mode == "ssl":
                server = imaplib.IMAP4_SSL(
                    host, port, ssl_context=ctx, timeout=timeout_sec
                )
            else:
                server = _imap_plain_after_starttls_attempt(
                    host, port, timeout_sec, ctx
                )
            try:
                server.logout()
            except Exception:
                try:
                    server.shutdown()
                except Exception:
                    pass
            return True
        except (
            imaplib.IMAP4.error,
            socket.timeout,
            ConnectionRefusedError,
            ssl.SSLError,
            OSError,
        ):
            if server:
                try:
                    server.shutdown()
                except Exception:
                    pass
            continue
    return False


class IMAPModule(BaseModule):
    """IMAP: IMAPS or STARTTLS; retries alternate wire mode on transport errors only."""

    def __init__(self, target):
        super().__init__(target)

    def _imap_implicit_tls(self):
        return self.port == 993 or (self.scheme == "imaps")

    def base_url(self):
        p = self.port or 0
        return f"{self.host}:{p}" if p else f"{self.host}"

    def _login_ssl(self, ctx):
        return imaplib.IMAP4_SSL(
            self.host,
            self.port,
            ssl_context=ctx,
            timeout=self.timeout,
        )

    def _login_plain(self, ctx):
        return _imap_plain_after_starttls_attempt(
            self.host, self.port, self.timeout, ctx
        )

    @staticmethod
    def _safe_close(server):
        if not server:
            return
        try:
            server.logout()
        except Exception:
            try:
                server.socket().close()
            except Exception:
                pass

    def _authenticate_conn(self, username, password):
        """Return a LIVE authenticated imaplib connection, or None. Sets last_error."""
        self.last_error = None
        if not self.port:
            self.last_error = "connect: port not set"
            return None

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if self._imap_implicit_tls():
            modes = ("ssl", "plain")
        else:
            modes = ("plain", "ssl")

        last_transport = None
        for mode in modes:
            server = None
            try:
                if mode == "ssl":
                    server = self._login_ssl(ctx)
                else:
                    server = self._login_plain(ctx)
            except (
                imaplib.IMAP4.error,   # greeting error/abort (e.g. "* BYE too many conns") in __init__
                socket.timeout,
                ConnectionRefusedError,
                ssl.SSLError,
                OSError,
            ) as e:
                last_transport = f"connect: {type(e).__name__}: {e}"
                self._safe_close(server)
                continue

            try:
                server.login(username, password)
                return server  # LIVE — caller owns it (spray closes, modules keep)
            except imaplib.IMAP4.abort as e:
                # Connection dropped mid-LOGIN (rate-limit/greylist) — transport, not bad creds.
                last_transport = f"connect: IMAP4.abort: {e}"
                self._safe_close(server)
                continue
            except imaplib.IMAP4.error:
                self.last_error = "auth"
                self._safe_close(server)
                return None
            except (
                socket.timeout,
                ConnectionRefusedError,
                ssl.SSLError,
                OSError,
            ) as e:
                last_transport = f"connect: {type(e).__name__}: {e}"
                self._safe_close(server)
                continue

        self.last_error = last_transport or "connect: all transports failed"
        return None

    def login(self, username, password):
        server = self._authenticate_conn(username, password)
        if server is None:
            return False
        self._safe_close(server)
        return True

    def authenticate(self, username, password):
        """Live imaplib connection for post-auth modules (e.g. cred_scan), or None."""
        return self._authenticate_conn(username, password)

    def disconnect(self, handle):
        self._safe_close(handle)
