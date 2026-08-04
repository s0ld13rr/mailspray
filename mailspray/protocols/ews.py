import re
from mailspray.core.base import BaseModule

# Minimal FindFolder SOAP — success body contains NoError; failures expose Error* codes
_EWS_FINDFOLDER = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
  <soap:Header>
    <t:RequestServerVersion Version="Exchange2013"/>
  </soap:Header>
  <soap:Body>
    <m:FindFolder Traversal="Shallow">
      <m:FolderShape>
        <t:BaseShape>IdOnly</t:BaseShape>
      </m:FolderShape>
      <m:IndexedPageFolderView MaxEntriesReturned="1" Offset="0"/>
      <m:ParentFolderIds>
        <t:DistinguishedFolderId Id="msgfolderroot"/>
      </m:ParentFolderIds>
    </m:FindFolder>
  </soap:Body>
</soap:Envelope>"""


class EWSModule(BaseModule):
    """Exchange Web Services — validates credentials via SOAP FindFolder (not naive HTTP 200)."""

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"
        # Populated by authenticate(); used by post_soap() for the gal module.
        self.session = None
        self.auth = None
        self.ews_url = None

    def _soap_auth_failure(self, text):
        if not text:
            return True
        t = text
        if "ErrorInvalidCredentials" in t:
            return True
        if "ErrorAccessDenied" in t:
            return True
        if "Unauthorized" in t and "fault" in t.lower():
            return True
        if re.search(r"ResponseClass=\"Error\"", t, re.I):
            if "NoError" not in t:
                return True
        if "FailedAuthentication" in t:
            return True
        if "wsse:FailedAuthentication" in t:
            return True
        return False

    def _soap_findfolder_ok(self, text):
        if not text:
            return False
        if "FindFolderResponse" not in text:
            return False
        if self._soap_auth_failure(text):
            return False
        if "NoError" not in text:
            return False
        return True

    def _try_findfolder(self, session, url, username, password):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://schemas.microsoft.com/exchange/services/2006/messages/FindFolder"',
        }
        r = session.post(
            url,
            data=_EWS_FINDFOLDER.encode("utf-8"),
            headers=headers,
            auth=(username, password),
            timeout=self.timeout,
            allow_redirects=False,
        )
        if r.status_code == 401 or r.status_code == 403:
            return False
        if r.status_code != 200:
            return False
        return self._soap_findfolder_ok(r.text)

    def _autodiscover_ok(self, text, status_code):
        if status_code == 401 or status_code == 403:
            return False
        if status_code != 200 or not text:
            return False
        tl = text.lower()
        if "wsse:failedauthentication" in tl or "failedauthentication" in tl:
            return False
        if "<account>" in tl or "<user>" in tl or "<displayname>" in tl or "<smtpaddress>" in tl:
            return True
        return False

    def login(self, username, password):
        base = self.base_url()
        url = f"{base}/EWS/Exchange.asmx"

        session = self._new_session()
        try:
            if self._try_findfolder(session, url, username, password):
                return True
        except Exception:
            pass
        finally:
            session.close()

        session2 = self._new_session()
        try:
            url2 = f"{base}/autodiscover/autodiscover.xml"
            r2 = session2.get(
                url2,
                auth=(username, password),
                timeout=self.timeout,
                allow_redirects=False,
            )
            if self._autodiscover_ok(r2.text, r2.status_code):
                return True
        except Exception:
            pass
        finally:
            session2.close()

        return False

    def authenticate(self, username, password):
        """Validate via FindFolder and keep a live Basic-auth session.

        Returns self as the handle (EWS re-sends Basic on every request); the gal
        module calls post_soap() on it. Returns None on failure.
        """
        base = self.base_url()
        url = f"{base}/EWS/Exchange.asmx"
        session = self._new_session()
        try:
            if self._try_findfolder(session, url, username, password):
                self.session = session
                self.auth = (username, password)
                self.ews_url = url
                return self
        except Exception:
            pass
        session.close()
        return None

    def post_soap(self, body, soap_action):
        """POST a SOAP envelope to EWS using the stored Basic credentials."""
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
        }
        return self.session.post(
            self.ews_url,
            data=body.encode("utf-8"),
            headers=headers,
            auth=self.auth,
            timeout=self.timeout,
            allow_redirects=False,
        )

    def disconnect(self, handle):
        try:
            sess = getattr(self, "session", None)
            if sess is not None:
                sess.close()
        except Exception:
            pass
