import imaplib
import socket
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
                server = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
                try:
                    server.starttls()
                except Exception:
                    pass

            server.login(username, password)
            return True
        except (imaplib.IMAP4.error, socket.timeout, ConnectionError, OSError):
            return False
        finally:
            if server:
                try:
                    server.logout()
                except Exception:
                    pass
