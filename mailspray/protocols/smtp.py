import smtplib
import socket
import ssl
from mailspray.core.base import BaseModule

# Ascending order; 465 = implicit TLS (SMTP_SSL), others = plaintext then STARTTLS if advertised
SMTP_DISCOVERY_PORTS = (25, 465, 587, 2525, 3025)


def _smtp_connect_mode(host, port, timeout_sec, ctx, mode):
    """mode: 'ssl' = SMTP_SSL, 'starttls' = plain EHLO + optional STARTTLS."""
    if mode == "ssl":
        server = smtplib.SMTP_SSL(
            host, port, timeout=timeout_sec, context=ctx
        )
        server.ehlo()
        return server
    server = smtplib.SMTP(host, port, timeout=timeout_sec)
    server.ehlo()
    if server.has_extn("STARTTLS"):
        server.starttls(context=ctx)
        server.ehlo()
    return server


def discover_smtp_port(host, timeout_sec):
    """First reachable port: probes STARTTLS and implicit TLS when needed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for port in SMTP_DISCOVERY_PORTS:
        if _smtp_discover_one_port(host, port, timeout_sec, ctx):
            return port
    return None


def _smtp_discover_one_port(host, port, timeout_sec, ctx):
    if port == 465:
        order = ("ssl", "starttls")
    else:
        order = ("starttls", "ssl")
    for mode in order:
        server = None
        try:
            server = _smtp_connect_mode(host, port, timeout_sec, ctx, mode)
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass
            return True
        except (
            smtplib.SMTPException,
            socket.timeout,
            ConnectionRefusedError,
            ssl.SSLError,
            OSError,
        ):
            if server:
                try:
                    server.close()
                except Exception:
                    pass
            continue
    return False


class SMTPModule(BaseModule):
    """SMTP: STARTTLS or implicit TLS; retries alternate wire mode on transport errors only."""

    def __init__(self, target):
        super().__init__(target)

    def _smtp_implicit_tls(self):
        return self.port == 465 or (self.scheme == "smtps")

    def base_url(self):
        p = self.port or 0
        return f"{self.host}:{p}" if p else f"{self.host}"

    def _login_attempt(self, username, password, ctx, mode):
        server = None
        try:
            server = _smtp_connect_mode(
                self.host, self.port, self.timeout, ctx, mode
            )
            server.login(username, password)
            return True, None, server
        except smtplib.SMTPAuthenticationError:
            return False, "auth", server
        except (
            smtplib.SMTPException,
            socket.timeout,
            ConnectionRefusedError,
            ssl.SSLError,
            OSError,
        ) as e:
            return False, f"connect: {type(e).__name__}: {e}", server

    def login(self, username, password):
        self.last_error = None
        if not self.port:
            self.last_error = "connect: port not set"
            return False
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if self._smtp_implicit_tls():
            modes = ("ssl", "starttls")
        else:
            modes = ("starttls", "ssl")

        last_transport = None
        for mode in modes:
            server = None
            try:
                ok, err_kind, server = self._login_attempt(
                    username, password, ctx, mode
                )
                if ok:
                    return True
                if err_kind == "auth":
                    self.last_error = "auth"
                    return False
                last_transport = err_kind
            finally:
                if server:
                    try:
                        server.close()
                    except Exception:
                        pass
            continue
        self.last_error = last_transport or "connect: all transports failed"
        return False
