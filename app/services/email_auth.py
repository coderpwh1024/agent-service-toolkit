"""Passwordless email login and registration backed by PostgreSQL and Redis."""

import asyncio
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Protocol

from app.core import settings
from app.repositories.verification_codes import InvalidVerificationCodeError
from app.schemas.auth import (
    EmailAuthResponse,
    EmailCodeAccepted,
    EmailCodeRequest,
    EmailCodeVerify,
    UserProfile,
)
from app.services.auth import create_access_token
from app.services.skills.email import EmailDeliveryError


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
        self.use_ssl = config.SMTP_USE_SSL

    async def send_code(self, email: str, code: str) -> None:
        await self.send_email(
            email, "登录验证码", f"您的验证码是 {code}，3 分钟内有效。请勿将验证码告知他人。"
        )

    async def send_email(self, email: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = email
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid()
        message.set_content(body, charset="utf-8")
        try:
            await asyncio.to_thread(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError from exc

    def _send(self, message: EmailMessage) -> None:
        with self._connect() as smtp:
            if self.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

    def _connect(self) -> smtplib.SMTP:
        if self.use_ssl:
            return smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=10,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(self.host, self.port, timeout=10)


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
