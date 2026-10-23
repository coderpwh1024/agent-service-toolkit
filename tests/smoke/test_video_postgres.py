"""Verify task constraints, audit fields, polling fairness and locks on PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb

from memory.postgres import get_postgres_connection_string
from schema.video import ProviderResult, VideoCreate, VideoEdit, VideoListQuery, VideoStatus
from video.providers import VideoStorageError
from video.repository import VideoRepository
from video.service import VideoService


@pytest.mark.docker
@pytest.mark.asyncio
async def test_video_completion_persists_assets_and_recovers_missing_cover():
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        provider = AsyncMock()
        provider.create.return_value = ProviderResult.model_validate(
            {"output": {"task_id": "smoke-task", "task_status": "PENDING"}}
        )
        provider.query.return_value = ProviderResult.model_validate(
            {
                "output": {
                    "task_id": "smoke-task",
                    "task_status": "SUCCEEDED",
                    "video_url": "https://oss.aliyuncs.com/smoke.mp4",
                }
            }
        )
        storage = AsyncMock()
        storage.archive.return_value = (
            "videos/happyhorse/smoke-task.mp4",
            "https://cdn.example/smoke.mp4",
        )
        storage.archive_cover.side_effect = VideoStorageError("cover failure")
        service = VideoService(repo, provider, storage, 0)
        body = VideoCreate(
            prompt="海边日落。镜头跟随", cover_url="https://outside.example/input.jpg"
        )
        created = await service.create(12, "cover-smoke", body)
        assert created.title == "海边日落"
        await service.query(created.id, 12)
        failed = await repo.get(created.id, 12)
        assert failed is not None and failed.error_code == "COVER_ARCHIVE_FAILED"
        assert failed.qiniu_video_url and failed.cover_url is None
        assert failed.qiniu_cover_object_key is None
        assert failed.request_payload == body
        original_hash = failed.request_hash
        failed.storage_status = "STORED"
        failed.title = None
        await repo.save(failed, 0)
        async with repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE video_generation_tasks SET next_poll_time = CURRENT_TIMESTAMP,
                update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s""",
                (created.id,),
            )
        assert [item.id for item in await repo.due()] == [created.id]
        storage.archive_cover.side_effect = None
        storage.archive_cover.return_value = (
            "videos/happyhorse/smoke-task.jpg",
            "https://cdn.example/smoke.jpg",
        )
        repaired = await service.query(created.id, 12)
        assert (
            repaired.title == "海边日落" and repaired.cover_url == "https://cdn.example/smoke.jpg"
        )
        assert repaired.storage_status == "STORED" and repaired.error_code is None
        reloaded = await repo.get(created.id, 12)
        assert reloaded.qiniu_cover_object_key == "videos/happyhorse/smoke-task.jpg"
        assert reloaded.request_hash == original_hash and reloaded.request_payload == body
        assert reloaded.create_by == 12 and reloaded.update_by == 12
        assert await repo.due() == []
        items, total = await repo.list_tasks(12, VideoListQuery())
        assert (
            total == 1
            and items[0].title == repaired.title
            and items[0].cover_url == repaired.cover_url
        )
        assert (await service.create(12, "cover-smoke", body)).id == created.id
        storage.archive.assert_awaited_once()
        provider.query.assert_awaited_once()
        provider.create.assert_awaited_once()
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


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
        body = VideoCreate(
            prompt="烟雾测试，不调用真实模型",
            title="海边日落",
            cover_url="https://cdn.example/cover.jpg",
        )
        results = await asyncio.gather(
            *(repo.reserve(12, "same", "a" * 64, body) for _ in range(12))
        )
        assert sum(created for _, created in results) == 1
        assert len({record.id for record, _ in results}) == 1
        record = results[0][0]
        assert await repo.latest_id(12) == record.id
        assert await repo.latest_id(13) is None
        assert record.create_by == record.update_by == 12
        assert record.title == body.title and record.cover_url == str(body.cover_url)
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
        assert reloaded.title == body.title and reloaded.cover_url == str(body.cover_url)
        items, total = await other.list_tasks(12, VideoListQuery())
        assert total == 1 and items[0].title == body.title
        assert items[0].cover_url == str(body.cover_url)
        legacy = reloaded.request_payload.model_dump(mode="json")
        legacy["watermark"] = True
        async with repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE video_generation_tasks SET request_payload = %s,
                update_by = 0, update_time = CURRENT_TIMESTAMP WHERE id = %s""",
                (Jsonb(legacy), record.id),
            )
        assert (await other.get(record.id, 12)).request_payload.watermark is True
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
        edit = VideoEdit(
            source_record_id=replacement.id,
            source_url="https://cdn.example/test.mp4",
            prompt="水墨画风",
            duration=5,
            ratio="16:9",
            title="水墨画风",
            cover_url="https://cdn.example/ink.jpg",
        )
        edited, created = await repo.reserve(12, "edit-key", "e" * 64, edit)
        assert created and (await other.get(edited.id, 12)).request_payload == edit
        assert edited.title == edit.title and edited.cover_url == str(edit.cover_url)
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
async def test_video_metadata_upgrade_preserves_legacy_rows_and_is_repeatable():
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        body = VideoCreate(prompt="旧任务")
        legacy, _ = await repo.reserve(12, "legacy-key", "a" * 64, body)
        async with repo.pool.connection() as conn:
            await conn.execute(
                """ALTER TABLE video_generation_tasks DROP COLUMN title, DROP COLUMN cover_url,
                DROP COLUMN qiniu_cover_object_key"""
            )
            await conn.execute(
                """UPDATE video_generation_tasks SET request_payload = %s,
                update_by = 12, update_time = CURRENT_TIMESTAMP WHERE id = %s""",
                (Jsonb(body.model_dump(mode="json", exclude={"title", "cover_url"})), legacy.id),
            )
        await repo.close()
        for _ in range(2):
            repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
            await repo.open()
            reloaded = await repo.get(legacy.id, 12)
            assert reloaded is not None
            assert reloaded.title is None and reloaded.cover_url is None
            assert reloaded.qiniu_cover_object_key is None
            assert reloaded.request_hash == legacy.request_hash
            assert reloaded.request_payload == body
            assert reloaded.create_time == legacy.create_time
            await repo.close()
        repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
        await repo.open()
        metadata = VideoCreate(prompt="新任务", title="海边", cover_url="https://cdn.example/c.jpg")
        created, _ = await repo.reserve(12, "new-key", "b" * 64, metadata)
        assert created.title == metadata.title and created.cover_url == str(metadata.cover_url)
        async with repo.pool.connection() as conn:
            for field, value in (
                ("title", "字" * 201),
                ("cover_url", "https://cdn.example/" + "a" * 2048),
            ):
                with pytest.raises(psycopg.errors.StringDataRightTruncation):
                    await conn.execute(
                        SQL("""UPDATE video_generation_tasks SET {} = %s,
                        update_by = 12, update_time = CURRENT_TIMESTAMP WHERE id = %s""").format(
                            Identifier(field)
                        ),
                        (value, created.id),
                    )
    finally:
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
