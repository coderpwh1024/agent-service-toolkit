import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from schema.video import (
    ProviderResult,
    VideoCreate,
    VideoEdit,
    VideoListQuery,
    VideoMetadataUpdate,
    VideoRecord,
    VideoStatus,
    VideoStorageStatus,
    VideoTask,
)
from video.metadata import derive_video_title
from video.providers import VideoProviderError, VideoStorageError
from video.service import VideoBusyError, VideoConflictError, VideoEditInputError, VideoService


def record(**changes) -> VideoRecord:
    values = dict(
        id=1,
        user_id=12,
        task_id="task-123",
        status="PENDING",
        storage_status="NOT_READY",
        aliyun_video_url=None,
        qiniu_video_url=None,
        qiniu_object_key=None,
        error_code=None,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
        next_poll_time=datetime.now(UTC),
        idempotency_key="test-key",
        request_hash="hash",
        request_payload=VideoCreate(prompt="私人提示词"),
        provider_request_id=None,
        create_by=12,
        update_by=12,
        is_delete=0,
    )
    values.update(changes)
    return VideoRecord(**values)


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.keys = {}
        self.locked = set()
        self.saved_actors = []

    @asynccontextmanager
    async def guard(self, key):
        if key in self.locked:
            yield False
            return
        self.locked.add(key)
        try:
            yield True
        finally:
            self.locked.remove(key)

    async def reserve(self, user_id, key, request_hash, body):
        existing = self.keys.get((user_id, key))
        if existing:
            return self.records[existing].model_copy(deep=True), False
        current = record(
            id=len(self.records) + 1,
            user_id=user_id,
            idempotency_key=key,
            request_hash=request_hash,
            request_payload=body,
            title=body.title or derive_video_title(body.prompt),
            cover_url=str(body.cover_url) if body.cover_url is not None else None,
            task_id=None,
            status="SUBMITTING",
        )
        self.records[current.id] = current.model_copy(deep=True)
        self.keys[(user_id, key)] = current.id
        return current, True

    async def get(self, record_id, user_id):
        current = self.records.get(record_id)
        if current and current.user_id == user_id and current.is_delete == 0:
            return current.model_copy(deep=True)
        return None

    async def update_metadata(self, record_id, user_id, *, title, cover_url):
        current = await self.get(record_id, user_id)
        if current is None:
            raise LookupError("视频任务不存在")
        if title is not None:
            current.title = title
        if cover_url is not None:
            current.cover_url = cover_url
        current.update_by = user_id
        current.update_time = datetime.now(UTC)
        self.records[record_id] = current.model_copy(deep=True)
        return current

    async def delete(self, record_id, user_id):
        current = await self.get(record_id, user_id)
        if current is None:
            raise LookupError("视频任务不存在")
        current.is_delete = 1
        current.update_by = user_id
        current.update_time = datetime.now(UTC)
        self.records[record_id] = current

    async def save(self, current, actor):
        self.saved_actors.append(actor)
        self.records[current.id] = current.model_copy(deep=True)
        return current

    async def due(self):
        return list(self.records.values())[:10]


@pytest.fixture
def service():
    repo = MemoryRepository()
    provider = AsyncMock()
    provider.create.return_value = ProviderResult.model_validate(
        {
            "output": {"task_id": "task-123", "task_status": "PENDING"},
        }
    )
    storage = AsyncMock()
    storage.archive.return_value = (
        "videos/happyhorse/task-123.mp4",
        "https://cdn.example/video.mp4",
    )
    storage.archive_cover.return_value = (
        "videos/happyhorse/task-123.jpg",
        "https://cdn.example/cover.jpg",
    )
    return VideoService(repo, provider, storage, 0)


