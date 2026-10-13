import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from forge_integrations.config import load_settings
from forge_integrations.errors import ConfigError, ErrorCode, ForgeError
from forge_integrations.security.http_limits import read_all_limited
from forge_integrations.security.outbound import validate_outbound_url
from forge_integrations.security.redaction import REDACTED, Redactor, redact, redact_text
from forge_integrations.security.safe_files import SecureTempStore, safe_download_filename
from forge_integrations.security.tokens import TokenHasher, generate_token, parse_token

PEPPER = "p" * 48


class RedactionTests(unittest.TestCase):
    def test_sensitive_keys_are_replaced_case_and_hyphen_insensitively(self):
        data = {"Authorization": "Bearer abcdefgh12345678", "X-API-Key": "k", "name": "ok", "hf_token": "x"}
        out = redact(data)
        self.assertEqual(out["Authorization"], REDACTED)
        self.assertEqual(out["X-API-Key"], REDACTED)
        self.assertEqual(out["hf_token"], REDACTED)
        self.assertEqual(out["name"], "ok")

    def test_token_counts_are_not_treated_as_secrets(self):
        out = redact({"max_tokens": 10, "prompt_tokens": 3, "total_tokens": 13})
        self.assertEqual(out, {"max_tokens": 10, "prompt_tokens": 3, "total_tokens": 13})

    def test_secret_shapes_inside_strings(self):
        gen = generate_token()
        text = f"call failed with Bearer {gen.token} and hf_{'a' * 30} and sk-{'b' * 24}"
        out = redact_text(text)
        self.assertNotIn(gen.token, out)
        self.assertNotIn("a" * 30, out)
        self.assertNotIn("b" * 24, out)
        self.assertIn("REDACTED", out)

    def test_forge_token_alone_is_redacted(self):
        gen = generate_token()
        self.assertEqual(redact_text(f"token={gen.token}"), "token=[REDACTED_FORGE_TOKEN]")

    def test_does_not_mutate_input_and_handles_bytes(self):
        data = {"audio": b"\x00" * 10, "payload": b"abc", "nested": {"password": "pw"}}
        before = repr(data)
        out = redact(data)
        self.assertEqual(repr(data), before)
        self.assertEqual(out["audio"], REDACTED)
        self.assertEqual(out["payload"], "<3 bytes>")
        self.assertEqual(out["nested"]["password"], REDACTED)

    def test_bounds(self):
        r = Redactor(max_string_chars=10, max_items=2, max_depth=2)
        self.assertIn("truncated", r.value("x" * 50))
        self.assertEqual(len(r.value([1, 2, 3, 4])), 3)
        deep = {"a": {"b": {"c": {"d": 1}}}}
        self.assertEqual(r.value(deep)["a"]["b"]["c"], "[MAX_DEPTH]")


class TokenTests(unittest.TestCase):
    def test_generate_parse_hash_verify(self):
        gen = generate_token()
        parsed = parse_token(gen.token)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.prefix, gen.prefix)
        self.assertEqual(parsed.kind, "fmcp")
        hasher = TokenHasher(PEPPER)
        digest = hasher.hash(gen.token)
        self.assertEqual(len(digest), 64)
        self.assertTrue(hasher.verify(gen.token, digest))
        self.assertFalse(hasher.verify(gen.token + "x", digest))
        self.assertFalse(hasher.verify(gen.token, "not-hex-é"))

    def test_tokens_are_unique_and_high_entropy_shape(self):
        tokens = {generate_token().token for _ in range(200)}
        self.assertEqual(len(tokens), 200)
        self.assertTrue(all(len(t) == len("fmcp_") + 8 + 1 + 43 for t in tokens))

    def test_parse_rejects_malformed(self):
        for bad in ["", "fmcp_", "fmcp_zzzzzzzz_" + "a" * 43, "FMCP_" + "a" * 8 + "_" + "a" * 43, "x" * 500, None, 5]:
            self.assertIsNone(parse_token(bad))

    def test_pepper_must_be_long_enough(self):
        with self.assertRaises(ConfigError):
            TokenHasher("short")

    def test_different_peppers_produce_different_hashes(self):
        gen = generate_token()
        self.assertNotEqual(TokenHasher("a" * 40).hash(gen.token), TokenHasher("b" * 40).hash(gen.token))


class SecureTempStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SecureTempStore(Path(self.tmp.name) / "uploads")

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_is_created_with_random_name_and_removed(self):
        with self.store.write(b"data", ".wav") as path:
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"data")
            self.assertEqual(path.parent, self.store.base_dir.resolve())
            self.assertRegex(path.name, r"^[0-9a-f]{32}\.wav$")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse(path.exists())

    def test_removed_even_when_body_raises(self):
        captured = []
        with self.assertRaises(RuntimeError):
            with self.store.write(b"x", ".ogg") as path:
                captured.append(path)
                raise RuntimeError("boom")
        self.assertFalse(captured[0].exists())

    def test_suffix_allowlist_blocks_traversal_and_executables(self):
        for bad in ["/../evil", ".exe", ".sh", "../x.wav", ".WAV", ""]:
            with self.assertRaises(ValueError):
                with self.store.write(b"x", bad):
                    pass

    def test_cleanup_stale(self):
        with self.store.write(b"x", ".bin") as path:
            old = time.time() - 10_000
            os.utime(path, (old, old))
            self.assertEqual(self.store.cleanup_stale(), 1)
        self.assertEqual(self.store.cleanup_stale(), 0)

    def test_download_filename_sanitiser(self):
        self.assertEqual(safe_download_filename("../../etc/passwd"), "etc_passwd")
        self.assertEqual(safe_download_filename("Eval run: Yoruba #1"), "Eval_run__Yoruba__1")
        self.assertEqual(safe_download_filename("..."), "export")
        self.assertEqual(len(safe_download_filename("a" * 500)), 80)


def fake_resolver(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]

    return resolve


class OutboundPolicyTests(unittest.TestCase):
    def reason(self, **kwargs):
        with self.assertRaises(ForgeError) as ctx:
            validate_outbound_url(**kwargs)
        self.assertEqual(ctx.exception.code, ErrorCode.OUTBOUND_BLOCKED)
        return ctx.exception.details["reason"]

    def test_allows_allowlisted_public_https(self):
        t = validate_outbound_url(
            "https://asr.example.org/v1/audio/transcriptions",
            allowed_hosts=["asr.example.org"],
            resolver=fake_resolver({"asr.example.org": ["93.184.216.34"]}),
        )
        self.assertEqual((t.scheme, t.host, t.port), ("https", "asr.example.org", 443))

    def test_blocks_non_allowlisted_host(self):
        self.assertEqual(
            self.reason(url="https://evil.example/", allowed_hosts=["asr.example.org"],
                        resolver=fake_resolver({"evil.example": ["93.184.216.34"]})),
            "host_not_allowed",
        )

    def test_empty_allowlist_blocks_everything(self):
        self.assertEqual(self.reason(url="https://asr.example.org/", allowed_hosts=[]), "no_allowlist")

    def test_blocks_schemes_credentials_and_bad_urls(self):
        for url, why in [
            ("file:///etc/passwd", "scheme"),
            ("ftp://asr.example.org/", "scheme"),
            ("https://user:pw@asr.example.org/", "credentials_in_url"),
            ("https:///nohost", "no_host"),
            ("https://asr.example.org:99999/", "malformed_url"),
        ]:
            self.assertEqual(self.reason(url=url, allowed_hosts=["asr.example.org"]), why, url)

    def test_private_and_loopback_blocked_unless_opted_in(self):
        resolver = fake_resolver({"asr.internal": ["10.0.0.5"], "localhost": ["127.0.0.1"]})
        self.assertEqual(
            self.reason(url="https://asr.internal/", allowed_hosts=["asr.internal"], resolver=resolver),
            "blocked_address",
        )
        ok = validate_outbound_url(
            "http://localhost:8090/", allowed_hosts=["localhost"], allow_private=True, resolver=resolver
        )
        self.assertEqual(ok.port, 8090)

    def test_plain_http_requires_opt_in(self):
        resolver = fake_resolver({"asr.example.org": ["93.184.216.34"]})
        self.assertEqual(
            self.reason(url="http://asr.example.org/", allowed_hosts=["asr.example.org"], resolver=resolver),
            "insecure_http",
        )
        validate_outbound_url(
            "http://asr.example.org/", allowed_hosts=["asr.example.org"], allow_insecure_http=True, resolver=resolver
        )

    def test_metadata_and_link_local_always_blocked_even_in_dev_mode(self):
        resolver = fake_resolver({"meta": ["169.254.169.254"], "v6": ["fe80::1"], "mapped": ["::ffff:169.254.169.254"]})
        for host in ("meta", "v6", "mapped"):
            self.assertEqual(
                self.reason(url=f"http://{host}/", allowed_hosts=[host], allow_private=True, resolver=resolver),
                "blocked_address",
                host,
            )

    def test_any_blocked_address_among_several_blocks(self):
        resolver = fake_resolver({"mixed": ["93.184.216.34", "10.0.0.1"]})
        self.assertEqual(
            self.reason(url="https://mixed/", allowed_hosts=["mixed"], resolver=resolver), "blocked_address"
        )

    def test_unresolvable(self):
        self.assertEqual(
            self.reason(url="https://nope.example/", allowed_hosts=["nope.example"], resolver=fake_resolver({})),
            "unresolvable",
        )


