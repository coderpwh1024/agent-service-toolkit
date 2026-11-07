from fastapi import APIRouter, HTTPException, Request, status
from redis.exceptions import RedisError

from app.repositories.verification_codes import EmailRateLimitError, InvalidVerificationCodeError
from app.schemas.api import ApiResponse, api_success
from app.schemas.auth import EmailAuthResponse, EmailCodeAccepted, EmailCodeRequest, EmailCodeVerify
from app.services.email_auth import EmailAuthService
from app.services.skills.email import EmailDeliveryError

router = APIRouter(prefix="/auth/email", tags=["authentication"])


def _service(request: Request) -> EmailAuthService:
    service = getattr(request.app.state, "email_auth_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email authentication is disabled")
    return service


@router.post("/code", response_model=ApiResponse[EmailCodeAccepted])
async def request_email_code(
    body: EmailCodeRequest, request: Request
) -> ApiResponse[EmailCodeAccepted]:
    try:
        return api_success(await _service(request).request_code(body))
    except EmailRateLimitError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "This email has reached the 24-hour verification-code limit",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except EmailDeliveryError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Verification email could not be sent"
        ) from None
    except RedisError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Verification service is unavailable"
        ) from None


@router.post("/verify", response_model=ApiResponse[EmailAuthResponse])
async def verify_email_code(
    body: EmailCodeVerify, request: Request
) -> ApiResponse[EmailAuthResponse]:
    try:
        return api_success(await _service(request).verify(body))
    except InvalidVerificationCodeError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired verification code"
        ) from None
    except RedisError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Verification service is unavailable"
        ) from None
