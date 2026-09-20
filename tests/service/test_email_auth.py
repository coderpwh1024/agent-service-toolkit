import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from schema.auth import EmailCodeRequest, EmailCodeVerify, UserProfile
from service.email_auth import (
    EMAIL_SEND_WINDOW_SECONDS,
    USER_SCHEMA_PATH,
    VERIFICATION_CODE_TTL_SECONDS,
    EmailAuthService,
    EmailRateLimitError,
    InvalidVerificationCodeError,
    NicknameRequiredError,
    UserRepository,
    VerificationCodeStore,
    router,
)


class FakeUsers:
    def __init__(self):
        self.users: dict[str, UserProfile] = {}

    async def get_by_email(self, email: str) -> UserProfile | None:
        return self.users.get(email)

    async def create_or_get(
        self, email: str, nickname: str, image_url: str | None
    ) -> tuple[UserProfile, bool]:
        existing = self.users.get(email)
        if existing:
            return existing, False
        user = UserProfile(
            id=len(self.users) + 1, nickname=nickname, email=email, image_url=image_url
        )
        self.users[email] = user
        return user, True


class FakeCodes:
    def __init__(self):
        self.entries: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.send_counts: dict[str, int] = {}

    async def reserve(
        self, email: str, digest: str, registration_profile: dict[str, str | None]
    ) -> None:
        count = self.send_counts.get(email, 0)
        if count >= 6:
            raise EmailRateLimitError(3600)
        self.send_counts[email] = count + 1
        self.entries[email] = (digest, registration_profile)

    async def consume(self, email: str, digest: str) -> dict[str, str | None]:
        entry = self.entries.get(email)
        if not entry or entry[0] != digest:
            raise InvalidVerificationCodeError
        del self.entries[email]
        return entry[1]

    async def discard(self, email: str, digest: str) -> None:
        if self.entries.get(email, (None,))[0] == digest:
            del self.entries[email]

    async def ping(self) -> bool:
        return True


class CapturingSender:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_code(self, email: str, code: str) -> None:
        self.messages.append((email, code))


@pytest.fixture
def auth_components():
    users = FakeUsers()
    codes = FakeCodes()
    sender = CapturingSender()
    config = SimpleNamespace(
        APP_TOKEN_SECRET=SecretStr("x" * 32),
        APP_TOKEN_TTL_SECONDS=3600,
    )
    service = EmailAuthService(users, codes, sender, "x" * 32, config)
    return service, users, codes, sender, config


@pytest.mark.asyncio
async def test_new_email_requires_nickname(auth_components):
    service, _, _, _, _ = auth_components

    with pytest.raises(NicknameRequiredError):
        await service.request_code(EmailCodeRequest(email="new@example.com"))


@pytest.mark.asyncio
async def test_new_email_registers_and_issues_user_bound_token(auth_components):
    service, users, _, sender, config = auth_components

    accepted = await service.request_code(
        EmailCodeRequest(
            email=" New@Example.COM ",
            nickname=" 新用户 ",
            image_url="https://example.com/avatar.png",
        )
    )

    assert accepted.expires_in_seconds == 180
    assert sender.messages[0][0] == "new@example.com"
    assert re.fullmatch(r"[0-9]{6}", sender.messages[0][1])

    response = await service.verify(
        EmailCodeVerify(email="new@example.com", code=sender.messages[0][1])
    )

    assert response.is_new_user is True
    assert response.user.nickname == "新用户"
    assert str(response.user.image_url) == "https://example.com/avatar.png"
    assert users.users["new@example.com"].id == response.user.id
    claims = jwt.decode(
        response.access_token,
        config.APP_TOKEN_SECRET.get_secret_value(),
        algorithms=["HS256"],
        audience="agent-service-app",
        issuer="agent-service-toolkit",
    )
    assert claims["sub"] == str(response.user.id)

    with pytest.raises(InvalidVerificationCodeError):
        await service.verify(EmailCodeVerify(email="new@example.com", code=sender.messages[0][1]))


