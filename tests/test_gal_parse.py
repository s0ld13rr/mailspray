import unittest

from mailspray.modules.gal import GalModule


_FINDPEOPLE_JSON = {
    "Body": {
        "__type": "FindPeopleJsonResponse:#Exchange",
        "ResultSet": [
            {"DisplayName": "Bob Smith",
             "EmailAddresses": [{"EmailAddress": "bob@corp.com"}]},
            {"DisplayName": "Carol Jones",
             "EmailAddresses": [{"EmailAddress": "carol@corp.com"},
                                {"EmailAddress": "c.jones@corp.com"}]},
            {"DisplayName": "No Email Persona", "EmailAddresses": []},
        ],
        "TotalNumberOfPeopleInView": 3,
    }
}

_RESOLVENAMES_XML = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <m:ResolveNamesResponse
        xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
        xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <m:ResponseMessages>
        <m:ResolveNamesResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:ResolutionSet TotalItemsInView="2" IncludesLastItemInRange="true">
            <t:Resolution>
              <t:Mailbox>
                <t:Name>Alice Admin</t:Name>
                <t:EmailAddress>alice@corp.com</t:EmailAddress>
                <t:RoutingType>SMTP</t:RoutingType>
                <t:MailboxType>Mailbox</t:MailboxType>
              </t:Mailbox>
            </t:Resolution>
            <t:Resolution>
              <t:Mailbox>
                <t:Name>Andrew B</t:Name>
                <t:EmailAddress>andrew@corp.com</t:EmailAddress>
                <t:RoutingType>SMTP</t:RoutingType>
              </t:Mailbox>
            </t:Resolution>
          </m:ResolutionSet>
        </m:ResolveNamesResponseMessage>
      </m:ResponseMessages>
    </m:ResolveNamesResponse>
  </s:Body>
</s:Envelope>"""


class FakeCtx:
    def __init__(self):
        self.protocol = "owa"
        self.host = "owa.corp.com"
        self.username = "alice"
        self.loot = []
        self.loot_count = 0

    def emit_loot(self, category, key, value="", source=""):
        self.loot_count += 1
        self.loot.append((category, key, value))

    def log_info(self, *a, **k):
        pass

    log_good = log_warn = log_info


class TestGalParse(unittest.TestCase):
    def setUp(self):
        self.mod = GalModule()
        self.mod.options({})

    def test_parse_findpeople(self):
        entries, total, personas = self.mod._parse_findpeople(_FINDPEOPLE_JSON)
        emails = [e for e, _ in entries]
        self.assertIn("bob@corp.com", emails)
        self.assertIn("carol@corp.com", emails)
        self.assertIn("c.jones@corp.com", emails)
        self.assertEqual(total, 3)
        # 1 (Bob) + 2 (Carol) + 0 (no-email persona) = 3 address rows
        self.assertEqual(len(entries), 3)
        # persona count is 3 (used for pagination, distinct from the 3 addresses)
        self.assertEqual(personas, 3)

    def test_parse_findpeople_nulls(self):
        # JSON null Body/ResultSet must not raise
        self.assertEqual(self.mod._parse_findpeople({"Body": None}), ([], None, 0))
        self.assertEqual(self.mod._parse_findpeople(None), ([], None, 0))

    def test_parse_resolvenames(self):
        entries = self.mod._parse_resolvenames(_RESOLVENAMES_XML)
        emails = {e for e, _ in entries}
        names = {n for _, n in entries}
        self.assertEqual(emails, {"alice@corp.com", "andrew@corp.com"})
        self.assertIn("Alice Admin", names)

    def test_parse_resolvenames_malformed(self):
        self.assertEqual(self.mod._parse_resolvenames("<not xml"), [])

    def test_parse_resolvenames_ex_prefers_smtp(self):
        xml = """<?xml version="1.0"?>
        <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
         <s:Body><m:ResolveNamesResponse
           xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
           xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
          <m:ResponseMessages><m:ResolveNamesResponseMessage>
           <m:ResolutionSet><t:Resolution>
            <t:Mailbox>
              <t:Name>Dave EX</t:Name>
              <t:EmailAddress>/o=Corp/ou=Exchange/cn=Recipients/cn=dave</t:EmailAddress>
              <t:RoutingType>EX</t:RoutingType>
            </t:Mailbox>
            <t:Contact>
              <t:EmailAddresses>
                <t:Entry Key="EmailAddress1">SMTP:dave@corp.com</t:Entry>
              </t:EmailAddresses>
            </t:Contact>
           </t:Resolution></m:ResolutionSet>
          </m:ResolveNamesResponseMessage></m:ResponseMessages>
         </m:ResolveNamesResponse></s:Body></s:Envelope>"""
        entries = self.mod._parse_resolvenames(xml)
        emails = {e for e, _ in entries}
        self.assertIn("dave@corp.com", emails)                 # SMTP from Contact chosen
        self.assertNotIn("/o=Corp/ou=Exchange/cn=Recipients/cn=dave", emails)  # legacy DN skipped

    def test_record_dedup(self):
        ctx = FakeCtx()
        seen = {}
        self.assertTrue(self.mod._record(ctx, seen, "Bob@corp.com", "Bob"))
        self.assertFalse(self.mod._record(ctx, seen, "bob@corp.com", "Bob"))  # case-insensitive dup
        self.assertEqual(ctx.loot_count, 1)


if __name__ == "__main__":
    unittest.main()
