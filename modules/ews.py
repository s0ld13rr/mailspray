from core.base import BaseModule


class EWSModule(BaseModule):
    """Exchange Web Services (EWS) — NTLM and Basic auth."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"

    def login(self, username, password):
        session = self._new_session()
        base = self.base_url()
        url = f"{base}/EWS/Exchange.asmx"

        try:
            r = session.get(
                url,
                auth=(username, password),
                timeout=self.timeout,
                allow_redirects=False,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 302:
                location = r.headers.get("Location", "")
                if "/owa" in location.lower() and "logon" not in location.lower():
                    return True
        except Exception:
            pass
        finally:
            session.close()

        # Autodiscover fallback
        try:
            session2 = self._new_session()
            url2 = f"{base}/autodiscover/autodiscover.xml"
            r2 = session2.get(
                url2,
                auth=(username, password),
                timeout=self.timeout,
                allow_redirects=False,
            )
            if r2.status_code == 200:
                return True
            if r2.status_code == 302:
                location = r2.headers.get("Location", "")
                if "logon" not in location.lower():
                    return True
        except Exception:
            pass
        finally:
            session2.close()

        return False