@pytest.mark.asyncio
async def test_existing_email_logs_in_without_replacing_profile(auth_components):
    service, users, _, sender, _ = auth_components
    users.users["member@example.com"] = UserProfile(
        id=41,
        nickname="原昵称",
        email="member@example.com",
        image_url=None,
    )

    await service.request_code(EmailCodeRequest(email="MEMBER@example.com"))
    response = await service.verify(
        EmailCodeVerify(email="member@example.com", code=sender.messages[0][1])
    )

    assert response.is_new_user is False
    assert response.user.id == 41
    assert response.user.nickname == "原昵称"


@pytest.mark.asyncio
async def test_same_email_is_limited_to_six_sends(auth_components):
    service, users, _, _, _ = auth_components
    users.users["member@example.com"] = UserProfile(
        id=1,
        nickname="member",
        email="member@example.com",
    )

    for _ in range(6):
        await service.request_code(EmailCodeRequest(email="member@example.com"))

    with pytest.raises(EmailRateLimitError) as exc_info:
        await service.request_code(EmailCodeRequest(email="member@example.com"))
    assert exc_info.value.retry_after == 3600


@pytest.mark.asyncio
async def test_redis_reservation_uses_required_ttls_and_returns_retry_after():
    redis = AsyncMock()
    redis.eval.return_value = [6, EMAIL_SEND_WINDOW_SECONDS]
    store = VerificationCodeStore(redis)

    await store.reserve("user@example.com", "digest", {"nickname": None, "image_url": None})

    args = redis.eval.await_args.args
    assert VERIFICATION_CODE_TTL_SECONDS in args
    assert EMAIL_SEND_WINDOW_SECONDS in args

    redis.eval.return_value = [0, 123]
    with pytest.raises(EmailRateLimitError) as exc_info:
        await store.reserve("user@example.com", "digest", {"nickname": None, "image_url": None})
    assert exc_info.value.retry_after == 123


@pytest.mark.asyncio
async def test_user_repository_executes_the_standalone_schema():
    class FakeConnection:
        def __init__(self):
            self.executed: list[Any] = []

        async def execute(self, sql: Any) -> None:
            self.executed.append(sql)

    class ConnectionContext:
        def __init__(self, connection: FakeConnection):
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, *_args) -> None:
            return None

    class FakePool:
        def __init__(self):
            self.connection_value = FakeConnection()
            self.opened = False

        async def open(self, *, wait: bool) -> None:
            self.opened = wait

        def connection(self) -> ConnectionContext:
            return ConnectionContext(self.connection_value)

    pool = FakePool()
    repository = object.__new__(UserRepository)
    repository.pool = pool

    await repository.open()

    schema_sql = USER_SCHEMA_PATH.read_text(encoding="utf-8")
    assert pool.opened is True
    assert pool.connection_value.executed[0].as_string() == schema_sql
    assert "id BIGINT GENERATED BY DEFAULT AS IDENTITY" in schema_sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uk_app_users_email_active" in schema_sql
    assert "WHERE is_delete = 0" in schema_sql


def test_email_auth_routes_and_strict_input_validation(auth_components):
    service, _, _, sender, _ = auth_components
    app = FastAPI()
    app.state.email_auth_service = service
    app.include_router(router)

    with TestClient(app) as client:
        invalid = client.post(
            "/auth/email/code",
            json={"email": "not-an-email", "nickname": "user", "unexpected": True},
        )
        requested = client.post(
            "/auth/email/code",
            json={"email": "user@example.com", "nickname": "user"},
        )
        invalid_code = client.post(
            "/auth/email/verify",
            json={"email": "user@example.com", "code": "12345"},
        )
        verified = client.post(
            "/auth/email/verify",
            json={"email": "user@example.com", "code": sender.messages[0][1]},
        )

    assert invalid.status_code == 422
    assert requested.status_code == 202
    assert requested.json() == {"expires_in_seconds": 180}
    assert invalid_code.status_code == 422
    assert verified.status_code == 200
    assert verified.json()["is_new_user"] is True


def test_email_auth_route_is_unavailable_when_disabled():
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/auth/email/code",
            json={"email": "user@example.com", "nickname": "user"},
        )

    assert response.status_code == 503
