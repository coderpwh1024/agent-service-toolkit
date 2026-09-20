"""Passwordless email login and registration backed by PostgreSQL and Redis."""

import asyncio
import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Any, LiteralString, Protocol, cast

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core import settings
from memory.postgres import get_postgres_connection_string
from schema.auth import (
    EmailAuthResponse,
    EmailCodeAccepted,
    EmailCodeRequest,
    EmailCodeVerify,
    UserProfile,
)
from service.access import create_access_token

VERIFICATION_CODE_TTL_SECONDS = 180
EMAIL_SEND_LIMIT = 6
EMAIL_SEND_WINDOW_SECONDS = 86_400
MAX_VERIFICATION_ATTEMPTS = 5
USER_SCHEMA_PATH = Path(__file__).with_name("sql") / "create_app_users.sql"

_RESERVE_CODE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return {0, redis.call('TTL', KEYS[1])}
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
redis.call('HSET', KEYS[2],
    'digest', ARGV[3],
    'profile', ARGV[4],
    'attempts', 0)
redis.call('EXPIRE', KEYS[2], ARGV[5])
return {current, redis.call('TTL', KEYS[1])}
"""

_CONSUME_CODE_SCRIPT = """
local stored = redis.call('HGET', KEYS[1], 'digest')
if not stored then
    return {0, false}
end
if stored == ARGV[1] then
    local profile = redis.call('HGET', KEYS[1], 'profile')
    redis.call('DEL', KEYS[1])
    return {1, profile}
end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
if attempts >= tonumber(ARGV[2]) then
    redis.call('DEL', KEYS[1])
end
return {-1, false}
"""

_DISCARD_CODE_SCRIPT = """
if redis.call('HGET', KEYS[1], 'digest') == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class EmailRateLimitError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Email verification send limit reached")
        self.retry_after = retry_after


class InvalidVerificationCodeError(Exception):
    pass


class EmailDeliveryError(Exception):
    pass


class UserRepositoryProtocol(Protocol):
    async def get_by_email(self, email: str) -> UserProfile | None: ...

    async def create_or_get(
        self, email: str, nickname: str, image_url: str | None
    ) -> tuple[UserProfile, bool]: ...


class VerificationCodeStoreProtocol(Protocol):
    async def reserve(
        self, email: str, digest: str, registration_profile: dict[str, str | None]
    ) -> None: ...

    async def consume(self, email: str, digest: str) -> dict[str, str | None]: ...

    async def discard(self, email: str, digest: str) -> None: ...

    async def ping(self) -> bool: ...


class EmailSenderProtocol(Protocol):
    async def send_code(self, email: str, code: str) -> None: ...


class AsyncRedisProtocol(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...

    def ping(self) -> Awaitable[bool]: ...

    def aclose(self) -> Awaitable[None]: ...


class UserRepository:
    def __init__(self, conninfo: str, service_account_id: int):
        self.service_account_id = service_account_id
        self.pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
            conninfo,
            min_size=settings.POSTGRES_MIN_CONNECTIONS_PER_POOL,
            max_size=settings.POSTGRES_MAX_CONNECTIONS_PER_POOL,
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "application_name": settings.POSTGRES_APPLICATION_NAME + "-auth",
            },
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)
        schema_sql = await asyncio.to_thread(USER_SCHEMA_PATH.read_text, encoding="utf-8")
        async with self.pool.connection() as conn:
            await conn.execute(SQL(cast(LiteralString, schema_sql)))

    async def close(self) -> None:
        await self.pool.close()

    async def get_by_email(self, email: str) -> UserProfile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, nickname, email, image_url
                    FROM app_users
                    WHERE email = %s AND is_delete = 0
                    """,
                    (email,),
                )
            ).fetchone()
        return UserProfile.model_validate(row) if row else None

    async def create_or_get(
        self, email: str, nickname: str, image_url: str | None
    ) -> tuple[UserProfile, bool]:
        existing = await self.get_by_email(email)
        if existing is not None:
            return existing, False
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO app_users (
                        nickname, email, image_url, create_by, update_by
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id, nickname, email, image_url
                    """,
                    (
                        nickname,
                        email,
                        image_url,
                        self.service_account_id,
                        self.service_account_id,
                    ),
                )
            ).fetchone()
        if row:
            return UserProfile.model_validate(row), True
        existing = await self.get_by_email(email)
        if existing is None:
            raise RuntimeError("User registration conflict could not be resolved")
        return existing, False


class VerificationCodeStore:
    def __init__(self, redis: AsyncRedisProtocol):
        self.redis = redis

    @staticmethod
    def _email_key(email: str) -> str:
        return hashlib.sha256(email.encode()).hexdigest()

    async def reserve(
        self, email: str, digest: str, registration_profile: dict[str, str | None]
    ) -> None:
        email_key = self._email_key(email)
        result = await self.redis.eval(
            _RESERVE_CODE_SCRIPT,
            2,
            f"email_auth:send:{email_key}",
            f"email_auth:code:{email_key}",
            EMAIL_SEND_LIMIT,
            EMAIL_SEND_WINDOW_SECONDS,
            digest,
            json.dumps(registration_profile, ensure_ascii=False),
            VERIFICATION_CODE_TTL_SECONDS,
        )
        if int(result[0]) == 0:
            raise EmailRateLimitError(max(int(result[1]), 1))

    async def consume(self, email: str, digest: str) -> dict[str, str | None]:
        result = await self.redis.eval(
            _CONSUME_CODE_SCRIPT,
            1,
            f"email_auth:code:{self._email_key(email)}",
            digest,
            MAX_VERIFICATION_ATTEMPTS,
        )
        if int(result[0]) != 1 or not result[1]:
            raise InvalidVerificationCodeError
        profile = json.loads(result[1])
        if not isinstance(profile, dict):
            raise InvalidVerificationCodeError
        return profile

    async def discard(self, email: str, digest: str) -> None:
        await self.redis.eval(
            _DISCARD_CODE_SCRIPT,
            1,
            f"email_auth:code:{self._email_key(email)}",
            digest,
        )

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        await self.redis.aclose()


