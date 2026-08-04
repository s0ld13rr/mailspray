import unittest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from mailspray.modules.cred_scan import CredScanModule, _decode_mutf7


class FakeCtx:
    """Captures emit_loot() calls and swallows logging."""
    def __init__(self):
        self.protocol = "imap"
        self.host = "mail.test"
        self.username = "alice"
        self.loot = []
        self.loot_count = 0

    def emit_loot(self, category, key, value="", source=""):
        self.loot_count += 1
        self.loot.append((category, key, source))

    def log_info(self, *a, **k):
        pass

    log_good = log_warn = log_info


class FakeIMAP:
    """Minimal IMAP conn double that records the FETCH command string."""
    def __init__(self, raw):
        self._raw = raw
        self.fetch_cmds = []

    def list(self):
        return ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    def select(self, folder, readonly=False):
        assert readonly is True, "cred_scan must SELECT read-only"
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"1"])
        if cmd == "FETCH":
            uid, spec = args[0], args[1]
            self.fetch_cmds.append(spec)
            return ("OK", [(b"1 (UID 1 BODY[] {123}", self._raw), b")"])
        return ("NO", [])


def _build_raw():
    msg = MIMEMultipart()
    msg["Subject"] = "vpn access"
    msg["From"] = "it@test"
    msg.attach(MIMEText("Hi, your account password=Sup3rSecret! keep it safe.", "plain"))
    env = MIMEApplication(b"API_KEY=AKIAIOSFODNN7EXAMPLE\nremote vpn.test 1194\n", Name="creds.env")
    env["Content-Disposition"] = 'attachment; filename="creds.env"'
    msg.attach(env)
    return msg.as_bytes()


class TestCredScanParse(unittest.TestCase):
    def setUp(self):
        self.mod = CredScanModule()
        self.mod.options({})

    def test_scan_message_finds_body_and_attachment(self):
        ctx = FakeCtx()
        self.mod._scan_message(ctx, "INBOX", b"1", _build_raw())
        cats = {c for c, _, _ in ctx.loot}
        self.assertIn("password", cats)          # from body
        self.assertIn("aws_access_key", cats)     # from .env attachment
        self.assertIn("openvpn_remote", cats)     # from .env attachment

    def test_attachments_off(self):
        self.mod.options({"attachments": "off"})
        ctx = FakeCtx()
        self.mod._scan_message(ctx, "INBOX", b"1", _build_raw())
        cats = {c for c, _, _ in ctx.loot}
        self.assertIn("password", cats)           # body still scanned
        self.assertNotIn("aws_access_key", cats)  # attachment skipped

    def test_on_auth_uses_body_peek(self):
        self.mod.options({})
        ctx = FakeCtx()
        conn = FakeIMAP(_build_raw())
        self.mod.on_auth(ctx, conn)
        self.assertTrue(ctx.loot_count >= 2)
        # Proof the fetch never sets \Seen: every FETCH used BODY.PEEK
        self.assertTrue(conn.fetch_cmds, "no FETCH issued")
        for spec in conn.fetch_cmds:
            self.assertIn("BODY.PEEK", spec)
            self.assertNotIn("BODY[]", spec.replace("BODY.PEEK[]", ""))


class FakeListConn:
    def __init__(self, data):
        self._data = data

    def list(self):
        return ("OK", self._data)


class TestFolderParsing(unittest.TestCase):
    def setUp(self):
        self.mod = CredScanModule()
        self.mod.options({})

    def test_quoted_nil_literal_noselect(self):
        data = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) NIL "Flat"',                       # NIL delimiter
            b'(\\Noselect \\HasChildren) "/" "[Gmail]"',           # not selectable
            (b'(\\HasNoChildren) "/" {13}', b'Weird Folder!'),     # literal name
        ]
        folders = self.mod._list_folders(FakeListConn(data))
        raws = [r for r, _ in folders]
        self.assertIn("INBOX", raws)
        self.assertIn("Flat", raws)               # NIL delimiter folder kept
        self.assertIn("Weird Folder!", raws)      # literal name, not "{13}"
        self.assertNotIn("[Gmail]", raws)         # \Noselect dropped

    def test_mutf7_decode(self):
        self.assertEqual(_decode_mutf7("INBOX"), "INBOX")
        self.assertEqual(_decode_mutf7("Sent &- Drafts"), "Sent & Drafts")
        self.assertEqual(_decode_mutf7("caf&AOk-"), "café")

    def test_since_validation(self):
        m = CredScanModule()
        m.options({"since": "2024-01-01"})   # wrong format
        self.assertIsNone(m.since)
        self.assertEqual(m._since_invalid, "2024-01-01")
        m.options({"since": "01-Jan-2024"})  # correct
        self.assertEqual(m.since, "01-Jan-2024")
        self.assertIsNone(m._since_invalid)

    def test_max_validation(self):
        m = CredScanModule()
        m.options({"max": "0"})
        self.assertEqual(m.max_msgs, 0)          # 0 -> no cap
        self.assertEqual(m._max_invalid, "0")    # but flagged as invalid input
        m.options({"max": "50"})
        self.assertEqual(m.max_msgs, 50)
        self.assertIsNone(m._max_invalid)


if __name__ == "__main__":
    unittest.main()
