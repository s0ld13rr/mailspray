import re
from core.base import BaseModule


class ZimbraModule(BaseModule):
    """Zimbra Webmail (Web Client login form)."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"

    def _get_csrf_token(self, session, url):
        try:
            r = session.get(url, timeout=self.timeout)
            if r.status_code == 200:
                m = re.search(r'name="login_csrf"\s+value="([^"]+)"', r.text)
                if m:
                    return m.group(1)
                m = re.search(r'csrfToken\s*=\s*"([^"]+)"', r.text)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None

    def login(self, username, password):
        session = self._new_session()
        base = self.base_url()
        login_url = f"{base}/"

        try:
            csrf = self._get_csrf_token(session, login_url)

            data = {
                "loginOp": "login",
                "username": username,
                "password": password,
                "client": "preferred",
            }
            if csrf:
                data["login_csrf"] = csrf

            r = session.post(
                login_url,
                data=data,
                allow_redirects=False,
                timeout=self.timeout,
            )

            if r.status_code == 302:
                location = r.headers.get("Location", "")
                if "/mail" in location or "/zimbra/mail" in location:
                    return True
                if "loginOp" not in location and "loginError" not in location:
                    return True

            if "ZM_AUTH_TOKEN" in r.cookies:
                return True

        except Exception:
            pass
        finally:
            session.close()
        return False
