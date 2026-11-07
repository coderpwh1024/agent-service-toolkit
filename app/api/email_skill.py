"""User-bound email delivery with Redis replay protection."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.dependencies import principal
from app.schemas import ApiResponse, api_success
from app.schemas.email_skill import (
    EmailContent,
    EmailScheduleError,
    EmailSendRequest,
    EmailSendResult,
)
from app.services.skills.email import (
    EmailSkillRateLimited,
    EmailSkillUnavailable,
    EmailSkillUserUnavailable,
    get_email_skill_service,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/skills/email", tags=["skills"])


@router.post(
    "/send", response_model=ApiResponse[EmailSendResult], operation_id="send_current_user_email"
)
async def send_current_user_email(
    body: EmailSendRequest, request: Request
) -> ApiResponse[EmailSendResult]:
    identity = principal(request)
    if identity.admin or not identity.user_id:
        raise HTTPException(403, "Sign in with a user access token to send email")
    service = get_email_skill_service()
    if service is None:
        raise HTTPException(503, "Email skill is unavailable")
    try:
        result = await service.send(
            identity.user_id,
            str(body.request_id),
            EmailContent.model_validate(body.model_dump(exclude={"request_id", "send_at"})),
            send_at=body.send_at,
        )
    except EmailScheduleError:
        raise HTTPException(
            422, "Send time must include a timezone and be in the next 24 hours"
        ) from None
    except EmailSkillUserUnavailable:
        raise HTTPException(403, "A registered signed-in user is required") from None
    except EmailSkillRateLimited:
        raise HTTPException(429, "Daily email limit reached") from None
    except (EmailSkillUnavailable, OSError):
        logger.warning("Email skill infrastructure unavailable")
        raise HTTPException(
            503, "Email delivery is unavailable; do not retry automatically"
        ) from None
    return api_success(result)
