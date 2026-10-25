"""Authentication of MCP requests with platform API keys. Uses an in-memory credential store."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.modules.mcp import catalog
from app.modules.mcp.auth import (
    REASON_MISSING_SCOPE,
    REASON_NO_CLIENT,
    REASON_PROJECT_ARCHIVED,
    McpCredentials,
    PlatformTokenService,
)
from app.security.tokens import generate_api_key, hash_api_key

PEPPER = b"unit-test-pepper-0123456789abcdef"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, McpCredentials] = {}
        self.touches: list[tuple[uuid.UUID, uuid.UUID, datetime]] = []
        self.fail_touch = False
        self.finds = 0
        self.healthy = True

    async def find(self, prefix):
        self.finds += 1
        return self.rows.get(prefix)

    async def touch(self, client_id, key_id, when):
        if self.fail_touch:
            raise RuntimeError("database is busy")
        self.touches.append((client_id, key_id, when))

    async def ping(self):
        return self.healthy


def _credentials(key, **overrides):
    base = dict(
        key_id=uuid.uuid4(),
        key_hash=hash_api_key(key.full_key, PEPPER),
        key_scopes=("mcp.invoke",),
        key_expires_at=NOW + timedelta(days=30),
        key_revoked_at=None,
        project_id=uuid.uuid4(),
        project_archived=False,
        client_id=uuid.uuid4(),
        client_name="Claude Code",
        client_allowed_tools=tuple(catalog.tools_for(["models:read", "project:read"])),
        client_revoked_at=None,
        client_last_seen_at=None,
    )
    base.update(overrides)
    return McpCredentials(**base)


def _service(store, clock=lambda: NOW, monotonic=None):
    clock_ticks = {"now": 0.0}
    return PlatformTokenService(
        store,
        PEPPER,
        clock=clock,
        monotonic=monotonic or (lambda: clock_ticks["now"]),
    ), clock_ticks


def _setup(**overrides):
    key = generate_api_key()
    store = FakeStore()
    credentials = _credentials(key, **overrides)
    store.rows[key.prefix] = credentials
    service, ticks = _service(store)
    return key, store, credentials, service, ticks


def _verify(service, token):
    return asyncio.run(service.verify(token))


def test_a_good_key_becomes_a_principal_whose_scopes_come_from_the_allowed_tools():
    key, _, credentials, service, _ = _setup()
    result = _verify(service, key.full_key)
    assert result.reason is None and result.principal is not None
    principal = result.principal
    assert principal.project_id == str(credentials.project_id)
    assert principal.client_id == str(credentials.client_id)
    assert principal.client_name == "Claude Code"
    assert principal.token_prefix == key.prefix
    assert principal.scopes == frozenset({"models:read", "project:read"})
    assert principal.expires_at == credentials.key_expires_at


def test_malformed_tokens_are_refused_without_a_lookup():
    key, store, _, service, _ = _setup()
    for token in ("", "nope", "Bearer x", key.full_key + "x", key.full_key[:-1], "fmcp_abcdefgh_" + "a" * 43):
        result = _verify(service, token)
        assert result.principal is None and result.reason == "malformed"
    assert store.finds == 0


def test_an_unknown_prefix_is_refused_and_reports_only_the_prefix():
    store = FakeStore()
    service, _ = _service(store)
    stranger = generate_api_key()
    result = _verify(service, stranger.full_key)
    assert result.principal is None and result.reason == "unknown_prefix"
    assert result.token_prefix == stranger.prefix and result.client_id is None


def test_a_wrong_secret_for_a_real_prefix_is_refused():
    key, _, _, service, _ = _setup()
    forged = key.full_key[:-3] + ("AAA" if not key.full_key.endswith("AAA") else "BBB")
    result = _verify(service, forged)
    assert result.principal is None and result.reason == "bad_secret"


def test_a_key_hashed_with_another_pepper_is_refused():
    key = generate_api_key()
    store = FakeStore()
    store.rows[key.prefix] = _credentials(key, key_hash=hash_api_key(key.full_key, b"other-pepper-xxxxxxxxxxxxxxxx"))
    service, _ = _service(store)
    assert _verify(service, key.full_key).reason == "bad_secret"


def test_a_revoked_key_an_expired_key_and_a_revoked_client_are_refused():
    cases = [
        ({"key_revoked_at": NOW - timedelta(minutes=1)}, "revoked"),
        ({"key_expires_at": NOW - timedelta(seconds=1)}, "expired"),
        ({"key_expires_at": NOW}, "expired"),
        ({"client_revoked_at": NOW - timedelta(minutes=1)}, "revoked"),
    ]
    for overrides, reason in cases:
        key, _, _, service, _ = _setup(**overrides)
        result = _verify(service, key.full_key)
        assert result.principal is None and result.reason == reason, overrides


def test_an_archived_project_is_refused():
    key, _, _, service, _ = _setup(project_archived=True)
    assert _verify(service, key.full_key).reason == REASON_PROJECT_ARCHIVED


def test_a_key_without_the_invoke_scope_is_refused_even_with_a_client_row():
    key, _, _, service, _ = _setup(key_scopes=("chat.create", "models.read"))
    assert _verify(service, key.full_key).reason == REASON_MISSING_SCOPE


def test_a_key_with_the_invoke_scope_but_no_client_row_is_refused():
    key, _, _, service, _ = _setup(client_id=None, client_name=None, client_allowed_tools=())
    result = _verify(service, key.full_key)
    assert result.principal is None and result.reason == REASON_NO_CLIENT


def test_a_client_with_no_complete_group_authenticates_but_holds_no_scope():
    key, _, _, service, _ = _setup(client_allowed_tools=("generate",))
    result = _verify(service, key.full_key)
    assert result.principal is not None and result.principal.scopes == frozenset()


def test_failures_never_reveal_which_check_failed_in_the_principal():
    key, _, _, service, _ = _setup(key_revoked_at=NOW)
    result = _verify(service, key.full_key)
    assert result.principal is None
    assert result.project_id and result.client_id and result.token_prefix == key.prefix


def test_last_use_is_written_at_most_once_a_minute():
    key, store, credentials, service, ticks = _setup()
    for _ in range(5):
        assert _verify(service, key.full_key).principal is not None
    assert len(store.touches) == 1
    assert store.touches[0][:2] == (credentials.client_id, credentials.key_id)
    ticks["now"] = 61.0
    _verify(service, key.full_key)
    assert len(store.touches) == 2


def test_a_failing_last_use_write_never_rejects_a_valid_key_and_is_retried():
    key, store, _, service, _ = _setup()
    store.fail_touch = True
    assert _verify(service, key.full_key).principal is not None
    store.fail_touch = False
    assert _verify(service, key.full_key).principal is not None
    assert len(store.touches) == 1


def test_ping_follows_the_store():
    store = FakeStore()
    service, _ = _service(store)
    assert asyncio.run(service.ping()) is True
    store.healthy = False
    assert asyncio.run(service.ping()) is False


def test_the_service_and_its_credentials_do_not_print_secrets():
    key, _, credentials, service, _ = _setup()
    assert key.full_key not in repr(service)
    assert key.full_key not in repr(credentials)
