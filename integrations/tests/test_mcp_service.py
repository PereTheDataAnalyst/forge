"""Rate limiter, audit recorder, client stores and the token service."""

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forge_integrations.config import McpSettings
from forge_integrations.errors import ErrorCode, ForgeError, ValidationFailed
from forge_integrations.mcp.audit import AuditRecorder, InMemoryAuditSink
from forge_integrations.mcp.ratelimit import InMemoryRateLimiter
from forge_integrations.mcp.scopes import ALL_SCOPES, DEFAULT_SCOPES
from forge_integrations.mcp.service import (
    McpTokenService,
    REASON_BAD_SECRET,
    REASON_EXPIRED,
    REASON_MALFORMED,
    REASON_REVOKED,
    REASON_UNKNOWN_PREFIX,
    REASON_WRONG_KIND,
)
from forge_integrations.mcp.stores import InMemoryMcpClientStore, SqliteMcpClientStore
from forge_integrations.ports import McpClientRecord
from forge_integrations.security.tokens import TokenHasher, generate_token, parse_token

from .helpers import RecordingObserver

PEPPER = "p" * 48
T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeTime:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- rate limiter
class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_allows_up_to_the_limit_then_blocks_with_retry_after(self):
        clock = FakeTime()
        limiter = InMemoryRateLimiter(clock=clock)
        decisions = []
        for _ in range(3):
            decisions.append(await limiter.check("k", 3, 60))
            clock.t += 1
        blocked = await limiter.check("k", 3, 60)
        self.assertEqual([d.allowed for d in decisions], [True, True, True])
        self.assertEqual([d.remaining for d in decisions], [2, 1, 0])
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.remaining, 0)
        # The oldest hit was 3 seconds ago, so it frees up in 57 seconds.
        self.assertEqual(blocked.retry_after_seconds, 57)

    async def test_the_window_slides(self):
        clock = FakeTime()
        limiter = InMemoryRateLimiter(clock=clock)
        for _ in range(2):
            self.assertTrue((await limiter.check("k", 2, 10)).allowed)
        self.assertFalse((await limiter.check("k", 2, 10)).allowed)
        clock.t += 10.01
        self.assertTrue((await limiter.check("k", 2, 10)).allowed)

    async def test_keys_are_independent(self):
        limiter = InMemoryRateLimiter(clock=FakeTime())
        self.assertTrue((await limiter.check("a", 1, 60)).allowed)
        self.assertFalse((await limiter.check("a", 1, 60)).allowed)
        self.assertTrue((await limiter.check("b", 1, 60)).allowed)

    async def test_blocked_requests_do_not_extend_the_block(self):
        clock = FakeTime()
        limiter = InMemoryRateLimiter(clock=clock)
        await limiter.check("k", 1, 10)
        for _ in range(20):
            clock.t += 0.4
            await limiter.check("k", 1, 10)  # hammering while blocked
        clock.t += 2.0  # 10.0 seconds after the single allowed hit
        self.assertTrue((await limiter.check("k", 1, 10)).allowed)

    async def test_memory_is_bounded_by_key_count(self):
        limiter = InMemoryRateLimiter(max_keys=5, clock=FakeTime())
        for index in range(100):
            await limiter.check(f"key-{index}", 1, 60)
        self.assertLessEqual(len(limiter), 5)

    async def test_expired_keys_are_dropped_before_live_ones(self):
        clock = FakeTime()
        limiter = InMemoryRateLimiter(max_keys=3, clock=clock)
        await limiter.check("old-1", 1, 5)
        await limiter.check("old-2", 1, 5)
        clock.t += 6
        await limiter.check("live", 1, 600)
        await limiter.check("new-1", 1, 60)  # room is made by dropping the expired keys
        self.assertFalse((await limiter.check("live", 1, 600)).allowed)

    async def test_invalid_arguments_are_rejected(self):
        limiter = InMemoryRateLimiter()
        with self.assertRaises(ValueError):
            await limiter.check("k", 0, 60)
        with self.assertRaises(ValueError):
            await limiter.check("k", 1, 0)
        with self.assertRaises(ValueError):
            InMemoryRateLimiter(max_keys=0)


