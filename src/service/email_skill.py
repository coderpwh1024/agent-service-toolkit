"""User-bound email delivery with Redis replay protection."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, FastAPI, HTTPException, Request

from core import settings
from schema import ApiResponse, api_success
from schema.email_skill import EmailContent, EmailScheduleError, EmailSendRequest, EmailSendResult
from service.access import principal
from skills.email import (
    EmailSkillRateLimited,
    EmailSkillService,
    EmailSkillUnavailable,
    EmailSkillUserUnavailable,
    get_email_skill_service,
    set_email_skill_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills/email", tags=["skills"])


@asynccontextmanager
async def email_skill_lifespan(app: FastAPI) -> AsyncIterator[None]:
    set_email_skill_service(None)
    if not settings.EMAIL_SKILL_ENABLED:
        yield
        return
    if not settings.NACOS_ENABLED or settings.email_skill is None:
        raise ValueError("Email skill must be loaded from Nacos before startup")
    auth = getattr(app.state, "email_auth_service", None)
    if auth is None:
        raise ValueError("EMAIL_AUTH_ENABLED is required for the email skill")
    if not settings.SMTP_USE_SSL and not settings.SMTP_USE_TLS:
        raise ValueError("The email skill requires an encrypted SMTP transport")
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise ValueError("The email skill requires SMTP credentials")
    service = EmailSkillService(auth.users, auth.sender, auth.codes.redis)
    set_email_skill_service(service)
    scheduler = asyncio.create_task(service.run_scheduler(), name="email-scheduler")
    logger.info("Email skill runtime started")
    try:
        yield
    finally:
        set_email_skill_service(None)
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler
        logger.info("Email skill runtime stopped")


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
