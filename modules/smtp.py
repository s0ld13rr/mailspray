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
            if self.port == 465:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)

            server.ehlo()

            if self.port != 465 and server.has_extn("STARTTLS"):
                server.starttls()
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
                    server.quit()
                except Exception:
                    pass
