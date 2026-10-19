"""Validated inputs for the current-user email skill."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class EmailSendResult(BaseModel):
    status: Literal["sent", "duplicate", "unknown"]


class EmailIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["none", "send", "draft", "cancel"]
    recipient: Literal["current_user", "other"] = "current_user"
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)


class EmailDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    subject: str = ""
    body: str = ""
    expires_at: float = 0
