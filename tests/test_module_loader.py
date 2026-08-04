import unittest

from mailspray.core import module
from mailspray.core.module import BaseMSModule


class TestModuleLoader(unittest.TestCase):
    def test_discovers_both_modules(self):
        mods = module.list_modules()
        self.assertIn("cred_scan", mods)
        self.assertIn("gal", mods)

    def test_get_module_returns_instance(self):
        m = module.get_module("cred_scan")
        self.assertIsInstance(m, BaseMSModule)
        self.assertEqual(m.name, "cred_scan")
        self.assertEqual(m.supported_protocols, ["imap"])

    def test_gal_protocols(self):
        m = module.get_module("gal")
        self.assertEqual(sorted(m.supported_protocols), ["ews", "owa"])

    def test_unknown_module(self):
        self.assertIsNone(module.get_module("does_not_exist"))

    def test_all_modules_have_metadata(self):
        for name, cls in module.list_modules().items():
            self.assertTrue(cls.description, f"{name} missing description")
            self.assertTrue(cls.supported_protocols, f"{name} missing supported_protocols")


if __name__ == "__main__":
    unittest.main()
