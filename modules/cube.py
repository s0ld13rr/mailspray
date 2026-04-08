import re
from core.base import BaseModule


class RoundcubeModule(BaseModule):
    """Roundcube Webmail."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"

    def _get_token(self, session, url):
        try:
            r = session.get(url, timeout=self.timeout)
            if r.status_code == 200:
                m = re.search(r'name="_token"\s+value="([^"]+)"', r.text)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None

    def login(self, username, password):
        session = self._new_session()
        base = self.base_url()
        login_url = f"{base}/?_task=login"

        try:
            token = self._get_token(session, login_url)
            if not token:
                return False

            data = {
                "_token": token,
                "_user": username,
                "_pass": password,
                "_task": "login",
                "_action": "login",
                "_timezone": "UTC",
                "_url": "_task=login",
            }

            r = session.post(login_url, data=data, allow_redirects=False, timeout=self.timeout)

            if r.status_code == 302:
                location = r.headers.get("Location", "")
                if "_task=mail" in location:
                    return True
                if "_task=login" not in location and "_action=login" not in location:
                    return True
        except Exception:
            pass
        finally:
            session.close()
        return False
