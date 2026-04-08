from core.base import BaseModule


class OWAModule(BaseModule):
    """Outlook Web Access (Exchange 2010-2019)."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"

    def login(self, username, password):
        session = self._new_session()
        base = self.base_url()
        url = f"{base}/owa/auth.owa"

        data = {
            "destination": f"{base}/owa/",
            "flags": "4",
            "forcedownlevel": "0",
            "username": username,
            "password": password,
            "isUtf8": "1",
        }

        try:
            r = session.post(url, data=data, allow_redirects=False, timeout=self.timeout)
            if "cadata" in r.cookies or "cadataKey" in r.cookies:
                return True
            location = r.headers.get("Location", "")
            if r.status_code == 302 and "/owa" in location and "logon" not in location.lower():
                return True
        except Exception:
            pass
        finally:
            session.close()
        return False
