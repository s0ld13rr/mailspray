import imaplib
import socket
import ssl
from mailspray.core.base import BaseModule


class IMAPModule(BaseModule):
    """IMAP4 with SSL/STARTTLS support."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 993

    def login(self, username, password):
        self.last_error = None
        server = None
        try:
            if self.port == 993:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                try:
                    server = imaplib.IMAP4_SSL(
                        self.host,
                        self.port,
                        ssl_context=ctx,
                        timeout=self.timeout,
                    )
                except (
                    socket.timeout,
                    ConnectionRefusedError,
                    ssl.SSLError,
                    OSError,
                ) as e:
                    self.last_error = f"connect: {type(e).__name__}: {e}"
                    return False
            else:
                try:
                    server = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
                    try:
                        ctx = ssl.create_default_context()
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                        server.starttls(ssl_context=ctx)
                    except Exception:
                        pass
                except (
                    socket.timeout,
                    ConnectionRefusedError,
                    ssl.SSLError,
                    OSError,
                ) as e:
                    self.last_error = f"connect: {type(e).__name__}: {e}"
                    return False

            try:
                server.login(username, password)
            except imaplib.IMAP4.error:
                self.last_error = "auth"
                return False
            return True
        finally:
            if server:
                try:
                    server.socket().close()
                except Exception:
                    pass
