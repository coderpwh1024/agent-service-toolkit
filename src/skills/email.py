"""Email skill delivery and replay protection independent of HTTP transport."""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import ceil
from typing import Any, Protocol

from psycopg import Error as PostgresError
from pydantic import ValidationError
from redis.exceptions import RedisError

from schema import UserProfile
from schema.email_skill import EmailContent, EmailSendResult, ScheduledEmail, validate_send_at

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
_QUEUE = "email_skill:scheduled"
_SCHEDULE = """
local existing = redis.call('GET', KEYS[1])
if existing then return existing end
local count = tonumber(redis.call('GET', KEYS[2]) or '0')
if count >= 10 then return 'limited' end
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5])
redis.call('SET', KEYS[4], ARGV[3], 'EX', ARGV[5])
redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
redis.call('INCR', KEYS[2])
if count == 0 then redis.call('EXPIRE', KEYS[2], 86400) end
return 'reserved'
"""
_PEEK = """
local jobs = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #jobs == 0 then return {} end
local payload = redis.call('GET', ARGV[2] .. jobs[1])
if not payload then
    redis.call('ZREM', KEYS[1], jobs[1])
    return {}
end
return {jobs[1], payload}
"""
_CLAIM = """
local due = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not due or tonumber(due) > tonumber(ARGV[2]) then return 0 end
if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
redis.call('SET', KEYS[3], 'unknown', 'EX', 86400)
redis.call('DEL', KEYS[2])
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    def __init__(
        self,
        users: EmailUsers,
        sender: EmailSender,
        redis: EmailRedis,
        *,
        clock: Callable[[], datetime] = utc_now,
        queue_key: str = _QUEUE,
    ) -> None:
        self.users = users
        self.sender = sender
        self.redis = redis
        self.clock = clock
        self.queue_key = queue_key
        self.job_prefix = queue_key + ":job:"

    async def _user(self, user_id: str) -> UserProfile:
        if not user_id.isascii() or not user_id.isdigit() or not 0 < int(user_id) < 2**63:
            raise EmailSkillUserUnavailable("A registered signed-in user is required")
        try:
            user = await self.users.get_by_id(int(user_id))
        except (PostgresError, OSError) as exc:
            logger.warning("Email skill user lookup unavailable error_type=%s", type(exc).__name__)
            raise EmailSkillUnavailable("User lookup is unavailable") from None
        if user is None:
            raise EmailSkillUserUnavailable("The signed-in user is unavailable")
        return user

    @staticmethod
    def _replay_result(state: str) -> EmailSendResult:
        if state.startswith("scheduled:"):
            return EmailSendResult(status="scheduled", send_at=datetime.fromisoformat(state[10:]))
        return EmailSendResult(status="duplicate" if state == "sent" else "unknown")

    async def send(
        self,
        user_id: str,
        request_id: str,
        content: EmailContent,
        *,
        send_at: datetime | None = None,
    ) -> EmailSendResult:
        if send_at is not None:
            send_at = validate_send_at(send_at, self.clock())
        user = await self._user(user_id)
        key_digest = hashlib.sha256(f"{user_id}:{request_id}".encode()).hexdigest()
        key = f"email_skill:request:{key_digest}"
        trace = key_digest[:12]
        try:
            if send_at is None:
                state = await self.redis.eval(_RESERVE, 2, key, f"email_skill:limit:{user_id}")
            else:
                now = self.clock()
                send_at = validate_send_at(send_at, now)
                job = ScheduledEmail(user_id=user_id, content=content, send_at=send_at)
                state = await self.redis.eval(
                    _SCHEDULE,
                    4,
                    key,
                    f"email_skill:limit:{user_id}",
                    self.queue_key,
                    self.job_prefix + key_digest,
                    key_digest,
                    send_at.timestamp(),
                    job.model_dump_json(),
                    "scheduled:" + send_at.isoformat(),
                    ceil((send_at - now).total_seconds()) + 86400,
                )
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
            return self._replay_result(state)
        if send_at is not None:
            logger.info("Email skill scheduled trace=%s send_at=%s", trace, send_at.isoformat())
            return EmailSendResult(status="scheduled", send_at=send_at)
        return await self._deliver(str(user.email), content, key, trace)

    async def _deliver(
        self,
        recipient: str,
        content: EmailContent,
        key: str,
        trace: str,
    ) -> EmailSendResult:
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

    async def process_due(self) -> int:
        processed = 0
        for _ in range(10):
            item = await self.redis.eval(
                _PEEK, 1, self.queue_key, self.clock().timestamp(), self.job_prefix
            )
            if not item:
                break
            digest, payload = item
            trace = digest[:12]
            recipient: str | None = None
            job: ScheduledEmail | None = None
            try:
                job = ScheduledEmail.model_validate_json(payload)
                recipient = str((await self._user(job.user_id)).email)
            except (ValidationError, EmailSkillUserUnavailable) as exc:
                logger.warning(
                    "Scheduled email discarded trace=%s error_type=%s", trace, type(exc).__name__
                )
            claimed = await self.redis.eval(
                _CLAIM,
                3,
                self.queue_key,
                self.job_prefix + digest,
                "email_skill:request:" + digest,
                digest,
                self.clock().timestamp(),
                payload,
            )
            if not claimed:
                continue
            processed += 1
            if job is not None and recipient is not None:
                logger.info("Scheduled email due trace=%s", trace)
                await self._deliver(recipient, job.content, "email_skill:request:" + digest, trace)
        return processed

    async def run_scheduler(self) -> None:
        logger.info("Email scheduler started")
        try:
            while True:
                try:
                    await self.process_due()
                except Exception as exc:
                    logger.warning(
                        "Email scheduler iteration failed error_type=%s", type(exc).__name__
                    )
                await asyncio.sleep(1)
        finally:
            logger.info("Email scheduler stopped")


_service: EmailSkillService | None = None


def get_email_skill_service() -> EmailSkillService | None:
    return _service


def set_email_skill_service(service: EmailSkillService | None) -> None:
    global _service
    _service = service
