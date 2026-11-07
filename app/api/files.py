"""Authenticated file endpoints and storage lifecycle."""

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Path, Request, UploadFile
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout

from app.api.dependencies import principal
from app.schemas import ApiResponse, api_success
from app.schemas.files import UploadedFile
from app.services.files import FileService, FileTooLargeError, FileUploadError

router = APIRouter(prefix="/files", tags=["files"])


def _user_id(request: Request) -> int:
    identity = principal(request)
    if identity.admin or identity.user_id is None:
        raise HTTPException(403, "File endpoints require an app user token")
    if not identity.user_id.isascii() or not identity.user_id.isdecimal():
        raise HTTPException(401, "Invalid app user identity")
    user_id = int(identity.user_id)
    if not 0 < user_id <= 9223372036854775807:
        raise HTTPException(401, "Invalid app user identity")
    return user_id


def _service(request: Request) -> FileService:
    service = getattr(request.app.state, "file_service", None)
    if service is None:
        raise HTTPException(503, "File uploads are unavailable")
    return service


@router.post("", response_model=ApiResponse[UploadedFile], status_code=201)
async def upload_file(
    request: Request, file: Annotated[UploadFile, File()]
) -> ApiResponse[UploadedFile]:
    try:
        user_id = _user_id(request)
        return api_success(await _service(request).upload(user_id, file))
    except FileTooLargeError as exc:
        raise HTTPException(413, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except FileUploadError:
        raise HTTPException(502, "File upload failed; retry later") from None
    except (PostgresError, PoolTimeout):
        raise HTTPException(503, "File metadata storage is unavailable") from None
    finally:
        await file.close()


@router.get("/{file_id}/url", response_model=ApiResponse[UploadedFile])
async def get_file_url(
    file_id: Annotated[int, Path(gt=0, le=9223372036854775807)], request: Request
) -> ApiResponse[UploadedFile]:
    user_id = _user_id(request)
    try:
        return api_success(await _service(request).get(file_id, user_id))
    except LookupError:
        raise HTTPException(404, "File not found") from None
    except (PostgresError, PoolTimeout):
        raise HTTPException(503, "File metadata storage is unavailable") from None
