import unittest

from mailspray.core import patterns


class TestPatterns(unittest.TestCase):
    def _cats(self, text):
        return {c for c, _ in patterns.scan_text(text)}

    def test_password_assignment(self):
        self.assertIn("password", self._cats("db_password = Sup3rSecret!"))
        self.assertIn("password", self._cats("PWD: hunter2xx"))

    def test_private_key(self):
        pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaAAAA...\n"
        self.assertIn("private_key", self._cats(pem))

    def test_aws_access_key(self):
        self.assertIn("aws_access_key", self._cats("key: AKIAIOSFODNN7EXAMPLE done"))

    def test_url_credentials(self):
        self.assertIn("url_credentials", self._cats("db url postgres://user:p4ss@db.local:5432/app"))

    def test_connection_string(self):
        cs = "Server=sql01;Database=hr;User Id=sa;Password=Str0ng!;"
        self.assertIn("connection_string", self._cats(cs))

    def test_bearer_token(self):
        self.assertIn("bearer_token", self._cats("Authorization: Bearer abcdef0123456789ABCDEF"))

    def test_openvpn(self):
        ovpn = "client\nremote vpn.corp.com 1194\nauth-user-pass\n"
        cats = self._cats(ovpn)
        self.assertIn("openvpn", cats)
        self.assertIn("openvpn_remote", cats)

    def test_jwt(self):
        jwt = "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4"
        self.assertIn("jwt", self._cats(jwt))

    # ── negatives: ordinary prose must not trigger ──
    def test_no_false_positive_prose(self):
        prose = (
            "Please reset your password using the portal. The meeting about API "
            "access is at 3pm. Remote work is allowed on Fridays. Contact the "
            "server team for the secret santa list."
        )
        self.assertEqual(self._cats(prose), set())

    def test_no_false_positive_empty(self):
        self.assertEqual(patterns.scan_text(""), [])
        self.assertEqual(patterns.scan_text(None), [])

    def test_no_false_positive_boarding_pass(self):
        # bare "pass" keyword was removed; marketing "pass:" must not fire
        self.assertNotIn("password", self._cats("Your boarding pass: ABCDEF is ready"))
        self.assertNotIn("password", self._cats("Season pass: RENEW today and save"))

    def test_no_false_positive_call_to_action(self):
        # keyword matches but the value is a plain word -> validator rejects
        self.assertNotIn("password", self._cats("Reset your password: click the button"))

    def test_real_password_still_matches(self):
        # values with digits/symbols or long passphrases survive the validator
        self.assertIn("password", self._cats("password=Sup3rSecret!"))
        self.assertIn("password", self._cats("db_password = Pr0dP@ss2025"))
        self.assertIn("password", self._cats("passphrase: correcthorsebattery"))

    def test_dedup(self):
        text = "password=S3cret!\npassword=S3cret!\n"
        # identical (category, context) collapses
        hits = patterns.scan_text(text)
        self.assertEqual(len([h for h in hits if h[0] == "password"]), 1)


if __name__ == "__main__":
    unittest.main()
