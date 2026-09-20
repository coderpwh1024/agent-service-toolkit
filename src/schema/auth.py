"""Schemas for passwordless email authentication."""

from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator


class EmailCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    image_url: AnyHttpUrl | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("nickname", mode="before")
    @classmethod
    def normalize_nickname(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class EmailCodeVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    code: str = Field(pattern=r"^[0-9]{6}$")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class EmailCodeAccepted(BaseModel):
    expires_in_seconds: int = 180


class UserProfile(BaseModel):
    id: int
    nickname: str
    email: EmailStr
    image_url: AnyHttpUrl | None = None


class EmailAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int
    is_new_user: bool
    user: UserProfile
