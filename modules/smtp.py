import smtplib
import socket
import ssl
from core.base import BaseModule


class SMTPModule(BaseModule):
    """SMTP authentication via STARTTLS (587) or SMTPS (465)."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 587

    def login(self, username, password):
        server = None
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port,
                                          timeout=self.timeout, context=ctx)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)

            server.ehlo()

            if self.port != 465 and server.has_extn("STARTTLS"):
                server.starttls(context=ctx)
                server.ehlo()

            server.login(username, password)
            return True
        except smtplib.SMTPAuthenticationError:
            return False
        except (smtplib.SMTPException, socket.timeout, ConnectionError, OSError):
            return False
        finally:
            if server:
                try:
                    # Force-close without QUIT round-trip — saves one RTT per attempt
                    server.sock.close()
                except Exception:
                    pass
