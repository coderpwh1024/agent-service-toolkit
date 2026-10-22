"""Verify task constraints, audit fields, polling fairness and locks on PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb

from memory.postgres import get_postgres_connection_string
from schema.video import VideoCreate, VideoListQuery, VideoStatus
from video.repository import VideoRepository


@pytest.mark.docker
@pytest.mark.asyncio
async def test_video_repository_constraints_and_reconnect():
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    other = VideoRepository(repo.conninfo)
    try:
        await repo.open()
        await other.open()
        body = VideoCreate(prompt="烟雾测试，不调用真实模型")
        results = await asyncio.gather(
            *(repo.reserve(12, "same", "a" * 64, body) for _ in range(12))
        )
        assert sum(created for _, created in results) == 1
        assert len({record.id for record, _ in results}) == 1
        record = results[0][0]
        assert await repo.latest_id(12) == record.id
        assert await repo.latest_id(13) is None
        assert record.create_by == record.update_by == 12
        assert await other.get(record.id, 13) is None
        async with repo.guard("test-task") as first:
            async with other.guard("test-task") as second:
                assert first and not second
        async with other.guard("test-task") as acquired:
            assert acquired
        record.status = VideoStatus.RUNNING
        record.task_id = "upstream-task"
        updated = await repo.save(record, 0)
        assert updated.create_time == record.create_time
        assert updated.update_time > record.update_time
        assert updated.create_by == 12 and updated.update_by == 0
        reloaded = await other.get(record.id, 12)
        assert reloaded is not None and reloaded.task_id == "upstream-task"
        assert reloaded.request_payload == body
        for index in range(11):
            await repo.reserve(12, f"next-{index}", "b" * 64, body)
        async with repo.pool.connection() as conn:
            await conn.execute("""
                UPDATE video_generation_tasks SET next_poll_time = CURRENT_TIMESTAMP,
                update_by = 0, update_time = CURRENT_TIMESTAMP
            """)
        batch = await repo.due()
        assert len(batch) == 10
        for current in batch:
            await repo.save(current, 0)
        assert len(await repo.due()) == 2
        async with repo.pool.connection() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    """
                    UPDATE video_generation_tasks SET status = 'INVALID',
                    update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s
                """,
                    (record.id,),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    """
                    UPDATE video_generation_tasks SET storage_status = 'STORED',
                    update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s
                """,
                    (record.id,),
                )
            await conn.execute(
                """
                UPDATE video_generation_tasks SET is_delete = 1,
                update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s
            """,
                (record.id,),
            )
        assert await repo.get(record.id, 12) is None
        replacement, created = await repo.reserve(12, "same", "c" * 64, body)
        assert created and replacement.id != record.id
        assert await other.latest_id(12) == replacement.id
        other_record, _ = await repo.reserve(13, "other-user", "d" * 64, body)
        assert await repo.latest_id(13) == other_record.id
        async with repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE video_generation_tasks SET is_delete = 1,
                update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s""",
                (other_record.id,),
            )
        assert await repo.latest_id(13) is None
    finally:
        await other.close()
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


@pytest.mark.docker
@pytest.mark.asyncio
async def test_video_list_filters_pagination_and_creation_order():
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        seeds = [
            (101, 12, "SUCCEEDED", "STORED", 3, 0),
            (102, 12, "RUNNING", "NOT_READY", 5, 0),
            (103, 12, "SUCCEEDED", "FAILED", 4, 0),
            (104, 12, "SUCCEEDED", "STORED", 4, 0),
            (105, 13, "SUCCEEDED", "STORED", 6, 0),
            (106, 12, "SUCCEEDED", "STORED", 7, 1),
            (107, 12, "FAILED", "NOT_READY", 2, 0),
        ]
        async with repo.pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """INSERT INTO video_generation_tasks (
                        id, user_id, idempotency_key, request_hash, request_payload,
                        status, storage_status, qiniu_video_url, qiniu_object_key,
                        create_by, update_by, create_time, update_time, is_delete
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            record_id,
                            user_id,
                            f"list-{record_id}",
                            "a" * 64,
                            Jsonb(VideoCreate(prompt="分页测试").model_dump()),
                            status,
                            storage,
                            f"https://cdn.example/{record_id}.mp4" if storage == "STORED" else None,
                            f"videos/{record_id}.mp4" if storage == "STORED" else None,
                            user_id,
                            user_id,
                            datetime(2026, 1, day, tzinfo=UTC),
                            datetime(2026, 1, day, tzinfo=UTC),
                            deleted,
                        )
                        for record_id, user_id, status, storage, day, deleted in seeds
                    ],
                )
        for page, expected_ids in [(1, [102, 104]), (2, [103, 101]), (3, [107]), (4, [])]:
            items, total = await repo.list_tasks(12, VideoListQuery(page=page, page_size=2))
            assert total == 5
            assert [item.id for item in items] == expected_ids
            assert all(item.user_id == 12 for item in items)
        for filters, expected_ids in [
            ({"status": "SUCCEEDED"}, [104, 103, 101]),
            ({"storage_status": "STORED"}, [104, 101]),
            ({"status": "SUCCEEDED", "storage_status": "FAILED"}, [103]),
            ({"status": "RUNNING", "storage_status": "STORED"}, []),
            ({"status": "FAILED"}, [107]),
        ]:
            items, total = await repo.list_tasks(12, VideoListQuery(**filters))
            assert [item.id for item in items] == expected_ids
            assert total == len(expected_ids)
        items, total = await repo.list_tasks(14, VideoListQuery())
        assert items == [] and total == 0
        other_items, total = await repo.list_tasks(13, VideoListQuery())
        assert total == 1 and other_items[0].id == 105
        _, created = await repo.reserve(
            12, "write-after-list", "b" * 64, VideoCreate(prompt="新任务")
        )
        assert created
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))
