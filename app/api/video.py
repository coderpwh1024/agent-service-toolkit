"""Authenticated video endpoints and cancellable service lifecycle."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request

from app.api.dependencies import principal
from app.schemas import ApiResponse, api_success
from app.schemas.video import (
    VideoCreate,
    VideoListQuery,
    VideoMetadataUpdate,
    VideoTask,
    VideoTaskPage,
)
from app.services.video.service import VideoBusyError, VideoConflictError, VideoService

router = APIRouter(prefix="/videos", tags=["videos"])


def _user_id(request: Request) -> int:
    identity = principal(request)
    if identity.admin or identity.user_id is None:
        raise HTTPException(403, "视频接口需要应用用户令牌")
    if not identity.user_id.isascii() or not identity.user_id.isdecimal():
        raise HTTPException(401, "应用用户 ID 必须是正整数")
    user_id = int(identity.user_id)
    if not 0 < user_id <= 9223372036854775807:
        raise HTTPException(401, "应用用户 ID 超出范围")
    return user_id


def _service(request: Request) -> VideoService:
    service = getattr(request.app.state, "video_service", None)
    if service is None:
        raise HTTPException(503, "视频生成功能未启用")
    return service


@router.post("", response_model=ApiResponse[VideoTask], status_code=202)
async def create_video(
    body: VideoCreate,
    request: Request,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    ],
) -> ApiResponse[VideoTask]:
    user_id = _user_id(request)
    try:
        return api_success(await _service(request).create(user_id, idempotency_key, body))
    except (VideoConflictError, VideoBusyError) as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("", response_model=ApiResponse[VideoTaskPage])
async def list_videos(
    request: Request, query: Annotated[VideoListQuery, Query()]
) -> ApiResponse[VideoTaskPage]:
    user_id = _user_id(request)
    return api_success(await _service(request).list_tasks(user_id, query))


@router.get("/{record_id}", response_model=ApiResponse[VideoTask])
async def query_video(
    record_id: Annotated[int, Path(gt=0, le=9223372036854775807)], request: Request
) -> ApiResponse[VideoTask]:
    user_id = _user_id(request)
    try:
        return api_success(await _service(request).query(record_id, user_id))
    except LookupError:
        raise HTTPException(404, "视频任务不存在") from None
    except VideoBusyError as exc:
        raise HTTPException(409, str(exc)) from None


@router.patch("/{record_id}", response_model=ApiResponse[VideoTask])
async def update_video_metadata(
    record_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    body: VideoMetadataUpdate,
    request: Request,
) -> ApiResponse[VideoTask]:
    user_id = _user_id(request)
    try:
        return api_success(await _service(request).update_metadata(record_id, user_id, body))
    except LookupError:
        raise HTTPException(404, "视频任务不存在") from None
    except (VideoBusyError, VideoConflictError) as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/{record_id}", response_model=ApiResponse[dict[str, bool]])
async def delete_video(
    record_id: Annotated[int, Path(gt=0, le=9223372036854775807)], request: Request
) -> ApiResponse[dict[str, bool]]:
    user_id = _user_id(request)
    try:
        await _service(request).delete(record_id, user_id)
    except LookupError:
        raise HTTPException(404, "视频任务不存在") from None
    except VideoBusyError as exc:
        raise HTTPException(409, str(exc)) from None
    return api_success({"deleted": True})
