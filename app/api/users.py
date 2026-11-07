from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from app.api.dependencies import principal
from app.schemas import ApiResponse, UserProfile, api_success
from app.services.users import (
    AvatarTooLargeError,
    AvatarUploadError,
    EmptyProfileUpdateError,
    InvalidAvatarError,
    UserNotFoundError,
    UserProfileService,
)

router = APIRouter(prefix="/users", tags=["users"])


def _service(request: Request) -> UserProfileService:
    service = getattr(request.app.state, "user_profile_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "User profiles are unavailable")
    return service


def _current_user_id(request: Request) -> int:
    identity = principal(request)
    if identity.admin or identity.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "An app user token is required")
    try:
        user_id = int(identity.user_id)
        if user_id <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid app user identity") from None
    return user_id


@router.get("/me", response_model=ApiResponse[UserProfile])
async def get_current_user(request: Request) -> ApiResponse[UserProfile]:
    try:
        user = await _service(request).get(_current_user_id(request))
    except UserNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from None
    return api_success(user)


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
    user_id = _current_user_id(request)
    if email is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Email cannot be changed")

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
