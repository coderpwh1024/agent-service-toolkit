"""Idempotent submission, refresh and five-minute background synchronization."""

import asyncio
import hashlib
import json
import logging

from schema.video import (
    ProviderResult,
    VideoCreate,
    VideoEdit,
    VideoListQuery,
    VideoRecord,
    VideoStatus,
    VideoStorageStatus,
    VideoTask,
    VideoTaskPage,
)
from video.providers import (
    HappyHorseClient,
    QiniuVideoStorage,
    VideoProviderError,
    VideoStorageError,
)
from video.repository import VideoRepository

logger = logging.getLogger(__name__)


class VideoConflictError(ValueError):
    pass


class VideoBusyError(RuntimeError):
    pass


class VideoEditInputError(ValueError):
    pass


def public_task(record: VideoRecord) -> VideoTask:
    return VideoTask.model_validate(record.model_dump())


class VideoService:
    def __init__(
        self,
        repo: VideoRepository,
        provider: HappyHorseClient,
        storage: QiniuVideoStorage,
        service_account_id: int,
    ):
        self.repo = repo
        self.provider = provider
        self.storage = storage
        self.service_account_id = service_account_id

    async def create(self, user_id: int, key: str, body: VideoCreate) -> VideoTask:
        return await self._submit(user_id, key, body)

    async def edit(
        self,
        user_id: int,
        key: str,
        source_record_id: int,
        prompt: str,
        *,
        resolution: str = "1080P",
        audio_setting: str = "auto",
        seed: int | None = None,
        title: str | None = None,
        cover_url: str | None = None,
    ) -> VideoTask:
        source = await self.repo.get(source_record_id, user_id)
        if source is None:
            raise LookupError("未找到当前用户的视频，请核对本地视频编号")
        if (
            source.status != VideoStatus.SUCCEEDED
            or source.storage_status != VideoStorageStatus.STORED
            or not source.qiniu_video_url
        ):
            raise VideoEditInputError("原视频尚未生成并完成转存，请先查询进度，完成后再编辑")
        numerator, denominator = source.request_payload.ratio.split(":")
        if not 0.4 <= int(numerator) / int(denominator) <= 2.5:
            raise VideoEditInputError("原视频宽高比超出编辑模型支持的 1:2.5 至 2.5:1 范围")
        body = VideoEdit.model_validate(
            {
                "source_record_id": source_record_id,
                "source_url": source.qiniu_video_url,
                "prompt": prompt,
                "resolution": resolution,
                "audio_setting": audio_setting,
                "seed": seed,
                "duration": source.request_payload.duration,
                "ratio": source.request_payload.ratio,
                "watermark": False,
                "title": title,
                "cover_url": cover_url,
            }
        )
        return await self._submit(user_id, key, body)

    async def _submit(self, user_id: int, key: str, body: VideoCreate | VideoEdit) -> VideoTask:
        payload = body.model_dump(mode="json")
        for field in ("title", "cover_url"):
            if payload[field] is None:
                del payload[field]
        request_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        logger.info(
            "视频生成入参：用户ID=%s 请求摘要=%s 模型=%s 时长=%s",
            user_id,
            request_hash,
            body.model,
            body.duration,
        )
        record, created = await self.repo.reserve(user_id, key, request_hash, body)
        if record.request_hash != request_hash:
            raise VideoConflictError("幂等键已被其他生成参数使用")
        if created:
            async with self.repo.guard(f"video-task:{record.id}") as acquired:
                if not acquired:
                    raise VideoBusyError("视频任务正在处理中")
                try:
                    result = await self.provider.create(body)
                except VideoProviderError as exc:
                    record.status = (
                        VideoStatus.SUBMISSION_UNKNOWN if exc.uncertain else VideoStatus.FAILED
                    )
                    record.error_code = (
                        "SUBMISSION_UNCERTAIN" if exc.uncertain else "SUBMISSION_REJECTED"
                    )
                else:
                    self._apply(record, result)
                record = await self.repo.save(record, user_id)
        return self._output(record)

    async def query(self, record_id: int, user_id: int) -> VideoTask:
        logger.info("视频查询入参：记录ID=%s 用户ID=%s", record_id, user_id)
        record = await self.repo.get(record_id, user_id)
        if record is None:
            raise LookupError("视频任务不存在")
        async with self.repo.guard(f"video-task:{record_id}") as acquired:
            if not acquired:
                raise VideoBusyError("视频任务正在同步，请稍后查询")
            record = await self.repo.get(record_id, user_id)
            if record is None:
                raise LookupError("视频任务不存在")
            record = await self._refresh(record, user_id)
        return self._output(record)

    async def list_tasks(self, user_id: int, query: VideoListQuery) -> VideoTaskPage:
        logger.info(
            "视频列表查询入参：用户ID=%s 页码=%s 每页条数=%s 生成状态=%s 转存状态=%s",
            user_id,
            query.page,
            query.page_size,
            query.status or "全部",
            query.storage_status or "全部",
        )
        items, total = await self.repo.list_tasks(user_id, query)
        result = VideoTaskPage(
            items=items,
            total=total,
            page=query.page,
            page_size=query.page_size,
            total_pages=(total + query.page_size - 1) // query.page_size,
        )
        logger.info(
            "视频列表查询出参：用户ID=%s 页码=%s 当前条数=%s 总条数=%s 总页数=%s",
            user_id,
            result.page,
            len(result.items),
            result.total,
            result.total_pages,
        )
        return result

    @staticmethod
    def _apply(record: VideoRecord, result: ProviderResult) -> None:
        record.task_id = result.output.task_id
        record.status = VideoStatus(result.output.task_status)
        record.provider_request_id = result.request_id
        record.error_code = (
            "GENERATION_" + record.status.value
            if record.status in {VideoStatus.FAILED, VideoStatus.CANCELED, VideoStatus.UNKNOWN}
            else None
        )
        if result.output.video_url is not None:
            record.aliyun_video_url = str(result.output.video_url)
        if record.status == VideoStatus.SUCCEEDED and record.task_id:
            record.storage_status = VideoStorageStatus.PENDING

    async def _refresh(self, record: VideoRecord, actor: int) -> VideoRecord:
        if record.storage_status == VideoStorageStatus.STORED:
            return record
        if record.status == VideoStatus.SUBMITTING:
            # A lost submission response cannot be retried safely without provider idempotency.
            record.status = VideoStatus.SUBMISSION_UNKNOWN
            record.error_code = "SUBMISSION_UNCERTAIN"
            return await self.repo.save(record, actor)
        if record.status not in {VideoStatus.PENDING, VideoStatus.RUNNING, VideoStatus.SUCCEEDED}:
            return record
        if not record.task_id:
            return record
        if record.status != VideoStatus.SUCCEEDED or not record.aliyun_video_url:
            try:
                result = await self.provider.query(record.task_id)
            except VideoProviderError:
                record.error_code = "QUERY_FAILED"
                return await self.repo.save(record, actor)
            self._apply(record, result)
            record = await self.repo.save(record, actor)
        if record.status == VideoStatus.SUCCEEDED and record.task_id:
            if not record.aliyun_video_url:
                record.error_code = "VIDEO_URL_MISSING"
            else:
                try:
                    object_key, url = await self.storage.archive(
                        record.task_id, record.aliyun_video_url
                    )
                except VideoStorageError:
                    record.storage_status = VideoStorageStatus.FAILED
                    record.error_code = "ARCHIVE_FAILED"
                else:
                    record.qiniu_object_key = object_key
                    record.qiniu_video_url = url
                    record.storage_status = VideoStorageStatus.STORED
                    record.error_code = None
            record = await self.repo.save(record, actor)
        return record

    async def sync_once(self) -> None:
        async with self.repo.guard("video-scheduler") as acquired:
            if not acquired:
                return
            records = await self.repo.due()
            logger.info("视频定时同步入参：待处理数量=%s 每批上限=10", len(records))
            for record in records:
                try:
                    async with self.repo.guard(f"video-task:{record.id}") as locked:
                        if not locked:
                            continue
                        current = await self.repo.get(record.id, record.user_id)
                        if current is not None:
                            await self._refresh(current, self.service_account_id)
                except Exception as exc:
                    logger.error(
                        "视频定时同步异常：记录ID=%s 异常类型=%s", record.id, type(exc).__name__
                    )
            logger.info("视频定时同步出参：本批处理结束 数量=%s", len(records))

    async def run(self) -> None:
        while True:
            try:
                async with self.repo.guard("video-worker") as leader:
                    if leader:
                        while True:
                            started = asyncio.get_running_loop().time()
                            await self.sync_once()
                            elapsed = asyncio.get_running_loop().time() - started
                            await asyncio.sleep(max(0, 300 - elapsed))
            except Exception as exc:
                logger.error("视频定时任务异常：异常类型=%s", type(exc).__name__)
            await asyncio.sleep(300)

    @staticmethod
    def _output(record: VideoRecord) -> VideoTask:
        logger.info(
            "视频接口出参：记录ID=%s 任务ID=%s 生成状态=%s 转存状态=%s 已有七牛地址=%s",
            record.id,
            record.task_id,
            record.status,
            record.storage_status,
            record.qiniu_video_url is not None,
        )
        return public_task(record)
