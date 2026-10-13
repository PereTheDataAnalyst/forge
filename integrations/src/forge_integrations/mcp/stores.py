"""MCP client stores.

``InMemoryMcpClientStore`` is for tests and short demos. ``SqliteMcpClientStore`` is a durable
store for running the MCP server on its own. When the core platform provides its database, it
implements the ``McpClientStore`` port on that database instead (the SQL contract is in
``docs/integration-contract.md``) and these two classes are not used.

A store holds only the HMAC of each token, never the token.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..errors import ErrorCode, ForgeError
from ..ports import McpClientRecord
from .scopes import ALL_SCOPES

T = TypeVar("T")

SCHEMA_VERSION = 1


def _conflict() -> ForgeError:
    return ForgeError(ErrorCode.CONFLICT, "A client with this identifier or token prefix already exists.", http_status=409)


class InMemoryMcpClientStore:
    def __init__(self) -> None:
        self._by_id: dict[str, McpClientRecord] = {}
        self._by_prefix: dict[str, str] = {}

    async def find_by_prefix(self, prefix: str) -> McpClientRecord | None:
        client_id = self._by_prefix.get(prefix)
        return self._by_id.get(client_id) if client_id else None

    async def touch_last_used(self, client_id: str, when: datetime) -> None:
        record = self._by_id.get(client_id)
        if record is not None and (record.last_used_at is None or when > record.last_used_at):
            self._by_id[client_id] = replace(record, last_used_at=when)

    async def add(self, record: McpClientRecord) -> None:
        if record.client_id in self._by_id or record.token_prefix in self._by_prefix:
            raise _conflict()
        self._by_id[record.client_id] = record
        self._by_prefix[record.token_prefix] = record.client_id

    async def list_for_project(self, project_id: str) -> list[McpClientRecord]:
        found = [r for r in self._by_id.values() if r.project_id == project_id]
        return sorted(found, key=lambda r: (r.created_at, r.client_id), reverse=True)

    async def get(self, project_id: str, client_id: str) -> McpClientRecord | None:
        record = self._by_id.get(client_id)
        return record if record is not None and record.project_id == project_id else None

    async def revoke(self, project_id: str, client_id: str, when: datetime) -> bool:
        record = self._by_id.get(client_id)
        if record is None or record.project_id != project_id or record.revoked_at is not None:
            return False
        self._by_id[client_id] = replace(record, revoked_at=when)
        return True


# --------------------------------------------------------------------------------------- sqlite
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_clients (
    client_id    TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_prefix TEXT NOT NULL UNIQUE,
    token_hash   TEXT NOT NULL,
    scopes       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    revoked_at   TEXT,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_clients_project ON mcp_clients (project_id, created_at);
"""

_COLUMNS = "client_id, project_id, name, token_prefix, token_hash, scopes, created_at, expires_at, revoked_at, last_used_at"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row_to_record(row: tuple[Any, ...]) -> McpClientRecord:
    scopes_raw = json.loads(row[5])
    # A scope this version does not know grants nothing.
    scopes = frozenset(s for s in scopes_raw if isinstance(s, str) and s in ALL_SCOPES)
    created = _parse(row[6])
    assert created is not None
    return McpClientRecord(
        client_id=row[0],
        project_id=row[1],
        name=row[2],
        token_prefix=row[3],
        token_hash=row[4],
        scopes=scopes,
        created_at=created,
        expires_at=_parse(row[7]),
        revoked_at=_parse(row[8]),
        last_used_at=_parse(row[9]),
    )


class SqliteMcpClientStore:
    """Durable single-node store. The database file is created readable by its owner only."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        parent_existed = self._path.parent.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed and os.name == "posix":
            # Only a directory this class created is tightened; an existing one is left alone.
            try:
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass
        # Create the file ourselves so it is never world-readable, even briefly.
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None, timeout=10.0)
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout = 10000")
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
                version = self._conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError("The MCP client database was created by a newer version of this software.")
                self._conn.executescript(_SCHEMA)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            self._conn.close()
            raise

    # -- internals ------------------------------------------------------------------------
    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def locked() -> T:
            with self._lock:
                return fn(self._conn)

        return await asyncio.to_thread(locked)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- port -----------------------------------------------------------------------------
    async def find_by_prefix(self, prefix: str) -> McpClientRecord | None:
        def op(conn: sqlite3.Connection) -> McpClientRecord | None:
            row = conn.execute(f"SELECT {_COLUMNS} FROM mcp_clients WHERE token_prefix = ?", (prefix,)).fetchone()
            return _row_to_record(row) if row else None

        return await self._run(op)

    async def touch_last_used(self, client_id: str, when: datetime) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE mcp_clients SET last_used_at = ? WHERE client_id = ? AND (last_used_at IS NULL OR last_used_at < ?)",
                (_iso(when), client_id, _iso(when)),
            )

        await self._run(op)

    async def add(self, record: McpClientRecord) -> None:
        def op(conn: sqlite3.Connection) -> None:
            try:
                conn.execute(
                    f"INSERT INTO mcp_clients ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.client_id,
                        record.project_id,
                        record.name,
                        record.token_prefix,
                        record.token_hash,
                        json.dumps(sorted(record.scopes)),
                        _iso(record.created_at),
                        _iso(record.expires_at),
                        _iso(record.revoked_at),
                        _iso(record.last_used_at),
                    ),
                )
            except sqlite3.IntegrityError:
                raise _conflict() from None

        await self._run(op)

    async def list_for_project(self, project_id: str) -> list[McpClientRecord]:
        def op(conn: sqlite3.Connection) -> list[McpClientRecord]:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM mcp_clients WHERE project_id = ? ORDER BY created_at DESC, client_id DESC",
                (project_id,),
            ).fetchall()
            return [_row_to_record(r) for r in rows]

        return await self._run(op)

    async def get(self, project_id: str, client_id: str) -> McpClientRecord | None:
        def op(conn: sqlite3.Connection) -> McpClientRecord | None:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM mcp_clients WHERE project_id = ? AND client_id = ?",
                (project_id, client_id),
            ).fetchone()
            return _row_to_record(row) if row else None

        return await self._run(op)

    async def revoke(self, project_id: str, client_id: str, when: datetime) -> bool:
        def op(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE mcp_clients SET revoked_at = ? WHERE project_id = ? AND client_id = ? AND revoked_at IS NULL",
                (_iso(when), project_id, client_id),
            )
            return cursor.rowcount == 1

        return await self._run(op)
