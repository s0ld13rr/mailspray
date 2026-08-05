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

    def test_run_tracking_tags_findings(self):
        db = self._db("runs_ws")
        rid = db.start_run("imap", "mail.corp", "module", module="cred_scan",
                           users=1, passwords=1)
        self.assertIsNotNone(rid)
        db.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        db.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                    "password=secret1", "", "INBOX | s | uid 1")
        db.finish_run()

        runs = db.get_runs()
        self.assertEqual(len(runs), 1)
        # runs row: id, started, protocol, target, mode, module, users, passwords, found, loot
        self.assertEqual(runs[0][0], rid)
        self.assertEqual(runs[0][5], "cred_scan")   # module
        self.assertEqual(runs[0][8], 1)             # found backfilled
        self.assertEqual(runs[0][9], 1)             # loot backfilled

        # findings are tagged with the run id
        self.assertEqual(db.get_credentials(run=rid)[0][0], rid)
        self.assertEqual(db.get_loot(run=rid)[0][0], rid)
        self.assertEqual(db.get_loot(run=999), [])  # filter isolates runs
        db.close()

    def test_loot_filters(self):
        db = self._db("filter_ws")
        db.start_run("owa", "owa.corp", "module", module="gal")
        db.add_loot("gal", "owa", "owa.corp", "alice", "gal", "bob@corp.com", "Bob", "gal")
        db.add_loot("gal", "owa", "owa.corp", "alice", "gal", "eve@corp.com", "Eve", "gal")
        self.assertEqual(len(db.get_loot(module="gal")), 2)
        self.assertEqual(len(db.get_loot(category="gal")), 2)
        self.assertEqual(len(db.get_loot(module="cred_scan")), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
