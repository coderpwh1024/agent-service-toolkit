"""Public video contracts and provider task states."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

VideoGenerationModel = Literal[
    "happyhorse-1.1-t2v", "happyhorse-1.0-t2v", "wan3.0-video", "wan3.0-video-prime"
]
VideoModel = Literal[
    "happyhorse-1.1-t2v",
    "happyhorse-1.0-t2v",
    "happyhorse-1.0-video-edit",
    "wan3.0-video",
    "wan3.0-video-prime",
]


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


class VideoMetadataUpdate(VideoMetadata):
    model_config = ConfigDict(extra="forbid")
    cover_url: HttpUrl | None = Field(
        default=None,
        max_length=2048,
        description="已转存视频的新展示封面 HTTP(S) 地址，不修改原始生成请求",
    )

    @model_validator(mode="after")
    def require_change(self) -> "VideoMetadataUpdate":
        if self.title is None and self.cover_url is None:
            raise ValueError("至少提供标题或封面地址")
        return self


class VideoCreate(VideoMetadata):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        default="",
        max_length=20000,
        description="HappyHorse 必填；万相有首帧时可省略。HappyHorse 最多 2500 中文字、总计 5000 字符；万相最多 20000 字符",
    )
    model: VideoGenerationModel = "happyhorse-1.1-t2v"
    resolution: Literal["480P", "720P", "1080P"] = "1080P"
    ratio: Literal[
        "16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9", "adaptive"
    ] = "16:9"
    duration: int = Field(
        default=5,
        ge=-1,
        le=30,
        strict=True,
        description="HappyHorse：3–15 秒；万相 3.0：2–30 秒或 -1 智能时长",
    )
    watermark: bool = False
    seed: int | None = Field(default=None, ge=-1, le=2147483647, strict=True)
    first_frame_url: HttpUrl | None = Field(
        default=None,
        max_length=2048,
        description="仅万相：生成首帧的 HTTP(S) 图片 URL，与展示封面无关",
    )
    last_frame_url: HttpUrl | None = Field(
        default=None,
        max_length=2048,
        description="仅万相：生成尾帧的 HTTP(S) 图片 URL，必须同时提供首帧",
    )
    audio: bool | None = Field(
        default=None, strict=True, description="仅万相：输出是否含音轨；省略时采用供应商默认 true"
    )
    prompt_extend: bool | None = Field(
        default=None,
        strict=True,
        description="仅万相：是否智能改写提示词；省略时采用供应商默认 true",
    )

    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("提示词不能为空")
        chinese_count = sum("\u4e00" <= char <= "\u9fff" for char in value)
        if chinese_count > 2500:
            raise ValueError("中文提示词不能超过 2500 字")
        if len(value) > 5000:
            raise ValueError("HappyHorse 提示词不能超过 5000 字符")
        return value

    @model_validator(mode="after")
    def validate_resolution(self) -> "VideoCreate":
        if self.model.startswith("wan3.0-"):
            if not self.prompt.strip() and self.first_frame_url is None:
                raise ValueError("万相 3.0 需要非空 prompt 或 first_frame_url")
            if self.last_frame_url is not None and self.first_frame_url is None:
                raise ValueError("last_frame_url 必须与 first_frame_url 一起提供")
            if self.duration != -1 and not 2 <= self.duration <= 30:
                raise ValueError("万相 3.0 duration 必须为 2 至 30 秒整数或 -1（智能时长）")
            if self.ratio not in {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
                raise ValueError("万相 3.0 不支持此 ratio")
            return self
        self.validate_prompt(self.prompt)
        if not 3 <= self.duration <= 15:
            raise ValueError("HappyHorse duration 必须为 3 至 15 秒整数")
        if self.seed is not None and self.seed < 0:
            raise ValueError("HappyHorse seed 必须为 0 至 2147483647")
        if self.ratio == "adaptive":
            raise ValueError("HappyHorse 不支持 adaptive ratio")
        if any(
            value is not None
            for value in (self.first_frame_url, self.last_frame_url, self.audio, self.prompt_extend)
        ):
            raise ValueError("first_frame_url、last_frame_url、audio、prompt_extend 仅支持万相 3.0")
        if self.model == "happyhorse-1.0-t2v" and self.resolution == "480P":
            raise ValueError("HappyHorse 1.0 不支持 480P")
        return self

    def provider_payload(self) -> dict[str, object]:
        inputs: dict[str, object] = {"prompt": self.prompt}
        media = [
            {"type": kind, "url": str(url)}
            for kind, url in (
                ("first_frame", self.first_frame_url),
                ("last_frame", self.last_frame_url),
            )
            if url is not None
        ]
        if media:
            inputs["media"] = media
            if not self.prompt.strip():
                del inputs["prompt"]
        return {
            "model": self.model,
            "input": inputs,
            "parameters": self.model_dump(
                exclude={
                    "model",
                    "prompt",
                    "title",
                    "cover_url",
                    "first_frame_url",
                    "last_frame_url",
                },
                exclude_none=True,
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
    model: VideoModel = "happyhorse-1.1-t2v"
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    title: str | None = Field(default=None, description="用户指定或从生成描述提取的视频标题")
    cover_url: str | None = Field(
        default=None, description="视频的展示封面地址；转存完成后可由用户替换"
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
    model: VideoModel | None = None
    q: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="标题或生成/编辑描述关键词；忽略大小写，空白分隔的关键词须全部匹配",
    )

    @field_validator("q")
    @classmethod
    def validate_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("查询关键词不能为空")
        return " ".join(value.split())


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
