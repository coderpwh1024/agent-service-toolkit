import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from schema.video import (
    ProviderResult,
    VideoCreate,
    VideoListQuery,
    VideoRecord,
    VideoStatus,
    VideoStorageStatus,
    VideoTask,
)
from video.providers import VideoProviderError, VideoStorageError
from video.service import VideoConflictError, VideoService


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
            task_id=None,
            status="SUBMITTING",
        )
        self.records[current.id] = current.model_copy(deep=True)
        self.keys[(user_id, key)] = current.id
        return current, True

    async def get(self, record_id, user_id):
        current = self.records.get(record_id)
        if current and current.user_id == user_id:
            return current.model_copy(deep=True)
        return None

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
    return VideoService(repo, provider, storage, 0)


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
    await service.query(1, 12)
    assert service.provider.query.await_count == service.storage.archive.await_count == 1


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
