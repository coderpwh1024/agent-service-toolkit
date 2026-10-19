"""Validated inputs for the current-user email skill."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class EmailScheduleError(ValueError):
    pass


def validate_send_at(send_at: datetime, now: datetime) -> datetime:
    if send_at.tzinfo is None or send_at.utcoffset() is None:
        raise EmailScheduleError("Send time must include a timezone")
    target = send_at.astimezone(UTC)
    if not now < target <= now + timedelta(hours=24):
        raise EmailScheduleError("Send time must be in the next 24 hours")
    return target


class EmailContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Email subject cannot contain control characters")
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Email body cannot contain NUL")
        return value


class EmailSendRequest(EmailContent):
    request_id: UUID
    send_at: AwareDatetime | None = None


class EmailSendResult(BaseModel):
    status: Literal["sent", "duplicate", "unknown", "scheduled"]
    send_at: AwareDatetime | None = None


class EmailIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["none", "send", "draft", "cancel"]
    recipient: Literal["current_user", "other"] = "current_user"
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    timing: Literal["immediate", "scheduled", "unclear"] = "immediate"
    send_at: AwareDatetime | None = None
    clear_send_at: bool = False


class EmailDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    subject: str = ""
    body: str = ""
    timing: Literal["immediate", "scheduled", "unclear"] = "immediate"
    send_at: AwareDatetime | None = None
    expires_at: float = 0


class ScheduledEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    content: EmailContent
    send_at: AwareDatetime
