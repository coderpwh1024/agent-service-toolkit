from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.dependencies import principal
from app.core import settings
from app.schemas import ApiResponse, api_success
from app.schemas.auth import TokenInput
from app.services.auth import create_access_token

router = APIRouter(prefix="/auth")


@router.post("/token", response_model=ApiResponse[dict[str, Any]])
async def issue_token(body: TokenInput, request: Request) -> ApiResponse[dict[str, Any]]:
    if not settings.AUTH_SECRET or not settings.APP_TOKEN_SECRET:
        raise HTTPException(503, "AUTH_SECRET and APP_TOKEN_SECRET must be configured")
    if not principal(request).admin:
        raise HTTPException(403, "Only a trusted service can provision app tokens")
    token, expires_at = create_access_token(body.user_id)
    return api_success({"access_token": token, "token_type": "bearer", "expires_at": expires_at})
