"""Verify task constraints, audit fields, polling fairness and locks on PostgreSQL."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb

from memory.postgres import get_postgres_connection_string
from schema.video import (
    ProviderResult,
    VideoCreate,
    VideoEdit,
    VideoListQuery,
    VideoMetadataUpdate,
    VideoStatus,
    VideoStorageStatus,
)
from video.providers import VideoStorageError
from video.repository import VideoRepository
from video.service import VideoService


@pytest.mark.docker
@pytest.mark.asyncio
async def test_video_metadata_update_and_soft_delete_are_owned_and_audited() -> None:
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        service = VideoService(repo, AsyncMock(), AsyncMock(), 0)
        record, _ = await repo.reserve(12, "metadata", "a" * 64, VideoCreate(prompt="海边"))
        original_payload = record.request_payload
        original_hash = record.request_hash
        original_create_time = record.create_time

        renamed = await service.update_metadata(record.id, 12, VideoMetadataUpdate(title="新标题"))
        assert renamed.title == "新标题" and renamed.cover_url is None
        record = await repo.get(record.id, 12)
        assert record is not None
        assert record.update_by == 12 and record.update_time >= original_create_time
        record.status = VideoStatus.SUCCEEDED
        record.storage_status = VideoStorageStatus.STORED
        record.qiniu_video_url = "https://cdn.example/video.mp4"
        record.qiniu_object_key = "videos/video.mp4"
        record.qiniu_cover_object_key = "videos/auto.jpg"
        record.cover_url = "https://cdn.example/auto.jpg"
        await repo.save(record, 0)

        edited = await service.update_metadata(
            record.id,
            12,
            VideoMetadataUpdate(cover_url="https://cdn.example/custom.jpg"),
        )
        assert edited.cover_url == "https://cdn.example/custom.jpg"
        assert (await service.query(record.id, 12)).cover_url == edited.cover_url
        stored = await repo.get(record.id, 12)
        assert stored is not None
        assert stored.request_payload == original_payload and stored.request_hash == original_hash
        assert stored.create_time == original_create_time and stored.update_by == 12
        assert (await repo.list_tasks(12, VideoListQuery(q="新标题")))[1] == 1
        with pytest.raises(LookupError):
            await service.update_metadata(record.id, 13, VideoMetadataUpdate(title="他人标题"))
        with pytest.raises(LookupError):
            await service.delete(record.id, 13)

        await service.delete(record.id, 12)

        assert await repo.get(record.id, 12) is None
        assert (await repo.list_tasks(12, VideoListQuery()))[1] == 0
        assert await repo.latest_id(12) is None
        async with repo.pool.connection() as conn:
            deleted = await (
                await conn.execute(
                    """SELECT is_delete, update_by, update_time FROM video_generation_tasks
                    WHERE id = %s""",
                    (record.id,),
                )
            ).fetchone()
        assert deleted is not None
        assert deleted["is_delete"] == 1 and deleted["update_by"] == 12
        assert deleted["update_time"] >= stored.update_time
        with pytest.raises(LookupError):
            await service.delete(record.id, 12)
        replacement, created = await repo.reserve(
            12, "metadata", "b" * 64, VideoCreate(prompt="新任务")
        )
        assert created and replacement.id != record.id
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


@pytest.mark.docker
@pytest.mark.asyncio
async def test_wan3_migration_preserves_legacy_hash_and_filters_owned_models() -> None:
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    scoped = make_conninfo(conninfo, options=f"-csearch_path={schema}")
    repo = VideoRepository(scoped)
    try:
        old_payload = {
            "prompt": "旧视频",
            "model": "happyhorse-1.0-t2v",
            "resolution": "1080P",
            "ratio": "16:9",
            "duration": 5,
            "watermark": False,
            "seed": None,
        }
        old_hash = hashlib.sha256(
            json.dumps(old_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        async with await psycopg.AsyncConnection.connect(scoped, autocommit=True) as conn:
            await conn.execute(
                SQL(Path("src/video/sql/create_video_generation_tasks.sql").read_text())
            )
            row = await (
                await conn.execute(
                    """
                INSERT INTO video_generation_tasks (
                    user_id, idempotency_key, request_hash, request_payload, create_by, update_by
                ) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, create_time, update_time
            """,
                    (12, "legacy-wan-upgrade", old_hash, Jsonb(old_payload), 12, 12),
                )
            ).fetchone()
        assert row is not None
        legacy_id, created_at, updated_at = row
        for _ in range(2):
            repo = VideoRepository(scoped)
            await repo.open()
            old = await repo.get(legacy_id, 12)
            assert old is not None and old.model == "happyhorse-1.0-t2v"
            assert old.request_hash == old_hash
            assert old.create_time == created_at and old.update_time == updated_at
            assert old.request_payload == VideoCreate.model_validate(old_payload)
            await repo.close()
        repo = VideoRepository(scoped)
        await repo.open()
        service = VideoService(repo, AsyncMock(), AsyncMock(), 0)
        replay = await service.create(
            12, "legacy-wan-upgrade", VideoCreate.model_validate(old_payload)
        )
        assert replay.id == legacy_id
        service.provider.create.assert_not_awaited()
        happyhorse, _ = await repo.reserve(
            12, "new-happyhorse", "f" * 64, VideoCreate(prompt="兼容旧版")
        )
        async with repo.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """SELECT request_payload ?| ARRAY[
                'first_frame_url', 'last_frame_url', 'audio', 'prompt_extend'
            ] AS has_wan_fields FROM video_generation_tasks WHERE id = %s""",
                    (happyhorse.id,),
                )
            ).fetchone()
            assert row is not None and row["has_wan_fields"] is False
        body = VideoCreate(
            model="wan3.0-video",
            prompt="首尾帧过渡",
            duration=30,
            audio=False,
            first_frame_url="https://images.example/start.png",
            last_frame_url="https://images.example/end.png",
        )
        wan, _ = await repo.reserve(12, "wan-create", "b" * 64, body)
        await repo.reserve(13, "wan-other-user", "c" * 64, body)
        prime, _ = await repo.reserve(
            12, "wan-prime", "d" * 64, body.model_copy(update={"model": "wan3.0-video-prime"})
        )
        assert (await repo.get(wan.id, 12)).request_payload == body
        assert (await repo.get(wan.id, 12)).last_frame_url == str(body.last_frame_url)
        for model, expected in [
            ("wan3.0-video", wan.id),
            ("wan3.0-video-prime", prime.id),
            ("happyhorse-1.0-t2v", legacy_id),
        ]:
            items, total = await repo.list_tasks(12, VideoListQuery(model=model))
            assert total == 1 and [item.id for item in items] == [expected]
            assert items[0].model == model
        items, total = await repo.list_tasks(12, VideoListQuery(model="wan3.0-video", q="首尾帧"))
        assert total == 1 and items[0].first_frame_url == str(body.first_frame_url)
        assert (await repo.list_tasks(14, VideoListQuery(model="wan3.0-video")))[1] == 0
        async with repo.pool.connection() as conn:
            for field, value in [("model", "unknown-model"), ("first_frame_url", None)]:
                with pytest.raises(psycopg.errors.CheckViolation):
                    await conn.execute(
                        SQL("""UPDATE video_generation_tasks SET {} = %s,
                        update_by = 12, update_time = CURRENT_TIMESTAMP WHERE id = %s""").format(
                            Identifier(field)
                        ),
                        (value, wan.id),
                    )
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


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


@pytest.mark.docker
@pytest.mark.asyncio
async def test_video_keyword_search_matches_titles_and_descriptions_with_user_isolation():
    schema = "video_smoke_" + uuid4().hex
    conninfo = get_postgres_connection_string()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = VideoRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        seeds = [
            (12, "海边日落", "海鸥飞过，Sunset sky", "SUCCEEDED", 0),
            (12, "海边清晨", "帆船驶过", "PENDING", 0),
            (12, "旧任务", "海边海鸥飞过", "PENDING", 0),
            (13, "海边海鸥", "其他用户", "PENDING", 0),
            (12, "海边海鸥", "已删除", "PENDING", 1),
            (12, "100%_完成!", "字面符号与反斜线\\", "PENDING", 0),
            (12, "100ABC完成", "普通描述", "PENDING", 0),
            (12, "引号", "' OR 1=1 --", "PENDING", 0),
        ]
        ids = []
        for index, (user_id, title, prompt, status, deleted) in enumerate(seeds):
            record, _ = await repo.reserve(
                user_id,
                f"search-{index}",
                "a" * 64,
                VideoCreate(title=title, prompt=prompt),
            )
            ids.append(record.id)
            async with repo.pool.connection() as conn:
                await conn.execute(
                    """UPDATE video_generation_tasks SET title = %s, status = %s,
                    is_delete = %s, create_time = %s,
                    update_by = %s, update_time = CURRENT_TIMESTAMP WHERE id = %s""",
                    (
                        None if index == 2 else title,
                        status,
                        deleted,
                        datetime(2026, 1, index + 1, tzinfo=UTC),
                        user_id,
                        record.id,
                    ),
                )
        for query, expected in [
            (VideoListQuery(q="海边"), [ids[2], ids[1], ids[0]]),
            (VideoListQuery(q=" 海边\t海鸥 "), [ids[2], ids[0]]),
            (VideoListQuery(q="海边 海鸥", status="SUCCEEDED"), [ids[0]]),
            (VideoListQuery(q="sUnSeT"), [ids[0]]),
            (VideoListQuery(q="%_"), [ids[5]]),
            (VideoListQuery(q="!"), [ids[5]]),
            (VideoListQuery(q="\\"), [ids[5]]),
            (VideoListQuery(q="' OR 1=1 --"), [ids[7]]),
            (VideoListQuery(q="不存在的场景"), []),
        ]:
            items, total = await repo.list_tasks(12, query)
            assert [item.id for item in items] == expected
            assert total == len(expected)
            assert all(item.user_id == 12 for item in items)
        for page, expected in [(1, [ids[2]]), (2, [ids[0]]), (3, [])]:
            items, total = await repo.list_tasks(
                12, VideoListQuery(q="海边 海鸥", page=page, page_size=1)
            )
            assert [item.id for item in items] == expected and total == 2
        other_items, total = await repo.list_tasks(13, VideoListQuery(q="海边 海鸥"))
        assert total == 1 and other_items[0].id == ids[3]
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))
