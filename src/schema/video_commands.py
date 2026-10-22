"""Structured video commands and user-bound conversation context."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema.video import VideoCreate


class VideoIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["none", "create", "draft", "query", "cancel", "unsupported"]
    generation: VideoCreate | None = None
    record_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    latest: bool = False


class VideoDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    last_record_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    draft_request: str | None = Field(default=None, max_length=6000)
    draft_expires_at: float = 0
