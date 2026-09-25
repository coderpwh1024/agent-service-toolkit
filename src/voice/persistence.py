"""Durable voice records and cross-worker thread coordination in PostgreSQL."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from schema.voice import VoiceSession, VoiceTurn


class VoiceRepository:
    def __init__(self, conninfo: str):
        self.conninfo = conninfo
        self.pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
            conninfo,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)
        async with self.pool.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_thread_owners (
                    thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_runs (
                    run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    session_id UUID PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL, data JSONB NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_turns (
                    turn_id UUID PRIMARY KEY, session_id UUID NOT NULL REFERENCES voice_sessions,
                    thread_id TEXT NOT NULL, input_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL, data JSONB NOT NULL,
                    UNIQUE(session_id, input_id)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS voice_turns_thread_created
                ON voice_turns(thread_id, created_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS voice_sessions_user_expiry
                ON voice_sessions(user_id, expires_at)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS app_runs_user_created
                ON app_runs(user_id, created_at DESC)
            """)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def guard(self, key: str) -> AsyncIterator[None]:
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
        async with await AsyncConnection.connect(self.conninfo, autocommit=True) as conn:
            row = await (
                await conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            ).fetchone()
            if not row or not row[0]:
                raise HTTPException(409, "The session or thread is already in use")
            try:
                yield
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

    async def owner(self, thread_id: str) -> tuple[str, str] | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT user_id, agent_id FROM app_thread_owners WHERE thread_id=%s",
                    (thread_id,),
                )
            ).fetchone()
        return (row["user_id"], row["agent_id"]) if row else None

    async def claim(self, thread_id: str, user_id: str, agent_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO app_thread_owners VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (thread_id, user_id, agent_id),
            )
        if await self.owner(thread_id) != (user_id, agent_id):
            raise HTTPException(403, "Thread belongs to another user or agent")

    async def register_run(self, run_id: str, user_id: str, thread_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO app_runs(run_id, user_id, thread_id) VALUES (%s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id, user_id, thread_id),
            )

    async def owns_run(self, run_id: str, user_id: str) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT 1 FROM app_runs WHERE run_id=%s AND user_id=%s",
                    (run_id, user_id),
                )
            ).fetchone()
        return row is not None

    async def save_session(self, session: VoiceSession) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO voice_sessions VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET data=EXCLUDED.data
            """,
                (
                    session.session_id,
                    session.user_id,
                    session.thread_id,
                    session.expires_at,
                    Jsonb(session.model_dump(mode="json")),
                ),
            )

    async def get_session(self, session_id: str) -> VoiceSession | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT data FROM voice_sessions WHERE session_id=%s", (session_id,)
                )
            ).fetchone()
        return VoiceSession.model_validate(row["data"]) if row else None

    async def active_sessions(self, user_id: str) -> int:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                SELECT count(*) AS count FROM voice_sessions
                WHERE user_id=%s AND expires_at>now()
                  AND data->>'status' IN ('created', 'connected')
            """,
                    (user_id,),
                )
            ).fetchone()
        return row["count"] if row else 0

    async def save_turn(self, turn: VoiceTurn) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO voice_turns VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (turn_id) DO UPDATE SET data=EXCLUDED.data
            """,
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.thread_id,
                    turn.input_id,
                    turn.created_at,
                    Jsonb(turn.model_dump(mode="json")),
                ),
            )

    async def has_input(self, session_id: str, input_id: str) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT 1 FROM voice_turns WHERE session_id=%s AND input_id=%s",
                    (session_id, input_id),
                )
            ).fetchone()
        return row is not None

    async def turns(self, thread_id: str, limit: int = 50) -> list[VoiceTurn]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                SELECT data FROM voice_turns WHERE thread_id=%s
                ORDER BY created_at DESC LIMIT %s
            """,
                    (thread_id, limit),
                )
            ).fetchall()
        return [VoiceTurn.model_validate(row["data"]) for row in reversed(rows)]