# ----------------------------------------------------------------------------- audit
class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_are_redacted_and_stamped(self):
        sink = InMemoryAuditSink()
        observer = RecordingObserver()
        recorder = AuditRecorder(sink, observer, clock=lambda: T0)
        secret = generate_token().token
        await recorder.record("mcp.test", authorization=f"Bearer {secret}", note=f"saw {secret}", token_hash="abc", tool="generate")
        event = sink.events[0]
        blob = json.dumps(event)
        self.assertNotIn(secret, blob)
        self.assertNotIn("abc", blob)
        self.assertEqual(event["event"], "mcp.test")
        self.assertEqual(event["at"], T0.isoformat())
        self.assertEqual(event["tool"], "generate")
        self.assertEqual(observer.events[0], event)

    async def test_a_failing_sink_never_fails_the_caller(self):
        class Broken:
            async def record(self, event):
                raise RuntimeError("database is down")

        observer = RecordingObserver()
        await AuditRecorder(Broken(), observer).record("mcp.test", tool="generate")
        self.assertEqual([e["event"] for e in observer.events], ["mcp.test", "mcp.audit_failure"])
        self.assertEqual(observer.events[1]["sink_error"], "RuntimeError")

    async def test_a_failing_observer_never_fails_the_caller(self):
        class Exploding:
            def emit(self, event):
                raise RuntimeError("logger down")

        sink = InMemoryAuditSink()
        await AuditRecorder(sink, Exploding()).record("mcp.test")
        self.assertEqual(len(sink.events), 1)

    async def test_the_in_memory_sink_is_bounded(self):
        sink = InMemoryAuditSink(max_events=3)
        for index in range(10):
            await sink.record({"n": index})
        self.assertEqual([e["n"] for e in sink.events], [7, 8, 9])


# ----------------------------------------------------------------------------- stores
def make_record(index=0, project="proj-1", prefix=None, **overrides):
    base = dict(
        client_id=f"client-{index}",
        project_id=project,
        name=f"client {index}",
        token_prefix=prefix or f"{index:08x}",
        token_hash="f" * 64,
        scopes=frozenset({"models:read"}),
        created_at=T0 + timedelta(minutes=index),
        expires_at=T0 + timedelta(days=30),
    )
    base.update(overrides)
    return McpClientRecord(**base)


