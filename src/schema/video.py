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


class VideoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=5000)
    model: Literal["happyhorse-1.1-t2v", "happyhorse-1.0-t2v"] = "happyhorse-1.1-t2v"
    resolution: Literal["480P", "720P", "1080P"] = "1080P"
    ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"] = "16:9"
    duration: int = Field(default=5, ge=3, le=15, strict=True)
    watermark: bool = True
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
            "parameters": self.model_dump(exclude={"model", "prompt"}, exclude_none=True),
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
    idempotency_key: str
    request_hash: str
    request_payload: VideoCreate
    provider_request_id: str | None
    create_by: int
    update_by: int
    is_delete: int
    next_poll_time: datetime