@pytest.mark.asyncio
async def test_edit_is_new_idempotent_task_with_original_preserved(service):
    source = record(
        status="SUCCEEDED",
        storage_status="STORED",
        qiniu_video_url="https://cdn.example/original.mp4",
        qiniu_object_key="original.mp4",
    )
    service.repo.records[source.id] = source
    first = await service.edit(12, "edit-key", 1, "改为水墨风格", audio_setting="origin")
    second = await service.edit(12, "edit-key", 1, "改为水墨风格", audio_setting="origin")
    assert first.id == second.id and first.id != source.id
    service.provider.create.assert_awaited_once()
    body = service.provider.create.await_args.args[0]
    assert isinstance(body, VideoEdit)
    assert body.source_record_id == 1 and body.watermark is False
    assert body.audio_setting == "origin"
    assert service.repo.records[1] == source
    assert (
        VideoRecord.model_validate(
            service.repo.records[first.id].model_dump(mode="json")
        ).request_payload
        == body
    )
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "video_url": "https://test.aliyuncs.com/edit.mp4",
            }
        }
    )
    ready = await service.query(first.id, 12)
    assert ready.storage_status == "STORED"
    service.storage.archive.assert_awaited_once()


@pytest.mark.asyncio
async def test_metadata_update_preserves_video_and_survives_query(service):
    original = record(
        title="旧标题",
        cover_url="https://cdn.example/old.jpg",
        status="SUCCEEDED",
        storage_status="STORED",
        qiniu_video_url="https://cdn.example/video.mp4",
        qiniu_object_key="videos/video.mp4",
        qiniu_cover_object_key="videos/cover.jpg",
    )
    service.repo.records[original.id] = original

    updated = await service.update_metadata(
        1,
        12,
        VideoMetadataUpdate(title=" 新标题 ", cover_url="https://cdn.example/custom.jpg"),
    )

    assert updated.title == "新标题"
    assert updated.cover_url == "https://cdn.example/custom.jpg"
    assert updated.qiniu_video_url == original.qiniu_video_url
    assert service.repo.records[1].request_hash == original.request_hash
    assert service.repo.records[1].request_payload == original.request_payload
    assert service.repo.records[1].create_time == original.create_time
    assert service.repo.records[1].update_by == 12
    assert (await service.query(1, 12)).cover_url == updated.cover_url
    previous_update_time = service.repo.records[1].update_time
    repeated = await service.update_metadata(
        1,
        12,
        VideoMetadataUpdate(title="新标题", cover_url="https://cdn.example/custom.jpg"),
    )
    assert repeated == updated
    assert service.repo.records[1].update_time == previous_update_time
    service.provider.query.assert_not_awaited()
    service.storage.archive_cover.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_update_allows_title_during_generation_but_rejects_early_cover(service):
    service.repo.records[1] = record(title="旧标题")
    updated = await service.update_metadata(1, 12, VideoMetadataUpdate(title="新标题"))
    assert updated.title == "新标题"
    assert updated.cover_url is None
    with pytest.raises(VideoConflictError, match="封面仅可"):
        await service.update_metadata(
            1, 12, VideoMetadataUpdate(cover_url="https://cdn.example/custom.jpg")
        )
    assert service.repo.records[1].title == "新标题"
    assert service.repo.records[1].cover_url is None


