"""Structured video skill decisions and user-bound dialogue state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema.video import VideoListQuery


class VideoGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=6000)
    model: str | None = None
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = Field(default=None, strict=True)
    seed: int | None = Field(default=None, strict=True)


class VideoEditDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=6000)
    resolution: str | None = None
    audio_setting: str | None = None
    seed: int | None = Field(default=None, strict=True)


class VideoSkillIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "none", "create", "edit", "detail", "list", "count", "draft", "cancel", "unsupported"
    ]
    authorized: bool = False
    reset_draft: bool = False
    generation: VideoGenerationDraft | None = None
    editing: VideoEditDraft | None = None
    record_id: int | None = Field(default=None, gt=0, le=9223372036854775807, strict=True)
    selection: int | None = Field(default=None, gt=0, strict=True)
    latest: bool = False
    query: VideoListQuery | None = None
    next_page: bool = False
    reason: (
        Literal[
            "duration",
            "source",
            "editing",
            "voice_clone",
            "playback",
            "selection",
            "parameters",
            "other",
        ]
        | None
    ) = None


class VideoSkillDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    last_record_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    draft_request: str | None = Field(default=None, max_length=12000)
    draft_expires_at: float = 0
    draft_action: Literal["create", "edit"] | None = None
    draft_generation: VideoGenerationDraft | None = None
    draft_editing: VideoEditDraft | None = None
    last_query: VideoListQuery | None = None
    listed_ids: list[int] = Field(default_factory=list, max_length=100)
