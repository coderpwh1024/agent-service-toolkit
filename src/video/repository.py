"""PostgreSQL task reservation and cross-worker coordination."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, LiteralString, cast

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from schema.video import VideoCreate, VideoEdit, VideoListQuery, VideoRecord, VideoTask

_LIST_COLUMNS = SQL("""
    id, user_id, task_id, status, storage_status, aliyun_video_url,
    qiniu_video_url, qiniu_object_key, error_code, create_time, update_time
""")

_COLUMNS = SQL("""
    id, user_id, idempotency_key, request_hash, request_payload, task_id, status,
    storage_status, aliyun_video_url, qiniu_video_url, qiniu_object_key,
    provider_request_id, error_code, next_poll_time, create_by, update_by,
    create_time, update_time, is_delete
""")


class VideoRepository:
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
        ddl = await asyncio.to_thread(
            Path(__file__).with_name("sql").joinpath("create_video_generation_tasks.sql").read_text,
            encoding="utf-8",
        )
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(824567120)")
                await conn.execute(SQL(cast(LiteralString, ddl)))

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def guard(self, key: str) -> AsyncIterator[bool]:
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
        async with await AsyncConnection.connect(self.conninfo, autocommit=True) as conn:
            row = await (
                await conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_id,))
            ).fetchone()
            acquired = bool(row and row[0])
            try:
                yield acquired
            finally:
                if acquired:
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

    async def reserve(
        self, user_id: int, key: str, request_hash: str, body: VideoCreate | VideoEdit
    ) -> tuple[VideoRecord, bool]:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    SQL("""
                        INSERT INTO video_generation_tasks (
                            user_id, idempotency_key, request_hash, request_payload,
                            create_by, update_by, next_poll_time
                        ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP + INTERVAL '5 minutes')
                        ON CONFLICT (user_id, idempotency_key) WHERE is_delete = 0 DO NOTHING
                        RETURNING {}
                    """).format(_COLUMNS),
                    (
                        user_id,
                        key,
                        request_hash,
                        Jsonb(body.model_dump(mode="json")),
                        user_id,
                        user_id,
                    ),
                )
            ).fetchone()
            if row:
                return VideoRecord.model_validate(row), True
            row = await (
                await conn.execute(
                    SQL("""SELECT {} FROM video_generation_tasks
                        WHERE user_id = %s AND idempotency_key = %s AND is_delete = 0
                    """).format(_COLUMNS),
                    (user_id, key),
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("视频幂等记录不可用")
        return VideoRecord.model_validate(row), False

    async def get(self, record_id: int, user_id: int) -> VideoRecord | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    SQL("""SELECT {} FROM video_generation_tasks
                        WHERE id = %s AND user_id = %s AND is_delete = 0
                    """).format(_COLUMNS),
                    (record_id, user_id),
                )
            ).fetchone()
        return VideoRecord.model_validate(row) if row else None

    async def latest_id(self, user_id: int) -> int | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """SELECT id FROM video_generation_tasks
                    WHERE user_id = %s AND is_delete = 0 ORDER BY id DESC LIMIT 1""",
                    (user_id,),
                )
            ).fetchone()
        return row["id"] if row else None

    async def list_tasks(self, user_id: int, query: VideoListQuery) -> tuple[list[VideoTask], int]:
        conditions = [SQL("user_id = %s"), SQL("is_delete = 0")]
        params: list[object] = [user_id]
        if query.status is not None:
            conditions.append(SQL("status = %s"))
            params.append(query.status)
        if query.storage_status is not None:
            conditions.append(SQL("storage_status = %s"))
            params.append(query.storage_status)
        where = SQL(" AND ").join(conditions)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                count = await (
                    await conn.execute(
                        SQL("SELECT COUNT(*) AS total FROM video_generation_tasks WHERE {}").format(
                            where
                        ),
                        params,
                    )
                ).fetchone()
                rows = await (
                    await conn.execute(
                        SQL("""
                            SELECT {} FROM video_generation_tasks WHERE {}
                            ORDER BY create_time DESC, id DESC LIMIT %s OFFSET %s
                        """).format(_LIST_COLUMNS, where),
                        [*params, query.page_size, (query.page - 1) * query.page_size],
                    )
                ).fetchall()
        if count is None:
            raise RuntimeError("视频列表总数查询失败")
        return [VideoTask.model_validate(row) for row in rows], count["total"]

    async def save(self, record: VideoRecord, actor: int) -> VideoRecord:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    SQL("""
                        UPDATE video_generation_tasks SET
                            task_id = %s, status = %s, storage_status = %s,
                            aliyun_video_url = %s, qiniu_video_url = %s, qiniu_object_key = %s,
                            provider_request_id = %s, error_code = %s,
                            next_poll_time = CURRENT_TIMESTAMP + INTERVAL '5 minutes',
                            update_by = %s, update_time = CURRENT_TIMESTAMP
                        WHERE id = %s AND user_id = %s AND is_delete = 0 RETURNING {}
                    """).format(_COLUMNS),
                    (
                        record.task_id,
                        record.status,
                        record.storage_status,
                        record.aliyun_video_url,
                        record.qiniu_video_url,
                        record.qiniu_object_key,
                        record.provider_request_id,
                        record.error_code,
                        actor,
                        record.id,
                        record.user_id,
                    ),
                )
            ).fetchone()
        if row is None:
            raise LookupError("视频任务不存在")
        return VideoRecord.model_validate(row)

    async def due(self) -> list[VideoRecord]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    SQL("""
                        SELECT {} FROM video_generation_tasks
                        WHERE is_delete = 0 AND next_poll_time <= CURRENT_TIMESTAMP AND (
                            status IN ('SUBMITTING', 'PENDING', 'RUNNING') OR
                            (status = 'SUCCEEDED' AND storage_status <> 'STORED')
                        ) ORDER BY next_poll_time, id LIMIT 10
                    """).format(_COLUMNS)
                )
            ).fetchall()
        return [VideoRecord.model_validate(row) for row in rows]