@pytest.mark.asyncio
async def test_metadata_update_and_delete_enforce_owner_and_task_lock(service):
    service.repo.records[1] = record()
    with pytest.raises(LookupError):
        await service.update_metadata(1, 13, VideoMetadataUpdate(title="其他用户"))
    with pytest.raises(LookupError):
        await service.delete(1, 13)
    service.repo.locked.add("video-task:1")
    with pytest.raises(VideoBusyError):
        await service.update_metadata(1, 12, VideoMetadataUpdate(title="并发"))
    with pytest.raises(VideoBusyError):
        await service.delete(1, 12)
    service.repo.locked.clear()

    await service.delete(1, 12)

    assert service.repo.records[1].is_delete == 1
    assert service.repo.records[1].update_by == 12
    assert await service.repo.get(1, 12) is None
    with pytest.raises(LookupError):
        await service.delete(1, 12)
    with pytest.raises(LookupError):
        await service.update_metadata(1, 12, VideoMetadataUpdate(title="删除后"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["other_user", "unfinished", "ratio", "invalid_resolution", "title", "cover_url"]
)
async def test_edit_rejects_invalid_source_or_parameters_before_reservation(service, condition):
    source = record(
        status="SUCCEEDED",
        storage_status="STORED",
        qiniu_video_url="https://cdn.example/original.mp4",
        qiniu_object_key="original.mp4",
    )
    if condition == "other_user":
        source.user_id = 13
    elif condition == "unfinished":
        source.status = "RUNNING"
        source.storage_status = "NOT_READY"
    elif condition == "ratio":
        source.request_payload = source.request_payload.model_copy(update={"ratio": "1:3"})
    service.repo.records[1] = source
    with pytest.raises((LookupError, ValueError)):
        await service.edit(
            12,
            "edit-key",
            1,
            "水墨风格",
            resolution="480P" if condition == "invalid_resolution" else "1080P",
            title=" " if condition == "title" else None,
            cover_url="ftp://cdn.example/c.jpg" if condition == "cover_url" else None,
        )
    assert len(service.repo.records) == 1
    service.provider.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_repeated_submission_and_parameter_conflict(service):
    body = VideoCreate(prompt="私人提示词")
    results = await asyncio.gather(*(service.create(12, "same", body) for _ in range(20)))
    assert {result.id for result in results} == {1}
    assert service.provider.create.await_count == 1
    with pytest.raises(VideoConflictError):
        await service.create(12, "same", VideoCreate(prompt="其他参数"))
    assert service.provider.create.await_count == 1


@pytest.mark.asyncio
async def test_metadata_survives_submission_query_and_conflicts_on_change(service):
    body = VideoCreate(
        prompt="海边日落", title="海边日落", cover_url="https://cdn.example/cover.jpg"
    )
    result = await service.create(12, "metadata", body)
    assert result.title == body.title and result.cover_url == str(body.cover_url)
    service.provider.query.return_value = ProviderResult.model_validate(
        {"output": {"task_id": "task-123", "task_status": "RUNNING"}}
    )
    refreshed = await service.query(result.id, 12)
    assert refreshed.title == result.title and refreshed.cover_url == result.cover_url
    for changes in ({"title": "新标题"}, {"cover_url": "https://cdn.example/other.jpg"}):
        with pytest.raises(VideoConflictError):
            await service.create(12, "metadata", VideoCreate(**(body.model_dump() | changes)))
    service.provider.create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("editing", [False, True])
async def test_legacy_hash_allows_replay_with_omitted_or_null_metadata(service, editing):
    body = (
        VideoEdit(
            source_record_id=9,
            source_url="https://cdn.example/source.mp4",
            prompt="水墨风格",
            duration=5,
            ratio="16:9",
        )
        if editing
        else VideoCreate(prompt="旧请求", seed=42)
    )
    legacy_payload = body.model_dump(
        mode="json",
        exclude={
            "title",
            "cover_url",
            "first_frame_url",
            "last_frame_url",
            "audio",
            "prompt_extend",
        },
    )
    legacy_hash = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    original, _ = await service.repo.reserve(12, "legacy", legacy_hash, body)
    result = await service._submit(12, "legacy", type(body)(**legacy_payload))
    assert result.id == original.id
    assert result.title == derive_video_title(body.prompt) and result.cover_url is None
    explicit_null = type(body)(**legacy_payload, title=None, cover_url=None)
    assert (await service._submit(12, "legacy", explicit_null)).id == original.id
    service.provider.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_metadata_belongs_to_new_task_without_copying_source_cover(service):
    source = record(
        title="原视频",
        cover_url="https://cdn.example/original.jpg",
        status="SUCCEEDED",
        storage_status="STORED",
        qiniu_video_url="https://cdn.example/original.mp4",
        qiniu_object_key="original.mp4",
    )
    service.repo.records[1] = source
    result = await service.edit(
        12,
        "edit-metadata",
        1,
        "水墨风格",
        title="水墨海边",
        cover_url="https://cdn.example/ink.jpg",
    )
    assert result.title == "水墨海边" and result.cover_url == "https://cdn.example/ink.jpg"
    assert service.repo.records[1] == source
    without_metadata = await service.edit(12, "edit-no-metadata", 1, "水墨风格")
    assert without_metadata.title == "水墨风格" and without_metadata.cover_url is None


@pytest.mark.asyncio
async def test_idempotency_is_scoped_to_user(service):
    first = await service.create(12, "same", VideoCreate(prompt="a"))
    second = await service.create(13, "same", VideoCreate(prompt="a"))
    assert first.id != second.id
    with pytest.raises(LookupError):
        await service.query(first.id, 13)
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain, expected", [(True, "SUBMISSION_UNKNOWN"), (False, "FAILED")])
async def test_failed_submission_is_never_automatically_reposted(service, uncertain, expected):
    service.provider.create.side_effect = VideoProviderError(uncertain=uncertain)
    body = VideoCreate(prompt="a")
    result = await service.create(12, "same", body)
    assert result.status == expected
    assert (await service.create(12, "same", body)).id == result.id
    await service.sync_once()
    assert service.provider.create.await_count == 1


@pytest.mark.asyncio
async def test_query_persists_source_before_archival_and_is_idempotent(service):
    service.repo.records[1] = record()
    source = "https://dashscope-result.oss-cn-beijing.aliyuncs.com/private.mp4?Signature=private"
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {"task_id": "task-123", "task_status": "SUCCEEDED", "video_url": source},
        }
    )

    async def archive(task_id, url):
        persisted = service.repo.records[1]
        assert persisted.aliyun_video_url == source
        assert persisted.status == VideoStatus.SUCCEEDED
        return "object", "https://cdn.example/video.mp4"

    service.storage.archive.side_effect = archive
    result = await service.query(1, 12)
    assert result.qiniu_video_url == "https://cdn.example/video.mp4"
    assert result.storage_status == VideoStorageStatus.STORED
    assert result.title and result.cover_url == "https://cdn.example/cover.jpg"
    service.storage.archive_cover.assert_awaited_once_with("task-123", "object")
    await service.query(1, 12)
    assert service.provider.query.await_count == service.storage.archive.await_count == 1


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("海边日落。镜头缓缓推进", "海边日落"),
        ("\n\n 海边  日落\n下一镜头", "海边 日落"),
        ("。森林！", "森林"),
        ("Beach at sunset. Camera follows a horse.", "Beach at sunset"),
        ("风景" * 100, "风景" * 40),
    ],
)
def test_title_comes_from_description_and_stays_bounded(prompt, expected):
    assert derive_video_title(prompt) == expected


