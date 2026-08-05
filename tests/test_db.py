import os
import tempfile
import unittest

from mailspray.core.findings import FindingsStore, list_workspaces


class TestFindingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _store(self, name="default"):
        return FindingsStore(workspace=name, base_dir=self.tmp)

    def test_dir_created_on_first_write(self):
        store = self._store()
        self.assertFalse(os.path.isdir(store.dir))
        store.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        self.assertTrue(os.path.isdir(store.dir))

    def test_add_credential_and_dedup(self):
        store = self._store()
        store.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        store.add_credential("imap", "mail.corp", 993, "alice", "P@ss")  # dup
        store.add_credential("imap", "mail.corp", 993, "bob", "P@ss")
        creds = store.get_credentials()
        self.assertEqual(len(creds), 2)

    def test_add_loot_and_dedup(self):
        store = self._store()
        store.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                       "password=secret", "", "INBOX | s | uid 1")
        store.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                       "password=secret", "", "INBOX | s | uid 1")  # dup
        store.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                       "bob@corp.com", "Bob", "global-address-list")
        cs_lines = store.get_loot("cred_scan.dump")
        gal_lines = store.get_loot("gal.dump")
        self.assertEqual(len(cs_lines), 1)
        self.assertEqual(len(gal_lines), 1)

    def test_context_manager(self):
        with self._store("ws2") as store:
            store.add_credential("owa", "owa.corp", 443, "carol", "pw")
            self.assertEqual(len(store.get_credentials()), 1)

    def test_saved_returns_written_files(self):
        store = self._store()
        store.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        store.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                       "bob@corp.com", "Bob", "gal")
        saved = store.saved()
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(count > 0 for _, count in saved))

    def test_start_finish_run_noop(self):
        store = self._store()
        store.start_run("imap", "mail.corp", "spray")
        store.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        store.finish_run()
        self.assertEqual(len(store.get_credentials()), 1)

    def test_gal_loot_format(self):
        store = self._store()
        store.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                       "bob@corp.com", "Bob", "gal")
        store.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                       "eve@corp.com", "Eve", "gal")
        lines = store.get_loot("gal.dump")
        self.assertEqual(len(lines), 2)
        self.assertIn("\t", lines[0])

    def test_get_loot_files(self):
        store = self._store()
        store.add_loot("cred_scan", "imap", "mail.corp", "alice", "password",
                       "pw=secret", "", "INBOX | s | uid 1")
        store.add_loot("gal", "owa", "owa.corp", "alice", "gal",
                       "bob@corp.com", "Bob", "gal")
        files = store.get_loot_files()
        names = {fn for fn, _ in files}
        self.assertIn("cred_scan.dump", names)
        self.assertIn("gal.dump", names)

    def test_list_workspaces(self):
        s1 = self._store("alpha")
        s1.add_credential("imap", "h", 993, "u", "p")
        s2 = self._store("beta")
        s2.add_credential("imap", "h", 993, "u", "p")
        wss = list_workspaces(base_dir=self.tmp)
        names = [n for n, _ in wss]
        self.assertIn("alpha", names)
        self.assertIn("beta", names)

    def test_cross_run_dedup(self):
        """Credentials from a prior run are not duplicated by a second store instance."""
        s1 = self._store()
        s1.add_credential("imap", "mail.corp", 993, "alice", "P@ss")
        s1.close()

        s2 = self._store()
        s2.add_credential("imap", "mail.corp", 993, "alice", "P@ss")  # dup from disk
        s2.add_credential("imap", "mail.corp", 993, "bob", "P@ss")
        creds = s2.get_credentials()
        self.assertEqual(len(creds), 2)


if __name__ == "__main__":
    unittest.main()
