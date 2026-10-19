"""Email skill delivery and replay protection independent of HTTP transport."""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable
from typing import Any, Protocol

from psycopg import Error as PostgresError
from redis.exceptions import RedisError

from schema import UserProfile
from schema.email_skill import EmailContent, EmailSendResult

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    pass


class EmailRedis(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...


_RESERVE = """
local existing = redis.call('GET', KEYS[1])
if existing then return existing end
local count = tonumber(redis.call('GET', KEYS[2]) or '0')
if count >= 10 then return 'limited' end
redis.call('SET', KEYS[1], 'unknown', 'EX', 86400)
redis.call('INCR', KEYS[2])
if count == 0 then redis.call('EXPIRE', KEYS[2], 86400) end
return 'reserved'
"""
_COMPLETE = "return redis.call('SET', KEYS[1], 'sent', 'EX', 86400)"


class EmailSkillUnavailable(RuntimeError):
    pass


class EmailSkillUserUnavailable(EmailSkillUnavailable):
    pass


class EmailSkillRateLimited(RuntimeError):
    pass


class EmailUsers(Protocol):
    async def get_by_id(self, user_id: int) -> UserProfile | None: ...


class EmailSender(Protocol):
    async def send_email(self, email: str, subject: str, body: str) -> None: ...


class EmailSkillService:
    def __init__(self, users: EmailUsers, sender: EmailSender, redis: EmailRedis) -> None:
        self.users = users
        self.sender = sender
        self.redis = redis

    async def send(self, user_id: str, request_id: str, content: EmailContent) -> EmailSendResult:
        if not user_id.isascii() or not user_id.isdigit() or not 0 < int(user_id) < 2**63:
            raise EmailSkillUserUnavailable("A registered signed-in user is required")
        try:
            user = await self.users.get_by_id(int(user_id))
        except (PostgresError, OSError) as exc:
            logger.warning("Email skill user lookup unavailable error_type=%s", type(exc).__name__)
            raise EmailSkillUnavailable("User lookup is unavailable") from None
        if user is None:
            raise EmailSkillUserUnavailable("The signed-in user is unavailable")
        recipient = str(user.email)
        key_digest = hashlib.sha256(f"{user_id}:{request_id}".encode()).hexdigest()
        key = f"email_skill:request:{key_digest}"
        trace = key_digest[:12]
        try:
            state = await self.redis.eval(_RESERVE, 2, key, f"email_skill:limit:{user_id}")
        except (RedisError, OSError) as exc:
            logger.warning(
                "Email skill reservation unavailable trace=%s error_type=%s",
                trace,
                type(exc).__name__,
            )
            raise EmailSkillUnavailable("Email replay protection is unavailable") from None
        if state == "limited":
            logger.warning("Email skill rate limited trace=%s", trace)
            raise EmailSkillRateLimited("Daily email limit reached")
        if state != "reserved":
            logger.info("Email skill replay blocked trace=%s status=%s", trace, state)
            return EmailSendResult(status="duplicate" if state == "sent" else "unknown")
        logger.info("Email skill sending trace=%s", trace)
        try:
            await self.sender.send_email(recipient, content.subject, content.body)
            await self.redis.eval(_COMPLETE, 1, key)
        except asyncio.CancelledError:
            logger.warning("Email skill cancelled; delivery may have completed trace=%s", trace)
            raise
        except (EmailDeliveryError, RedisError, OSError) as exc:
            logger.warning(
                "Email skill delivery uncertain trace=%s error_type=%s", trace, type(exc).__name__
            )
            return EmailSendResult(status="unknown")
        logger.info("Email skill sent trace=%s", trace)
        return EmailSendResult(status="sent")


_service: EmailSkillService | None = None


def get_email_skill_service() -> EmailSkillService | None:
    return _service


def set_email_skill_service(service: EmailSkillService | None) -> None:
    global _service
    _service = service
