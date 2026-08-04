import os
import tempfile
import unittest

from mailspray.core.db import WorkspaceDB


class TestWorkspaceDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _db(self, name="default"):
        return WorkspaceDB(name=name, base_dir=self.tmp)

    def test_file_created(self):
        db = self._db()
        self.assertTrue(db.available)
        self.assertTrue(os.path.isfile(db.path))
        db.close()

    def test_add_credential_and_dedup(self):
        db = self._db()
        db.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        db.add_credential("imap", "mail.corp", 993, "alice", "P@ss")  # dup
        db.add_credential("imap", "mail.corp", 993, "bob", "P@ss")
        self.assertEqual(db.count("credentials"), 2)
        db.close()

    def test_add_loot_and_dedup(self):
        db = self._db()
        db.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                    "password=secret", "", "INBOX | s | uid 1")
        db.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                    "password=secret", "", "INBOX | s | uid 1")  # dup
        db.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                    "bob@corp.com", "Bob", "global-address-list")
        self.assertEqual(db.count("loot"), 2)
        db.close()

    def test_context_manager(self):
        with self._db("ws2") as db:
            db.add_credential("owa", "owa.corp", 443, "carol", "pw")
            self.assertEqual(db.count("credentials"), 1)


if __name__ == "__main__":
    unittest.main()