class LimitTests(unittest.TestCase):
    def test_read_all_limited(self):
        import io

        self.assertEqual(read_all_limited(io.BytesIO(b"abc").read, 10), b"abc")
        self.assertEqual(read_all_limited(io.BytesIO(b"abcd").read, 4), b"abcd")
        with self.assertRaises(ForgeError) as ctx:
            read_all_limited(io.BytesIO(b"abcde").read, 4)
        self.assertEqual(ctx.exception.http_status, 413)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_derived_allowlist(self):
        s = load_settings({"ASR_SERVICE_URL": "http://127.0.0.1:8090", "ALLOW_PRIVATE_NETWORK_TARGETS": "true"})
        self.assertEqual(s.voice.asr_allowed_hosts, ("127.0.0.1",))
        self.assertTrue(s.voice.allow_private_targets)
        self.assertEqual(s.voice.max_seconds, 30)
        self.assertIsNone(s.mcp.token_pepper)

    def test_voice_seconds_cannot_exceed_the_official_window(self):
        with self.assertRaises(ConfigError):
            load_settings({"VOICE_MAX_SECONDS": "31"})

    def test_bad_values_fail_loudly(self):
        for env in [{"EVAL_MAX_CONCURRENCY": "0"}, {"VOICE_MAX_UPLOAD_BYTES": "abc"}, {"ALLOW_PRIVATE_NETWORK_TARGETS": "maybe"}]:
            with self.assertRaises(ConfigError):
                load_settings(env)

    def test_secrets_not_in_repr(self):
        s = load_settings({"MCP_TOKEN_PEPPER": "x" * 40, "ASR_SERVICE_TOKEN": "supersecrettoken"})
        text = repr(s)
        self.assertNotIn("x" * 40, text)
        self.assertNotIn("supersecrettoken", text)


class ErrorPayloadTests(unittest.TestCase):
    def test_payload_shape_and_redaction(self):
        err = ForgeError("provider_error", "Upstream failed.", http_status=502, details={"token": "abc", "hint": "retry"})
        payload = err.to_payload("req-1")
        self.assertEqual(payload["error"]["code"], "provider_error")
        self.assertEqual(payload["error"]["request_id"], "req-1")
        self.assertEqual(payload["error"]["details"]["token"], REDACTED)
        self.assertEqual(payload["error"]["details"]["hint"], "retry")


if __name__ == "__main__":
    unittest.main()
