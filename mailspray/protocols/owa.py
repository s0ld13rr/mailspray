import re
from html import unescape
from urllib.parse import urljoin

from mailspray.core.base import BaseModule


class OWAModule(BaseModule):
    """Outlook Web Access (Exchange). Aligns with browser logon.aspx where possible."""

    # Exchange may return a JS-only shell (no <input type=password>) for modern browser UAs;
    # SSR logon form is returned for simple/downlevel clients (see mail.stdi.kz style hosts).
    _OWA_FORM_USER_AGENT = "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0)"
    _OWA_FORM_ACCEPT = "*/*"

    # Body/URL fragments that indicate failed form login (EN + RU typical OWA strings)
    _FAIL_MARKERS = (
        "incorrect user name or password",
        "you could not be signed in",
        "couldn't sign in",
        "could not sign in",
        "sign in failed",
        "your request could not be completed",
        "neither your user name nor",
        "неправильное имя пользователя",
        "неправильно введен",
        "введено неправильное",
        "не удалось выполнить вход",
    )

    def __init__(self, target):
        super().__init__(target)
        if not self.port:
            self.port = 443
        if not self.scheme:
            self.scheme = "https"

    @staticmethod
    def _logon_html_has_password_input(html):
        if not html:
            return False
        h = html.lower()
        return 'type="password"' in h or "type='password'" in h

    def _fetch_logon_html(self, session, base):
        """GET logon.aspx; retry with downlevel UA if server returns JS shell without password field."""
        logon_url = f"{base}/owa/auth/logon.aspx"
        html = ""
        try:
            g = session.get(logon_url, timeout=self.timeout, allow_redirects=True)
            if g is not None and g.status_code == 200:
                html = g.text or ""
        except Exception:
            return ""

        if self._logon_html_has_password_input(html):
            return html

        session.headers["User-Agent"] = self._OWA_FORM_USER_AGENT
        session.headers["Accept"] = self._OWA_FORM_ACCEPT
        try:
            g = session.get(logon_url, timeout=self.timeout, allow_redirects=True)
            if g is not None and g.status_code == 200:
                return g.text or ""
        except Exception:
            pass
        return html

    def _has_auth_cookie(self, session):
        for c in session.cookies:
            if c.name.lower() in ("cadata", "cadatakey"):
                return True
        return False

    def _failure_from_text_or_loc(self, text, location):
        blob = f"{(text or '').lower()} {(location or '').lower()}"
        for m in self._FAIL_MARKERS:
            if m in blob:
                return True
        loc = (location or "").lower()
        if "reason=" in loc and ("logon" in loc or "auth" in loc):
            return True
        return False

    def _login_succeeded(self, resp, session):
        """Conservative: no error page text, no error query params, auth cookie or safe redirect."""
        loc = resp.headers.get("Location", "") if resp is not None else ""
        body = getattr(resp, "text", "") or ""

        if self._failure_from_text_or_loc(body, loc):
            return False

        if self._has_auth_cookie(session):
            ll = loc.lower()
            if ll and "logon" in ll and "reason=" in ll:
                return False
            return True

        if resp is not None and resp.status_code == 302:
            ll = loc.lower()
            if "/owa" not in ll:
                return False
            if "logon" in ll or "auth/logon" in ll:
                return False
            if "reason=" in ll:
                return False
            return True

        return False

    def _parse_input_triples(self, form_html):
        """Yield (name, value, type) from input tags inside form fragment."""
        for m in re.finditer(r"(?is)<input\s+([^>]+)>", form_html):
            attrs = m.group(1)
            name = re.search(r'name\s*=\s*"([^"]*)"', attrs, re.I)
            if not name:
                name = re.search(r"name\s*=\s*'([^']*)'", attrs, re.I)
            if not name:
                continue
            name = name.group(1)

            vm = re.search(r'value\s*=\s*"([^"]*)"', attrs, re.I)
            if not vm:
                vm = re.search(r"value\s*=\s*'([^']*)'", attrs, re.I)
            val = vm.group(1) if vm else ""

            tm = re.search(r'type\s*=\s*"([^"]*)"', attrs, re.I)
            if not tm:
                tm = re.search(r"type\s*=\s*'([^']*)'", attrs, re.I)
            typ = (tm.group(1) if tm else "text").lower()
            yield name, unescape(val), typ

    def _build_logon_post(self, base, html, username, password):
        """Find form containing a password field; return (post_url, data) or (None, None)."""
        lower = html.lower()
        key = 'type="password"'
        idx = lower.find(key)
        if idx < 0:
            idx = lower.find("type='password'")
        if idx < 0:
            return None, None

        form_open = html.rfind("<form", 0, idx)
        if form_open < 0:
            return None, None
        form_close = html.find("</form>", idx)
        if form_close < 0:
            return None, None
        chunk = html[form_open:form_close]

        am = re.search(r'<form[^>]*action\s*=\s*"([^"]*)"', chunk, re.I)
        if not am:
            am = re.search(r"<form[^>]*action\s*=\s*'([^']*)'", chunk, re.I)
        action = unescape(am.group(1)) if am else "/owa/auth/logon.aspx"
        post_url = urljoin(base.rstrip("/") + "/", action)

        data = {}
        user_field_seen = False
        pass_field_seen = False

        for name, val, typ in self._parse_input_triples(chunk):
            if typ == "hidden":
                data[name] = val
            elif typ == "password":
                data[name] = password
                pass_field_seen = True
            elif typ in ("text", "email"):
                lk = name.lower()
                if lk in ("username", "user name", "loginname", "email"):
                    data[name] = username
                    user_field_seen = True
                else:
                    data[name] = val
            else:
                if name not in data:
                    data[name] = val

        if pass_field_seen:
            for k in list(data.keys()):
                lk = k.lower()
                if lk == "username":
                    data[k] = username
                    user_field_seen = True
                elif lk == "password":
                    data[k] = password

        if not pass_field_seen or not data:
            return None, None
        if not user_field_seen:
            if "username" not in data and "Username" not in data:
                for k in list(data.keys()):
                    if k.lower() == "username":
                        data[k] = username
                        user_field_seen = True
                        break
        if not user_field_seen:
            return None, None

        return post_url, data

    def _post_legacy_auth_owa(self, session, base, username, password):
        url = f"{base}/owa/auth.owa"
        payload = {
            "destination": f"{base}/owa/",
            "flags": "4",
            "forcedownlevel": "0",
            "username": username,
            "password": password,
            "isUtf8": "1",
        }
        return session.post(url, data=payload, allow_redirects=False, timeout=self.timeout)

    def _authenticate_session(self, username, password):
        """Return a LIVE requests.Session carrying the OWA auth cookies, or None."""
        base = self.base_url()
        session = self._new_session()
        ok = False

        try:
            html = self._fetch_logon_html(session, base)
            if self._logon_html_has_password_input(html):
                post_url, data = self._build_logon_post(base, html, username, password)
                if post_url and data:
                    try:
                        r = session.post(
                            post_url,
                            data=data,
                            allow_redirects=False,
                            timeout=self.timeout,
                        )
                        if self._login_succeeded(r, session):
                            ok = True
                    except Exception:
                        pass

            if not ok:
                try:
                    r = self._post_legacy_auth_owa(session, base, username, password)
                    if self._login_succeeded(r, session):
                        ok = True
                except Exception:
                    pass
        except Exception:
            ok = False

        if ok:
            return session  # live — holds cadata/cadatakey (+ canary) cookies
        session.close()
        return None

    def login(self, username, password):
        session = self._authenticate_session(username, password)
        if session is None:
            return False
        session.close()
        return True

    def authenticate(self, username, password):
        """Live OWA session (cookie jar) for post-auth modules (e.g. gal), or None."""
        return self._authenticate_session(username, password)

    def disconnect(self, handle):
        try:
            handle.close()
        except Exception:
            pass
