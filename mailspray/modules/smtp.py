import smtplib
import socket
import ssl
from mailspray.core.base import BaseModule


class SMTPModule(BaseModule):
    """SMTP authentication via STARTTLS (587) or SMTPS (465)."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 587

    def login(self, username, password):
        self.last_error = None
        server = None
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            try:
                if self.port == 465:
                    server = smtplib.SMTP_SSL(
                        self.host, self.port, timeout=self.timeout, context=ctx
                    )
                else:
                    server = smtplib.SMTP(
                        self.host, self.port, timeout=self.timeout
                    )

                server.ehlo()

                if self.port != 465 and server.has_extn("STARTTLS"):
                    server.starttls(context=ctx)
                    server.ehlo()

                server.login(username, password)
            except smtplib.SMTPAuthenticationError:
                self.last_error = "auth"
                return False
            except (
                smtplib.SMTPException,
                socket.timeout,
                ConnectionRefusedError,
                ssl.SSLError,
                OSError,
            ) as e:
                self.last_error = f"connect: {type(e).__name__}: {e}"
                return False
            return True
        finally:
            if server:
                try:
                    server.sock.close()
                except Exception:
                    pass
