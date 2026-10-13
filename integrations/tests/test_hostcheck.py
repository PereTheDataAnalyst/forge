"""Host and Origin allow-list rules, written once and checked directly."""

from __future__ import annotations

import unittest

from forge_integrations.config import ConfigError, McpSettings
from forge_integrations.security.hostcheck import (
    LOOPBACK_HOSTS,
    HostPolicy,
    OriginPolicy,
    normalise_origin,
    parse_host_rule,
    parse_origin_rule,
)


class HostRuleTests(unittest.TestCase):
    def test_rule_forms(self):
        self.assertEqual(parse_host_rule("MCP.Example.com"), ("mcp.example.com", None))
        self.assertEqual(parse_host_rule("mcp.example.com:8443"), ("mcp.example.com", "8443"))
        self.assertEqual(parse_host_rule("mcp.example.com:*"), ("mcp.example.com", "*"))
        self.assertEqual(parse_host_rule("[::1]:9000"), ("[::1]", "9000"))
        self.assertEqual(parse_host_rule("127.0.0.1"), ("127.0.0.1", None))

    def test_bad_rules_are_refused_with_a_readable_message(self):
        for rule in ("", "*", "*.example.com", "http://example.com", "example.com/path", "a b", "example.com:0", "example.com:70000", "example.com:x", "user@example.com", "-bad.example.com", "ex_ample.com", "é.example.com"):
            with self.subTest(rule=rule):
                with self.assertRaises(ValueError) as caught:
                    parse_host_rule(rule)
                self.assertIn("not a valid host entry", str(caught.exception))


class HostPolicyTests(unittest.TestCase):
    def test_loopback_is_the_default_and_matches_any_port(self):
        policy = HostPolicy()
        self.assertEqual(tuple(h for h, _ in policy.rules), LOOPBACK_HOSTS)
        for header in ("localhost", "localhost:3000", "127.0.0.1:1", "[::1]:65535", "LocalHost:80"):
            self.assertTrue(policy.allows(header), header)
        for header in ("0.0.0.0", "127.0.0.2", "localhost.evil.com", "evil.com", "[::2]"):
            self.assertFalse(policy.allows(header), header)

    def test_a_name_without_a_port_matches_any_port_and_an_exact_port_matches_only_that_port(self):
        policy = HostPolicy(["a.example.com", "b.example.com:8443", "c.example.com:*"])
        for header in ("a.example.com", "a.example.com:1", "b.example.com:8443", "c.example.com:2", "C.EXAMPLE.COM:9"):
            self.assertTrue(policy.allows(header), header)
        for header in ("b.example.com", "b.example.com:443", "d.example.com", "example.com", "a.example.com.evil.com"):
            self.assertFalse(policy.allows(header), header)

    def test_configured_entries_replace_the_loopback_default(self):
        self.assertFalse(HostPolicy(["mcp.example.com"]).allows("localhost"))

    def test_malformed_headers_are_never_allowed(self):
        policy = HostPolicy(["example.com", "[::1]"])
        for header in (None, "", " ", "a b", "example.com:*", "example.com:0", "example.com:99999", "example.com:80:80", "[::1", "::1", "user@example.com", "example.com/x", "example.com\\x", "exaémple.com", "x" * 400, "example.com.", ".example.com"):
            self.assertFalse(policy.allows(header), repr(header))

    def test_sdk_patterns_say_the_same_thing(self):
        patterns = HostPolicy(["a.example.com", "b.example.com:8443", "c.example.com:*"]).sdk_patterns()
        self.assertEqual(patterns, ["a.example.com", "a.example.com:*", "b.example.com:8443", "c.example.com:*"])


class OriginTests(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(normalise_origin("HTTPS://App.Example.com"), "https://app.example.com")
        self.assertEqual(normalise_origin(" http://localhost:3000 "), "http://localhost:3000")
        for value in (None, "", "null", "*", "ftp://x.com", "https://", "https://x.com/path", "https://x.com?q=1", "https://*.x.com", "https://x.com:99999", "https://user@x.com", "javascript:alert(1)", "file://"):
            self.assertIsNone(normalise_origin(value), repr(value))

    def test_a_default_port_is_a_different_origin_because_browsers_omit_it(self):
        policy = OriginPolicy(["https://app.example.com"])
        self.assertTrue(policy.allows("https://app.example.com"))
        self.assertFalse(policy.allows("https://app.example.com:443"))

    def test_rules_and_matching(self):
        policy = OriginPolicy(["https://app.example.com", "HTTP://localhost:3000"])
        self.assertTrue(policy.allows(None))  # not a browser request
        self.assertEqual(policy.matches("HTTPS://APP.example.com"), "https://app.example.com")
        for origin in ("https://evil.com", "http://app.example.com", "https://sub.app.example.com", "null", "*"):
            self.assertFalse(policy.allows(origin), origin)
            self.assertIsNone(policy.matches(origin))
        self.assertIsNone(policy.matches(None))

    def test_bad_rules_are_refused(self):
        for rule in ("*", "https://*.example.com", "app.example.com", "https://app.example.com/", "null"):
            with self.subTest(rule=rule):
                with self.assertRaises(ValueError):
                    parse_origin_rule(rule)

    def test_no_origins_means_no_browser_is_allowed(self):
        self.assertFalse(OriginPolicy().allows("https://anything.example"))


class SettingsValidateTheListsAtLoadTimeTests(unittest.TestCase):
    def test_bad_entries_stop_start_up_rather_than_failing_open(self):
        with self.assertRaises(ConfigError):
            McpSettings(allowed_hosts=("*",))
        with self.assertRaises(ConfigError):
            McpSettings(allowed_origins=("https://*.example.com",))
        McpSettings(allowed_hosts=("mcp.example.com:443",), allowed_origins=("https://app.example.com",))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
