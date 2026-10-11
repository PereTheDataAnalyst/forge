"""Unit tests for dependency-free security and networking primitives."""

import re

from app.core.net import normalize_origin, origin_allowed, resolve_client_ip
from app.core.redaction import REDACTED, is_sensitive_key, redact_mapping, redact_text
from app.core.scopes import KNOWN_SCOPES, register_scope
from app.security.password_policy import validate_password
from app.security.tokens import (
    csrf_token_for_session,
    generate_api_key,
    generate_session_token,
    hash_api_key,
    hash_session_token,
    parse_api_key,
    verify_api_key,
    verify_csrf_token,
)

PEPPER = b"p" * 32
SECRET = b"s" * 32


# --- redaction ---------------------------------------------------------------------------


def test_redact_api_key_keeps_public_prefix_only():
    key = generate_api_key().full_key
    redacted = redact_text(f"calling with {key} now")
    assert key not in redacted
    assert f"{key[:14]}_{REDACTED}" in redacted


def test_redact_bearer_and_hf_tokens():
    text = "Authorization: Bearer abcdefghijklmnop and hf_abcdefghijklmnopqrstuvwx"
    redacted = redact_text(text)
    assert "abcdefghijklmnop" not in redacted
    assert "hf_abcdefghijklmnopqrstuvwx" not in redacted


def test_redact_database_url_password():
    url = "postgresql+asyncpg://forge_app:s3cretValue@localhost:5432/forge_dev"
    redacted = redact_text(url)
    assert "s3cretValue" not in redacted
    assert redacted == f"postgresql+asyncpg://forge_app:{REDACTED}@localhost:5432/forge_dev"


def test_redact_session_cookie_pair():
    redacted = redact_text("cookie forge_session=abc123def456; other=1")
    assert "abc123def456" not in redacted


def test_redact_mapping_masks_sensitive_keys_but_keeps_usage_counts():
    data = {
        "password": "hunter2hunter2",
        "prompt_tokens": 5,
        "max_tokens": 100,
        "headers": {"Authorization": "Bearer abcdefghijklmnop", "x-csrf-token": "zzz"},
        "items": [{"api_key": "k"}, "plain"],
    }
    out = redact_mapping(data)
    assert out["password"] == REDACTED
    assert out["prompt_tokens"] == 5
    assert out["max_tokens"] == 100
    assert out["headers"]["Authorization"] == REDACTED
    assert out["headers"]["x-csrf-token"] == REDACTED
    assert out["items"][0]["api_key"] == REDACTED
    assert out["items"][1] == "plain"


def test_sensitive_key_detection():
    for key in ("password", "natlas_inference_token", "session_token", "X-CSRF-Token", "token"):
        assert is_sensitive_key(key), key
    for key in ("max_tokens", "total_tokens", "completion_tokens", "user_id", "request_id"):
        assert not is_sensitive_key(key), key


def test_redact_mapping_depth_guard():
    deep: dict = {}
    cursor = deep
    for _ in range(20):
        cursor["x"] = {}
        cursor = cursor["x"]
    assert redact_mapping(deep) is not None


# --- network helpers ---------------------------------------------------------------------


def test_client_ip_without_proxy_uses_socket_peer():
    assert resolve_client_ip("10.0.0.9", "1.2.3.4", 0) == "10.0.0.9"
    assert resolve_client_ip(None, None, 0) == "unknown"


def test_client_ip_with_one_trusted_proxy():
    assert resolve_client_ip("10.0.0.1", "1.2.3.4", 1) == "1.2.3.4"


def test_client_ip_ignores_spoofed_leading_entries():
    assert resolve_client_ip("10.0.0.1", "6.6.6.6, 1.2.3.4", 1) == "1.2.3.4"


def test_client_ip_with_two_trusted_proxies():
    assert resolve_client_ip("10.0.0.3", "1.2.3.4, 10.0.0.2", 2) == "1.2.3.4"


def test_client_ip_invalid_candidate_falls_back_to_peer():
    assert resolve_client_ip("10.0.0.1", "not-an-ip", 1) == "10.0.0.1"


def test_normalize_origin():
    assert normalize_origin("HTTP://LocalHost:5173/") == "http://localhost:5173"
    assert normalize_origin("https://Example.com:443") == "https://example.com"
    assert normalize_origin("http://[::1]:8000") == "http://[::1]:8000"
    assert normalize_origin("javascript:alert(1)") is None
    assert normalize_origin("null") is None
    assert normalize_origin("") is None
    assert normalize_origin(None) is None


def test_origin_allowed():
    allowed = {"https://forge.example.com"}
    assert origin_allowed("https://FORGE.example.com", allowed)
    assert not origin_allowed("https://evil.example.com", allowed)
    assert not origin_allowed("null", allowed)
    assert not origin_allowed(None, allowed)


# --- tokens ------------------------------------------------------------------------------


def test_api_key_format_and_parsing():
    generated = generate_api_key()
    assert re.fullmatch(r"nfk_[0-9a-f]{10}_[A-Za-z0-9_-]{43}", generated.full_key)
    assert generated.prefix == generated.full_key[:14]
    assert parse_api_key(generated.full_key) == generated.prefix
    for bad in ("", "nfk_", "sk_abc", generated.full_key + "x", generated.full_key[:-1]):
        assert parse_api_key(bad) is None, bad


def test_api_key_prefixes_are_unique_in_practice():
    assert len({generate_api_key().prefix for _ in range(200)}) == 200


def test_api_key_hash_roundtrip():
    generated = generate_api_key()
    stored = hash_api_key(generated.full_key, PEPPER)
    assert len(stored) == 64
    assert verify_api_key(generated.full_key, stored, PEPPER)
    assert not verify_api_key(generated.full_key, stored, b"q" * 32)
    assert not verify_api_key(generate_api_key().full_key, stored, PEPPER)
    assert generated.full_key not in stored


def test_session_token_hashing():
    token = generate_session_token()
    assert len(token) >= 43
    digest = hash_session_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == hash_session_token(token)
    assert digest != hash_session_token(generate_session_token())


def test_csrf_token_is_bound_to_session_and_secret():
    first = hash_session_token(generate_session_token())
    second = hash_session_token(generate_session_token())
    token = csrf_token_for_session(first, SECRET)
    assert verify_csrf_token(token, first, SECRET)
    assert not verify_csrf_token(token, second, SECRET)
    assert not verify_csrf_token(token, first, b"x" * 32)
    assert not verify_csrf_token("", first, SECRET)


# --- password policy ---------------------------------------------------------------------


def test_password_policy_accepts_reasonable_password():
    assert validate_password("Correct-Horse-Battery-9", "ada@example.com") == []


def test_password_policy_rejects_weak_passwords():
    assert validate_password("short1")
    assert validate_password("aaaaaaaaaaaa")
    assert validate_password("password123")
    assert validate_password("   " * 5)
    assert validate_password("x" * 200)


def test_password_policy_rejects_email_local_part():
    assert validate_password("adaobi.nwosu-2026!", "adaobi.nwosu@example.com")


# --- scopes ------------------------------------------------------------------------------


def test_register_scope_validates_names():
    register_scope("asr.transcribe")
    assert "asr.transcribe" in KNOWN_SCOPES
    for bad in ("Chat", "chat", "chat.", ".create", "chat create", "a.b"):
        try:
            register_scope(bad)
        except ValueError:
            continue
        raise AssertionError(f"scope {bad!r} should have been rejected")