@pytest.mark.asyncio
async def test_cover_failure_keeps_archived_video_and_retries_only_cover(service):
    service.repo.records[1] = record(
        status="SUCCEEDED",
        storage_status="PENDING",
        aliyun_video_url="https://oss.aliyuncs.com/v.mp4",
    )
    service.storage.archive_cover.side_effect = VideoStorageError("cover failure")
    result = await service.query(1, 12)
    assert result.status == "SUCCEEDED" and result.storage_status == "FAILED"
    assert result.error_code == "COVER_ARCHIVE_FAILED" and result.cover_url is None
    assert result.title and result.qiniu_video_url
    service.storage.archive_cover.side_effect = None
    await service.sync_once()
    persisted = service.repo.records[1]
    assert persisted.storage_status == "STORED" and persisted.error_code is None
    assert persisted.title and persisted.cover_url and persisted.qiniu_cover_object_key
    service.storage.archive.assert_awaited_once()
    service.provider.create.assert_not_awaited()
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_cover_cancellation_keeps_video_for_resume_without_regeneration(service):
    service.repo.records[1] = record(
        status="SUCCEEDED",
        storage_status="PENDING",
        aliyun_video_url="https://oss.aliyuncs.com/v.mp4",
    )
    service.storage.archive_cover.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.query(1, 12)
    pending = service.repo.records[1]
    assert pending.qiniu_video_url and pending.storage_status == "PENDING"
    assert pending.title and pending.cover_url is None
    service.storage.archive_cover.side_effect = None
    resumed = await service.query(1, 12)
    assert resumed.storage_status == "STORED" and resumed.title and resumed.cover_url
    service.storage.archive.assert_awaited_once()
    service.provider.create.assert_not_awaited()
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_immediate_success_is_returned_with_title_and_permanent_cover(service):
    service.provider.create.return_value = ProviderResult.model_validate(
        {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "video_url": "https://oss.aliyuncs.com/v.mp4",
            }
        }
    )
    result = await service.create(12, "immediate", VideoCreate(prompt="海边日落。镜头推进"))
    assert result.title == "海边日落" and result.cover_url == "https://cdn.example/cover.jpg"
    assert result.storage_status == "STORED"
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_repairs_legacy_stored_video_without_using_expired_provider_url(service):
    service.repo.records[1] = record(
        status="SUCCEEDED",
        storage_status="STORED",
        title=None,
        cover_url="https://untrusted.example/supplied.jpg",
        qiniu_cover_object_key=None,
        qiniu_video_url="https://cdn.example/video.mp4",
        qiniu_object_key="videos/original.mp4",
        aliyun_video_url=None,
    )
    result = await service.query(1, 12)
    assert result.title == "私人提示词" and result.cover_url == "https://cdn.example/cover.jpg"
    assert result.storage_status == "STORED"
    await service.query(1, 12)
    service.storage.archive_cover.assert_awaited_once_with("task-123", "videos/original.mp4")
    service.storage.archive.assert_not_awaited()
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_query_preserves_custom_title_and_original_request_hash(service):
    body = VideoCreate(
        prompt="描述首句。后续镜头", title="自定义标题", cover_url="https://outside.example/c.jpg"
    )
    result = await service.create(12, "ready", body)
    original_payload = service.repo.records[result.id].request_payload.model_dump()
    original_hash = service.repo.records[result.id].request_hash
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "video_url": "https://oss.aliyuncs.com/v.mp4",
            }
        }
    )
    result = await service.query(result.id, 12)
    assert result.title == "自定义标题" and result.cover_url == "https://cdn.example/cover.jpg"
    assert service.repo.records[result.id].request_payload.model_dump() == original_payload
    assert service.repo.records[result.id].request_hash == original_hash
    assert (await service.create(12, "ready", body)).id == result.id
    service.provider.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_archival_failure_remains_retryable_without_losing_success(service):
    service.repo.records[1] = record(
        status="SUCCEEDED",
        storage_status="PENDING",
        aliyun_video_url="https://oss.aliyuncs.com/v.mp4",
    )
    service.storage.archive.side_effect = VideoStorageError("failure")
    result = await service.query(1, 12)
    assert result.status == "SUCCEEDED"
    assert result.storage_status == "FAILED"
    assert result.qiniu_video_url is None
    service.storage.archive.side_effect = None
    await service.sync_once()
    assert service.repo.records[1].storage_status == "STORED"
    assert service.repo.saved_actors[-1] == 0
    service.provider.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_failure_keeps_task_for_retry(service):
    service.repo.records[1] = record(status="RUNNING")
    service.provider.query.side_effect = VideoProviderError()
    result = await service.query(1, 12)
    assert result.status == "RUNNING"
    assert result.error_code == "QUERY_FAILED"
    assert service.repo.records[1].error_code == "QUERY_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "UNKNOWN"])
