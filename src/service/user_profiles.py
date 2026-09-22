"""Authenticated user profile updates and Qiniu avatar storage."""

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any, Protocol
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from qiniu import Auth, BucketManager, put_data

from core import settings
from schema import ApiResponse, UserProfile, api_success
from service.access import principal

logger = logging.getLogger(__name__)

_IMAGE_FORMATS: tuple[tuple[str, str, Callable[[bytes], bool]], ...] = (
    ("image/jpeg", "jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ("image/png", "png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ("image/gif", "gif", lambda data: data.startswith((b"GIF87a", b"GIF89a"))),
    (
        "image/webp",
        "webp",
        lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ),
)


class UserProfileRepositoryProtocol(Protocol):
    async def update_profile(
        self,
        user_id: int,
        nickname: str | None,
        image_url: str | None,
    ) -> UserProfile | None: ...


class AvatarStorageProtocol(Protocol):
    async def upload(
        self,
        user_id: int,
        data: bytes,
        mime_type: str,
        extension: str,
    ) -> tuple[str, str]: ...

    async def delete(self, object_key: str) -> None: ...


class InvalidAvatarError(ValueError):
    pass


class AvatarTooLargeError(InvalidAvatarError):
    pass


class AvatarUploadError(RuntimeError):
    pass


class UserNotFoundError(LookupError):
    pass


class EmptyProfileUpdateError(ValueError):
    pass


class QiniuAvatarStorage:
    def __init__(self, config: Any = settings):
        missing = [
            name
            for name in (
                "QINIU_ACCESS_KEY",
                "QINIU_SECRET_KEY",
                "QINIU_BUCKET_NAME",
                "QINIU_PUBLIC_BASE_URL",
            )
            if not getattr(config, name)
        ]
        if missing:
            raise ValueError("Missing Qiniu settings: " + ", ".join(missing))
        base_url = config.QINIU_PUBLIC_BASE_URL.rstrip("/")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("QINIU_PUBLIC_BASE_URL must be an HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("QINIU_PUBLIC_BASE_URL cannot contain a query or fragment")

        self.auth = Auth(
            config.QINIU_ACCESS_KEY.get_secret_value(),
            config.QINIU_SECRET_KEY.get_secret_value(),
        )
        self.bucket_name = config.QINIU_BUCKET_NAME
        self.public_base_url = base_url
        self.token_ttl_seconds = config.QINIU_UPLOAD_TOKEN_TTL_SECONDS
        self.bucket_manager = BucketManager(self.auth)

    async def upload(
        self,
        user_id: int,
        data: bytes,
        mime_type: str,
        extension: str,
    ) -> tuple[str, str]:
        object_key = f"users/{user_id}/avatars/{uuid4().hex}.{extension}"
        try:
            result, response = await asyncio.to_thread(
                self._upload,
                object_key,
                data,
                mime_type,
            )
        except Exception as exc:
            raise AvatarUploadError("Qiniu avatar upload failed") from exc
        if response.status_code != 200 or not isinstance(result, dict):
            raise AvatarUploadError("Qiniu avatar upload failed")
        if result.get("key") != object_key or not result.get("hash"):
            raise AvatarUploadError("Qiniu returned an invalid upload response")
        url = f"{self.public_base_url}/{quote(object_key, safe='/')}"
        return object_key, url

    def _upload(self, object_key: str, data: bytes, mime_type: str):
        token = self.auth.upload_token(
            self.bucket_name,
            object_key,
            self.token_ttl_seconds,
        )
        return put_data(
            token,
            object_key,
            data,
            mime_type=mime_type,
            check_crc=True,
        )

    async def delete(self, object_key: str) -> None:
        try:
            _, response = await asyncio.to_thread(
                self.bucket_manager.delete,
                self.bucket_name,
                object_key,
            )
        except Exception:
            logger.exception("Failed to clean up Qiniu object %s", object_key)
            return
        if response.status_code not in {200, 612}:
            logger.warning(
                "Qiniu did not confirm cleanup for object %s: status=%s",
                object_key,
                response.status_code,
            )


class UserProfileService:
    def __init__(
        self,
        users: UserProfileRepositoryProtocol,
        avatars: AvatarStorageProtocol,
        max_avatar_bytes: int,
    ):
        self.users = users
        self.avatars = avatars
        self.max_avatar_bytes = max_avatar_bytes

    async def update(
        self,
        user_id: int,
        nickname: str | None,
        image: UploadFile | None,
    ) -> UserProfile:
        normalized_nickname = _normalize_nickname(nickname)
        if normalized_nickname is None and image is None:
            raise EmptyProfileUpdateError

        object_key = None
        image_url = None
        if image is not None:
            data, mime_type, extension = await _read_avatar(image, self.max_avatar_bytes)
            object_key, image_url = await self.avatars.upload(
                user_id,
                data,
                mime_type,
                extension,
            )

        try:
            user = await self.users.update_profile(user_id, normalized_nickname, image_url)
        except Exception:
            if object_key is not None:
                await self.avatars.delete(object_key)
            raise
        if user is None:
            if object_key is not None:
                await self.avatars.delete(object_key)
            raise UserNotFoundError
        return user


def _normalize_nickname(nickname: str | None) -> str | None:
    if nickname is None:
        return None
    normalized = nickname.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("Nickname must contain between 1 and 64 characters")
    return normalized


async def _read_avatar(image: UploadFile, max_bytes: int) -> tuple[bytes, str, str]:
    data = await image.read(max_bytes + 1)
    if not data:
        raise InvalidAvatarError("Avatar file is empty")
    if len(data) > max_bytes:
        raise AvatarTooLargeError("Avatar file is too large")
    detected = next(
        (
            (mime_type, extension)
            for mime_type, extension, matches in _IMAGE_FORMATS
            if matches(data)
        ),
        None,
    )
    if detected is None:
        raise InvalidAvatarError("Unsupported avatar image format")
    mime_type, extension = detected
    if image.content_type not in {None, "application/octet-stream", mime_type}:
        raise InvalidAvatarError("Avatar content type does not match its file data")
    return data, mime_type, extension


router = APIRouter(prefix="/users", tags=["users"])


def _service(request: Request) -> UserProfileService:
    service = getattr(request.app.state, "user_profile_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "User profiles are unavailable")
    return service


@router.patch("/me", response_model=ApiResponse[UserProfile])
async def update_current_user(
    request: Request,
    nickname: Annotated[str | None, Form(max_length=64)] = None,
    image: Annotated[UploadFile | None, File()] = None,
    email: Annotated[str | None, Form()] = None,
) -> ApiResponse[UserProfile]:
    try:
        return await _update_current_user(request, nickname, image, email)
    finally:
        if image is not None:
            await image.close()


async def _update_current_user(
    request: Request,
    nickname: str | None,
    image: UploadFile | None,
    email: str | None,
) -> ApiResponse[UserProfile]:
    identity = principal(request)
    if identity.admin or identity.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "An app user token is required")
    if email is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Email cannot be changed")
    try:
        user_id = int(identity.user_id)
        if user_id <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid app user identity") from None

    try:
        user = await _service(request).update(user_id, nickname, image)
    except AvatarTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from None
    except InvalidAvatarError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    except AvatarUploadError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Avatar upload failed") from None
    except EmptyProfileUpdateError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Nickname or image is required",
        ) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except UserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from None
    return api_success(user)