class SMTPEmailSender:
    def __init__(self, config: Any = settings):
        self.host = config.SMTP_HOST
        self.port = config.SMTP_PORT
        self.username = config.SMTP_USERNAME
        self.password = (
            config.SMTP_PASSWORD.get_secret_value() if config.SMTP_PASSWORD is not None else None
        )
        self.from_email = config.SMTP_FROM_EMAIL
        self.use_tls = config.SMTP_USE_TLS

    async def send_code(self, email: str, code: str) -> None:
        message = EmailMessage()
        message["Subject"] = "登录验证码"
        message["From"] = self.from_email
        message["To"] = email
        message.set_content(f"您的验证码是 {code}，3 分钟内有效。请勿将验证码告知他人。")
        try:
            await asyncio.to_thread(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError from exc

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


class EmailAuthService:
    def __init__(
        self,
        users: UserRepositoryProtocol,
        codes: VerificationCodeStoreProtocol,
        sender: EmailSenderProtocol,
        token_secret: str,
        config: Any = settings,
    ):
        self.users = users
        self.codes = codes
        self.sender = sender
        self.token_secret = token_secret.encode()
        self.config = config

    def _digest(self, email: str, code: str) -> str:
        return hmac.new(self.token_secret, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()

    async def request_code(self, body: EmailCodeRequest) -> EmailCodeAccepted:
        email = str(body.email)
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = self._digest(email, code)
        profile = {
            "nickname": email.partition("@")[0],
            "image_url": None,
        }
        await self.codes.reserve(email, digest, profile)
        try:
            await self.sender.send_code(email, code)
        except EmailDeliveryError:
            await self.codes.discard(email, digest)
            raise
        return EmailCodeAccepted()

    async def verify(self, body: EmailCodeVerify) -> EmailAuthResponse:
        email = str(body.email)
        profile = await self.codes.consume(email, self._digest(email, body.code))
        user = await self.users.get_by_email(email)
        is_new_user = False
        if user is None:
            nickname = profile.get("nickname")
            if not isinstance(nickname, str) or not nickname:
                raise InvalidVerificationCodeError
            image_url = profile.get("image_url")
            user, is_new_user = await self.users.create_or_get(email, nickname, image_url)
        token, expires_at = create_access_token(str(user.id), self.config)
        return EmailAuthResponse(
            access_token=token,
            expires_at=expires_at,
            is_new_user=is_new_user,
            user=user,
        )


router = APIRouter(prefix="/auth/email", tags=["authentication"])


def _service(request: Request) -> EmailAuthService:
    service = getattr(request.app.state, "email_auth_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email authentication is disabled")
    return service


@router.post("/code", response_model=EmailCodeAccepted, status_code=status.HTTP_202_ACCEPTED)
async def request_email_code(body: EmailCodeRequest, request: Request) -> EmailCodeAccepted:
    try:
        return await _service(request).request_code(body)
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


@router.post("/verify", response_model=EmailAuthResponse)
async def verify_email_code(body: EmailCodeVerify, request: Request) -> EmailAuthResponse:
    try:
        return await _service(request).verify(body)
    except InvalidVerificationCodeError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired verification code"
        ) from None
    except RedisError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Verification service is unavailable"
        ) from None


def _validate_email_auth_settings(config: Any) -> str:
    if config.APP_TOKEN_SECRET is None:
        raise ValueError("APP_TOKEN_SECRET is required for email authentication")
    token_secret = config.APP_TOKEN_SECRET.get_secret_value()
    if len(token_secret) < 32:
        raise ValueError("APP_TOKEN_SECRET must contain at least 32 characters")
    if not config.SMTP_HOST or not config.SMTP_FROM_EMAIL:
        raise ValueError("SMTP_HOST and SMTP_FROM_EMAIL are required for email authentication")
    if bool(config.SMTP_USERNAME) != bool(config.SMTP_PASSWORD):
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
    return token_secret


@asynccontextmanager
async def email_auth_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.email_auth_service = None
    if not settings.EMAIL_AUTH_ENABLED:
        yield
        return
    token_secret = _validate_email_auth_settings(settings)
    users = UserRepository(get_postgres_connection_string(), settings.AUTH_SERVICE_ACCOUNT_ID)
    redis = Redis.from_url(settings.REDIS_URL.get_secret_value(), decode_responses=True)
    codes = VerificationCodeStore(cast(AsyncRedisProtocol, redis))
    try:
        await users.open()
        await codes.ping()
        app.state.email_auth_service = EmailAuthService(
            users,
            codes,
            SMTPEmailSender(settings),
            token_secret,
        )
        yield
    finally:
        app.state.email_auth_service = None
        await codes.close()
        await users.close()