async def test_provider_terminal_states_are_persisted(service, status):
    service.repo.records[1] = record()
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {"task_id": "task-123", "task_status": status},
        }
    )
    await service.query(1, 12)
    assert service.repo.records[1].status == status
    await service.query(1, 12)
    assert service.provider.query.await_count == 1
    service.storage.archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_success_url_is_retried(service):
    service.repo.records[1] = record()
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {"task_id": "task-123", "task_status": "SUCCEEDED"},
        }
    )
    result = await service.query(1, 12)
    assert result.error_code == "VIDEO_URL_MISSING"
    await service.query(1, 12)
    assert service.provider.query.await_count == 2


@pytest.mark.asyncio
async def test_batch_limit_and_scheduler_lock(service):
    for record_id in range(1, 13):
        service.repo.records[record_id] = record(id=record_id)
    service.provider.query.side_effect = VideoProviderError()
    await service.sync_once()
    assert service.provider.query.await_count == 10
    service.repo.locked.add("video-scheduler")
    await service.sync_once()
    assert service.provider.query.await_count == 10


@pytest.mark.asyncio
async def test_abandoned_submission_becomes_uncertain(service):
    service.repo.records[1] = record(task_id=None, status="SUBMITTING")
    await service.sync_once()
    assert service.repo.records[1].status == "SUBMISSION_UNKNOWN"
    service.provider.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_chinese_logs_do_not_expose_prompt_or_signed_urls(service, caplog):
    caplog.set_level(logging.INFO, logger="video")
    await service.create(12, "private-key", VideoCreate(prompt="私人提示词"))
    assert "视频生成入参" in caplog.text
    assert "视频接口出参" in caplog.text
    assert "私人提示词" not in caplog.text
    assert "private-key" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("total, page, expected_pages", [(0, 1, 0), (21, 1, 2), (21, 3, 2)])