class StoreContract:
    """Behaviour every ``McpClientStore`` must have. Mixed into one test case per implementation."""

    def make_store(self):  # pragma: no cover - provided by subclasses
        raise NotImplementedError

    def setUp(self):
        self.store = self.make_store()

    async def test_add_find_get(self):
        record = make_record(1)
        await self.store.add(record)
        self.assertEqual(await self.store.find_by_prefix(record.token_prefix), record)
        self.assertEqual(await self.store.get("proj-1", "client-1"), record)
        self.assertIsNone(await self.store.find_by_prefix("deadbeef"))

    async def test_get_is_scoped_to_the_project(self):
        await self.store.add(make_record(1))
        self.assertIsNone(await self.store.get("other-project", "client-1"))

    async def test_duplicate_prefix_or_id_conflicts(self):
        await self.store.add(make_record(1, prefix="aaaaaaaa"))
        with self.assertRaises(ForgeError) as same_prefix:
            await self.store.add(make_record(2, prefix="aaaaaaaa"))
        self.assertEqual((same_prefix.exception.code, same_prefix.exception.http_status), (ErrorCode.CONFLICT, 409))
        with self.assertRaises(ForgeError) as same_id:
            await self.store.add(make_record(1, prefix="bbbbbbbb"))
        self.assertEqual(same_id.exception.code, ErrorCode.CONFLICT)

    async def test_list_is_per_project_and_newest_first(self):
        for index in range(3):
            await self.store.add(make_record(index))
        await self.store.add(make_record(9, project="proj-2"))
        listed = await self.store.list_for_project("proj-1")
        self.assertEqual([r.client_id for r in listed], ["client-2", "client-1", "client-0"])
        self.assertEqual([r.client_id for r in await self.store.list_for_project("proj-2")], ["client-9"])
        self.assertEqual(await self.store.list_for_project("nobody"), [])

    async def test_revoke_semantics(self):
        await self.store.add(make_record(1))
        self.assertFalse(await self.store.revoke("other-project", "client-1", T0))
        self.assertIsNone((await self.store.get("proj-1", "client-1")).revoked_at)
        self.assertTrue(await self.store.revoke("proj-1", "client-1", T0))
        self.assertEqual((await self.store.get("proj-1", "client-1")).revoked_at, T0)
        self.assertFalse(await self.store.revoke("proj-1", "client-1", T0 + timedelta(hours=1)))
        self.assertEqual((await self.store.get("proj-1", "client-1")).revoked_at, T0)  # first revocation wins
        self.assertFalse(await self.store.revoke("proj-1", "missing", T0))

    async def test_touch_last_used_only_moves_forward(self):
        await self.store.add(make_record(1))
        await self.store.touch_last_used("client-1", T0 + timedelta(hours=2))
        await self.store.touch_last_used("client-1", T0 + timedelta(hours=1))
        self.assertEqual((await self.store.get("proj-1", "client-1")).last_used_at, T0 + timedelta(hours=2))
        await self.store.touch_last_used("missing", T0)  # no error

    async def test_hostile_strings_are_data_not_sql(self):
        evil = "x'; DROP TABLE mcp_clients; --"
        await self.store.add(make_record(1))
        self.assertIsNone(await self.store.find_by_prefix(evil))
        self.assertIsNone(await self.store.get(evil, "client-1"))
        self.assertEqual(await self.store.list_for_project(evil), [])
        self.assertFalse(await self.store.revoke(evil, evil, T0))
        self.assertIsNotNone(await self.store.find_by_prefix("00000001"))


class InMemoryStoreTests(StoreContract, unittest.IsolatedAsyncioTestCase):
    def make_store(self):
        return InMemoryMcpClientStore()


