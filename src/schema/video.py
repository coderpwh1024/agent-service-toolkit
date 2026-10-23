"""Public video contracts and provider task states."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class VideoStatus(StrEnum):
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"


class VideoStorageStatus(StrEnum):
    NOT_READY = "NOT_READY"
    PENDING = "PENDING"
    STORED = "STORED"
    FAILED = "FAILED"


class VideoMetadata(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="可选展示标题；未指定时从生成描述首句提取，最多 80 字符",
    )
    cover_url: HttpUrl | None = Field(
        default=None,
        max_length=2048,
        description="兼容可选的暂存封面；完成后替换为生成视频实际截帧的七牛永久地址",
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("视频标题不能为空")
        return value.strip()


class VideoCreate(VideoMetadata):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=5000)
    model: Literal["happyhorse-1.1-t2v", "happyhorse-1.0-t2v"] = "happyhorse-1.1-t2v"
    resolution: Literal["480P", "720P", "1080P"] = "1080P"
    ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"] = "16:9"
    duration: int = Field(default=5, ge=3, le=15, strict=True)
    watermark: bool = False
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("提示词不能为空")
        chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in value)
        if chinese_count > 2500:
            raise ValueError("中文提示词不能超过 2500 字")
        return value

    @model_validator(mode="after")
    def validate_resolution(self) -> "VideoCreate":
        if self.model == "happyhorse-1.0-t2v" and self.resolution == "480P":
            raise ValueError("HappyHorse 1.0 不支持 480P")
        return self

    def provider_payload(self) -> dict[str, object]:
        return {
            "model": self.model,
            "input": {"prompt": self.prompt},
            "parameters": self.model_dump(
                exclude={"model", "prompt", "title", "cover_url"}, exclude_none=True
            ),
        }


class VideoEdit(VideoMetadata):
    model_config = ConfigDict(extra="forbid")

    model: Literal["happyhorse-1.0-video-edit"] = "happyhorse-1.0-video-edit"
    source_record_id: int = Field(gt=0, le=9223372036854775807)
    source_url: HttpUrl
    prompt: str = Field(min_length=1, max_length=5000)
    resolution: Literal["720P", "1080P"] = "1080P"
    audio_setting: Literal["auto", "origin"] = "auto"
    watermark: bool = False
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    duration: int = Field(ge=3, le=15, strict=True)
    ratio: str

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        return VideoCreate.validate_prompt(value)

    def provider_payload(self) -> dict[str, object]:
        return {
            "model": self.model,
            "input": {
                "prompt": self.prompt,
                "media": [{"type": "video", "url": str(self.source_url)}],
            },
            "parameters": self.model_dump(
                include={"resolution", "audio_setting", "watermark", "seed"}, exclude_none=True
            ),
        }


class ProviderOutput(BaseModel):
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    task_status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"]
    video_url: HttpUrl | None = None
    code: str | None = None


class ProviderResult(BaseModel):
    output: ProviderOutput
    request_id: str | None = None


class VideoTask(BaseModel):
    id: int
    user_id: int
    title: str | None = Field(default=None, description="用户指定或从生成描述提取的视频标题")
    cover_url: str | None = Field(
        default=None, description="视频和封面转存完成后为独立 JPEG 图片的七牛永久地址"
    )
    task_id: str | None
    status: VideoStatus
    storage_status: VideoStorageStatus
    aliyun_video_url: str | None
    qiniu_video_url: str | None
    qiniu_object_key: str | None
    error_code: str | None
    create_time: datetime
    update_time: datetime


class VideoListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=2147483647)
    page_size: int = Field(default=20, ge=1, le=100)
    status: VideoStatus | None = None
    storage_status: VideoStorageStatus | None = None


class VideoTaskPage(BaseModel):
    items: list[VideoTask]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


class VideoRecord(VideoTask):
    qiniu_cover_object_key: str | None = None
    idempotency_key: str
    request_hash: str
    request_payload: VideoCreate | VideoEdit
    provider_request_id: str | None
    create_by: int
    update_by: int
    is_delete: int
    next_poll_time: datetime