async def test_list_page_metadata_and_no_upstream_calls(service, total, page, expected_pages):
    items = [VideoTask.model_validate(record().model_dump())] if total and page == 1 else []
    service.repo.list_tasks = AsyncMock(return_value=(items, total))
    query = VideoListQuery(page=page, status="SUCCEEDED")
    result = await service.list_tasks(12, query)
    assert result.items == items
    assert result.total == total and result.total_pages == expected_pages
    assert result.page == page and result.page_size == 20
    service.repo.list_tasks.assert_awaited_once_with(12, query)
    service.provider.query.assert_not_awaited()
    service.provider.create.assert_not_awaited()
    service.storage.archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_chinese_logs_only_include_summary(service, caplog):
    task = VideoTask.model_validate(
        record(
            aliyun_video_url="https://result.oss.aliyuncs.com/v.mp4?Signature=private-signature",
        ).model_dump()
    )
    service.repo.list_tasks = AsyncMock(return_value=([task], 1))
    caplog.set_level(logging.INFO, logger="video")
    await service.list_tasks(12, VideoListQuery())
    assert "视频列表查询入参" in caplog.text and "视频列表查询出参" in caplog.text
    assert "private-signature" not in caplog.text and "私人提示词" not in caplog.text


@pytest.mark.asyncio
async def test_wan3_create_replay_query_and_archive_preserve_inputs(service, caplog) -> None:
    body = VideoCreate(
        model="wan3.0-video",
        prompt="私人场景",
        duration=30,
        ratio="adaptive",
        audio=False,
        first_frame_url="https://images.example/start.png?sig=private-start",
        last_frame_url="https://images.example/end.png?sig=private-end",
    )
    caplog.set_level(logging.INFO, logger="video")
    created = await service.create(12, "wan-key", body)
    replay = await service.create(12, "wan-key", body)
    assert replay.id == created.id
    assert created.model == body.model and created.first_frame_url == str(body.first_frame_url)
    service.provider.create.assert_awaited_once_with(body)
    with pytest.raises(VideoConflictError):
        await service.create(12, "wan-key", body.model_copy(update={"last_frame_url": None}))
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "video_url": "https://oss.aliyuncs.com/wan.mp4",
            }
        }
    )
    completed = await service.query(created.id, 12)
    assert completed.storage_status == "STORED"
    assert completed.last_frame_url == str(body.last_frame_url)
    service.storage.archive.assert_awaited_once_with(
        "task-123", "https://oss.aliyuncs.com/wan.mp4", prefix="wan3"
    )
    assert service.repo.records[created.id].request_payload == body
    assert "private-start" not in caplog.text and "private-end" not in caplog.text
    assert "私人场景" not in caplog.text


@pytest.mark.asyncio
async def test_wan3_image_only_has_generated_title(service) -> None:
    body = VideoCreate(model="wan3.0-video", first_frame_url="https://images.example/start.png")
    created = await service.create(12, "image-only", body)
    service.provider.query.return_value = ProviderResult.model_validate(
        {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "video_url": "https://oss.aliyuncs.com/wan.mp4",
            }
        }
    )
    completed = await service.query(created.id, 12)
    assert completed.title == "万相图片视频" and completed.storage_status == "STORED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duration, ratio", [(30, "16:9"), (-1, "16:9"), (5, "adaptive"), (2, "16:9")]
)
async def test_wan3_incompatible_source_is_not_submitted_to_happyhorse_edit(
    service, duration: int, ratio: str
) -> None:
    service.repo.records[1] = record(
        status="SUCCEEDED",
        storage_status="STORED",
        qiniu_video_url="https://cdn.example/wan.mp4",
        request_payload=VideoCreate(
            model="wan3.0-video", prompt="海边", duration=duration, ratio=ratio
        ),
    )
    with pytest.raises(VideoEditInputError, match="3 至 15 秒"):
        await service.edit(12, "edit-wan", 1, "水墨风格")
    service.provider.create.assert_not_awaited()