class SqliteStoreTests(StoreContract, unittest.IsolatedAsyncioTestCase):
    def make_store(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        store = SqliteMcpClientStore(Path(self._dir.name) / "data" / "mcp.sqlite3")
        self.addCleanup(store.close)
        return store

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_the_database_and_its_new_directory_are_private(self):
        path = Path(self._dir.name) / "data" / "mcp.sqlite3"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_an_existing_directory_is_not_changed(self):
        with tempfile.TemporaryDirectory() as shared:
            os.chmod(shared, 0o755)
            store = SqliteMcpClientStore(Path(shared) / "mcp.sqlite3")
            try:
                self.assertEqual(Path(shared).stat().st_mode & 0o777, 0o755)
                self.assertEqual((Path(shared) / "mcp.sqlite3").stat().st_mode & 0o777, 0o600)
            finally:
                store.close()

    async def test_records_survive_a_restart(self):
        record = make_record(1, scopes=frozenset({"generate", "transcribe"}), last_used_at=T0)
        await self.store.add(record)
        path = Path(self._dir.name) / "data" / "mcp.sqlite3"
        self.store.close()
        reopened = SqliteMcpClientStore(path)
        try:
            self.assertEqual(await reopened.get("proj-1", "client-1"), record)
        finally:
            reopened.close()

    async def test_unknown_scopes_in_the_database_grant_nothing(self):
        await self.store.add(make_record(1, scopes=frozenset({"models:read"})))
        path = Path(self._dir.name) / "data" / "mcp.sqlite3"
        raw = sqlite3.connect(path)
        raw.execute("UPDATE mcp_clients SET scopes = ? WHERE client_id = 'client-1'", (json.dumps(["models:read", "admin", "*"]),))
        raw.commit()
        raw.close()
        loaded = await self.store.get("proj-1", "client-1")
        self.assertEqual(loaded.scopes, frozenset({"models:read"}))

    def test_a_newer_schema_is_refused(self):
        path = Path(self._dir.name) / "newer.sqlite3"
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA user_version = 99")
        raw.commit()
        raw.close()
        with self.assertRaises(RuntimeError):
            SqliteMcpClientStore(path)

    async def test_naive_datetimes_are_treated_as_utc(self):
        naive = datetime(2026, 10, 1, 12, 0, 0)
        await self.store.add(make_record(1, created_at=naive, expires_at=None))
        loaded = await self.store.get("proj-1", "client-1")
        self.assertEqual(loaded.created_at, naive.replace(tzinfo=timezone.utc))
        self.assertIsNone(loaded.expires_at)


# ----------------------------------------------------------------------------- service
class SpyHasher(TokenHasher):
    def __init__(self, pepper):
        super().__init__(pepper)
        self.verify_calls = 0

    def verify(self, token, expected_hex):
        self.verify_calls += 1
        return super().verify(token, expected_hex)


def make_service(store=None, settings=None, *, clock=None, mono=None, observer=None, sink=None, hasher=None, interval=60.0):
    store = store or InMemoryMcpClientStore()
    sink = sink if sink is not None else InMemoryAuditSink()
    service = McpTokenService(
        store,
        hasher or SpyHasher(PEPPER),
        settings or McpSettings(),
        audit=AuditRecorder(sink, observer, clock=clock or Clock()),
        clock=clock or Clock(),
        monotonic=mono or Mono(),
        last_used_interval_seconds=interval,
    )
    return service, store, sink


class IssueTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_token_is_well_formed_scoped_and_expiring(self):
        service, store, _ = make_service()
        issued = await service.issue(project_id="proj-1", name="  Cursor on laptop ", scopes=["generate", "models:read"], ttl_days=7)
        parsed = parse_token(issued.token)
        self.assertEqual((parsed.kind, parsed.prefix), ("fmcp", issued.record.token_prefix))
        self.assertEqual(issued.record.name, "Cursor on laptop")
        self.assertEqual(issued.record.scopes, frozenset({"generate", "models:read"}))
        self.assertEqual(issued.record.expires_at, T0 + timedelta(days=7))
        stored = await store.find_by_prefix(issued.record.token_prefix)
        self.assertNotIn(issued.token, repr(stored))
        self.assertNotIn(issued.token.split("_")[2], repr(stored))
        self.assertEqual(stored.token_hash, TokenHasher(PEPPER).hash(issued.token))
        self.assertNotIn(issued.token, repr(issued))

    async def test_defaults_are_read_only_and_short_lived(self):
        service, _, _ = make_service()
        issued = await service.issue(project_id="proj-1", name="reader")
        self.assertEqual(issued.record.scopes, DEFAULT_SCOPES)
        self.assertEqual(issued.record.expires_at, T0 + timedelta(days=30))
        self.assertFalse({"generate", "transcribe", "evaluate"} & issued.record.scopes)

    async def test_every_problem_is_reported_at_once(self):
        service, store, _ = make_service()
        with self.assertRaises(ValidationFailed) as caught:
            await service.issue(project_id="bad project!", name=" ", scopes=["generate", "root", 7], ttl_days=0)
        paths = {i.path for i in caught.exception.issues}
        self.assertEqual(paths, {"project_id", "name", "scopes[1]", "scopes[2]", "ttl_days"})
        self.assertEqual(await store.list_for_project("bad project!"), [])

    async def test_ttl_cannot_exceed_the_configured_maximum_and_there_is_no_forever_token(self):
        service, _, _ = make_service(settings=McpSettings(token_max_ttl_days=10, token_default_ttl_days=5))
        await service.issue(project_id="p", name="n", ttl_days=10)
        for bad in (11, 0, -1, True, 3.5, "7"):
            with self.subTest(ttl=bad):
                with self.assertRaises(ValidationFailed):
                    await service.issue(project_id="p", name="n", ttl_days=bad)

    async def test_names_with_control_characters_or_excess_length_are_rejected(self):
        service, _, _ = make_service()
        for bad in ("line\nbreak", "tab\there", "x" * 81, "\x00"):
            with self.subTest(name=bad):
                with self.assertRaises(ValidationFailed):
                    await service.issue(project_id="p", name=bad)

    async def test_scopes_that_do_not_exist_are_rejected_not_ignored(self):
        service, _, _ = make_service()
        for bad in (["*"], ["admin"], ["delete_project"], ["Generate"], [], ["generate", "generate ", "x"]):
            with self.subTest(scopes=bad):
                with self.assertRaises(ValidationFailed):
                    await service.issue(project_id="p", name="n", scopes=bad)
        issued = await service.issue(project_id="p", name="n", scopes=sorted(ALL_SCOPES))
        self.assertEqual(issued.record.scopes, ALL_SCOPES)

    async def test_prefix_collisions_are_retried(self):
        class CollidingStore(InMemoryMcpClientStore):
            def __init__(self):
                super().__init__()
                self.failures = 2

            async def add(self, record):
                if self.failures:
                    self.failures -= 1
                    raise ForgeError(ErrorCode.CONFLICT, "dup", http_status=409)
                await super().add(record)

        store = CollidingStore()
        service, _, _ = make_service(store=store)
        issued = await service.issue(project_id="p", name="n")
        self.assertEqual(store.failures, 0)
        self.assertIsNotNone(await store.find_by_prefix(issued.record.token_prefix))

    async def test_giving_up_after_repeated_collisions_is_a_retryable_error(self):
        class AlwaysConflict(InMemoryMcpClientStore):
            async def add(self, record):
                raise ForgeError(ErrorCode.CONFLICT, "dup", http_status=409)

        service, _, _ = make_service(store=AlwaysConflict())
        with self.assertRaises(ForgeError) as caught:
            await service.issue(project_id="p", name="n")
        self.assertEqual((caught.exception.code, caught.exception.retryable), (ErrorCode.INTERNAL, True))

    async def test_other_store_errors_are_not_swallowed(self):
        class Broken(InMemoryMcpClientStore):
            async def add(self, record):
                raise ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "db down", http_status=503)

        service, _, _ = make_service(store=Broken())
        with self.assertRaises(ForgeError) as caught:
            await service.issue(project_id="p", name="n")
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)

    async def test_issuing_is_audited_without_the_token(self):
        service, _, sink = make_service()
        issued = await service.issue(project_id="p", name="n", scopes=["generate"], actor="user-7")
        event = sink.events[0]
        self.assertEqual(event["event"], "mcp.token_issued")
        self.assertEqual((event["project_id"], event["actor"], event["scopes"]), ("p", "user-7", ["generate"]))
        self.assertNotIn(issued.token, json.dumps(event))
        self.assertNotIn(issued.record.token_hash, json.dumps(event))


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.mono = Mono()
        self.service, self.store, self.sink = make_service(clock=self.clock, mono=self.mono)
        self.issued = await self.service.issue(project_id="proj-1", name="agent", scopes=["generate", "models:read"], ttl_days=5)

    async def test_a_valid_token_yields_a_principal(self):
        result = await self.service.verify(self.issued.token)
        self.assertIsNone(result.reason)
        principal = result.principal
        self.assertEqual(
            (principal.project_id, principal.client_id, principal.client_name, principal.token_prefix),
            ("proj-1", self.issued.record.client_id, "agent", self.issued.record.token_prefix),
        )
        self.assertEqual(principal.scopes, frozenset({"generate", "models:read"}))
        self.assertTrue(principal.has_scope("generate"))
        self.assertFalse(principal.has_scope("evaluate"))
        self.assertEqual(principal.expires_at, T0 + timedelta(days=5))

    async def test_failures_carry_a_private_reason(self):
        good = self.issued.token
        prefix = self.issued.record.token_prefix
        wrong_secret = f"fmcp_{prefix}_" + "A" * 43
        unknown = "fmcp_00000000_" + "A" * 43
        wrong_kind = f"fk_{prefix}_" + "A" * 43
        cases = {
            "": REASON_MALFORMED,
            "garbage": REASON_MALFORMED,
            good[:-1]: REASON_MALFORMED,
            good + "x": REASON_MALFORMED,
            "x" * 5000: REASON_MALFORMED,
            wrong_secret: REASON_BAD_SECRET,
            unknown: REASON_UNKNOWN_PREFIX,
            wrong_kind: REASON_WRONG_KIND,
        }
        for token, reason in cases.items():
            with self.subTest(reason=reason, token=token[:20]):
                result = await self.service.verify(token)
                self.assertIsNone(result.principal)
                self.assertEqual(result.reason, reason)

    async def test_unknown_prefixes_cost_the_same_work_as_wrong_secrets(self):
        hasher = self.service._hasher
        hasher.verify_calls = 0
        await self.service.verify("fmcp_00000000_" + "A" * 43)
        unknown_cost = hasher.verify_calls
        hasher.verify_calls = 0
        await self.service.verify(f"fmcp_{self.issued.record.token_prefix}_" + "A" * 43)
        known_cost = hasher.verify_calls
        self.assertEqual((unknown_cost, known_cost), (1, 1))

    async def test_revoked_tokens_stop_working_immediately(self):
        self.assertTrue(await self.service.revoke("proj-1", self.issued.record.client_id, actor="user-1"))
        result = await self.service.verify(self.issued.token)
        self.assertEqual((result.principal, result.reason), (None, REASON_REVOKED))

    async def test_expired_tokens_stop_working(self):
        self.clock.advance(days=5, seconds=-1)
        self.assertIsNotNone((await self.service.verify(self.issued.token)).principal)
        self.clock.advance(seconds=1)
        result = await self.service.verify(self.issued.token)
        self.assertEqual((result.principal, result.reason), (None, REASON_EXPIRED))

    async def test_a_token_without_an_expiry_in_the_store_is_still_checked_for_revocation(self):
        record = replace(self.issued.record, expires_at=None)
        self.store._by_id[record.client_id] = record
        self.assertIsNotNone((await self.service.verify(self.issued.token)).principal)
        await self.service.revoke("proj-1", record.client_id)
        self.assertEqual((await self.service.verify(self.issued.token)).reason, REASON_REVOKED)

    async def test_last_used_is_recorded_at_most_once_per_interval(self):
        touches = []
        original = self.store.touch_last_used

        async def spy(client_id, when):
            touches.append(when)
            await original(client_id, when)

        self.store.touch_last_used = spy
        for _ in range(5):
            await self.service.verify(self.issued.token)
        self.assertEqual(len(touches), 1)
        self.mono.t += 61
        self.clock.advance(seconds=61)
        await self.service.verify(self.issued.token)
        self.assertEqual(len(touches), 2)
        stored = await self.store.get("proj-1", self.issued.record.client_id)
        self.assertEqual(stored.last_used_at, self.clock.now)

    async def test_a_store_failure_while_recording_use_does_not_reject_a_valid_token(self):
        async def broken(client_id, when):
            raise RuntimeError("write failed")

        self.store.touch_last_used = broken
        self.assertIsNotNone((await self.service.verify(self.issued.token)).principal)
        self.assertIsNotNone((await self.service.verify(self.issued.token)).principal)

    async def test_tokens_cannot_be_used_across_peppers(self):
        other, _, _ = make_service(store=self.store, hasher=SpyHasher("q" * 48))
        result = await other.verify(self.issued.token)
        self.assertEqual(result.reason, REASON_BAD_SECRET)


class ListAndRevokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_shows_public_fields_only(self):
        service, _, _ = make_service()
        issued = await service.issue(project_id="p1", name="one")
        await service.issue(project_id="p2", name="other project")
        listed = await service.list_clients("p1")
        self.assertEqual(len(listed), 1)
        blob = json.dumps(listed)
        self.assertNotIn(issued.record.token_hash, blob)
        self.assertNotIn(issued.token, blob)
        self.assertNotIn("token_hash", listed[0])
        self.assertEqual(listed[0]["token_prefix"], issued.record.token_prefix)

    async def test_revoke_is_idempotent_and_never_crosses_projects(self):
        service, _, sink = make_service()
        issued = await service.issue(project_id="p1", name="one")
        client_id = issued.record.client_id
        with self.assertRaises(ForgeError) as other_project:
            await service.revoke("p2", client_id)
        self.assertEqual((other_project.exception.code, other_project.exception.http_status), (ErrorCode.NOT_FOUND, 404))
        self.assertIsNotNone((await service.verify(issued.token)).principal)
        self.assertTrue(await service.revoke("p1", client_id, actor="user-1"))
        self.assertFalse(await service.revoke("p1", client_id, actor="user-1"))
        revoked_events = [e for e in sink.events if e["event"] == "mcp.token_revoked"]
        self.assertEqual(len(revoked_events), 1)
        self.assertEqual((revoked_events[0]["project_id"], revoked_events[0]["actor"]), ("p1", "user-1"))

    async def test_unknown_clients_and_bad_ids_are_not_found(self):
        service, _, _ = make_service()
        for client_id in ("nope", "", "x" * 65):
            with self.subTest(client_id=client_id[:10]):
                with self.assertRaises(ForgeError) as caught:
                    await service.revoke("p1", client_id)
                self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)
        with self.assertRaises(ValidationFailed):
            await service.revoke("bad id!", "x")
        with self.assertRaises(ValidationFailed):
            await service.list_clients("bad id!")


