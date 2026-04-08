import imaplib
import socket
import ssl
from core.base import BaseModule


class IMAPModule(BaseModule):
    """IMAP4 with SSL/STARTTLS support."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 993

    def login(self, username, password):
        server = None
        try:
            if self.port == 993:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                server = imaplib.IMAP4_SSL(self.host, self.port,
                                           ssl_context=ctx, timeout=self.timeout)
            else:
                server = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    server.starttls(ssl_context=ctx)
                except Exception:
                    pass

            server.login(username, password)
            return True
        except (imaplib.IMAP4.error, socket.timeout, ConnectionError, OSError):
            return False
        finally:
            if server:
                try:
                    # Force-close socket without LOGOUT round-trip — saves one RTT per attempt
                    server.socket().close()
                except Exception:
                    pass