class SettingsTests(unittest.TestCase):
    def test_body_limit_must_fit_the_audio_limit(self):
        from forge_integrations.config import ConfigError, McpSettings, load_mcp_settings

        with self.assertRaises(ConfigError):
            McpSettings(max_body_bytes=1024 * 1024, max_audio_bytes=5 * 1024 * 1024)
        with self.assertRaises(ConfigError):
            load_mcp_settings({"MCP_MAX_BODY_BYTES": "100000", "MCP_MAX_AUDIO_BYTES": "5242880"})
        ok = load_mcp_settings({"MCP_MAX_BODY_BYTES": "9000000", "MCP_MAX_AUDIO_BYTES": "5242880"})
        self.assertEqual(ok.max_body_bytes, 9_000_000)

    def test_new_limits_have_safe_defaults_and_env_overrides(self):
        from forge_integrations.config import load_mcp_settings

        defaults = load_mcp_settings({})
        self.assertEqual(
            (defaults.rate_limit_project_per_minute, defaults.tool_timeout_seconds, defaults.evaluate_call_timeout_seconds),
            (300, 120.0, 45.0),
        )
        self.assertEqual(defaults.evaluate_tool_timeout_seconds, 90 + 45.0 + 5.0)
        tuned = load_mcp_settings({"MCP_RATE_LIMIT_PROJECT_PER_MINUTE": "50", "MCP_TOOL_TIMEOUT_SECONDS": "30", "MCP_EVALUATE_CALL_TIMEOUT_SECONDS": "10"})
        self.assertEqual((tuned.rate_limit_project_per_minute, tuned.tool_timeout_seconds, tuned.evaluate_call_timeout_seconds), (50, 30.0, 10.0))


if __name__ == "__main__":
    unittest.main()
