"""User-scoped access tokens alongside trusted service credentials."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from fastapi import APIRouter, HTTPException, Request
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, SecretStr

from core import settings
from voice.persistence import VoiceRepository

router = APIRouter(prefix="/auth")


@dataclass(frozen=True)
class Principal:
    user_id: str | None = None
    admin: bool = False
    expires_at: int | None = None


def authenticate(authorization: str | None, config: Any = settings) -> Principal:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    secret = config.AUTH_SECRET
    app_secret = config.APP_TOKEN_SECRET
    if (
        isinstance(secret, SecretStr)
        and token
        and hmac.compare_digest(token, secret.get_secret_value())
    ):
        return Principal(admin=True)
    if token and isinstance(app_secret, SecretStr):
        try:
            claims = jwt.decode(
                token,
                app_secret.get_secret_value(),
                algorithms=["HS256"],
                audience="agent-service-app",
                issuer="agent-service-toolkit",
                options={"require": ["sub", "exp", "iat", "jti"]},
            )
            subject = claims["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 128:
                raise ValueError("Invalid subject")
            return Principal(subject, expires_at=claims["exp"])
        except (jwt.InvalidTokenError, ValueError):
            raise HTTPException(401, "Invalid or expired access token") from None
    if not secret and not isinstance(app_secret, SecretStr):
        return Principal(admin=True)
    raise HTTPException(401, "Authentication required")


def principal(request: Request) -> Principal:
    return authenticate(request.headers.get("authorization"))


def repository(request: Request) -> VoiceRepository | None:
    return getattr(request.app.state, "voice_repository", None)


def bind_user(identity: Principal, requested: str | None) -> str:
    if not identity.admin:
        if requested is not None and requested != identity.user_id:
            raise HTTPException(403, "user_id does not match the authenticated user")
        assert identity.user_id
        return identity.user_id
    return requested or str(uuid4())


async def authorize_thread(
    repo: VoiceRepository | None,
    agent: Any,
    thread_id: str,
    agent_id: str,
    identity: Principal,
    user_id: str | None = None,
    *,
    create: bool = False,
) -> None:
    if repo is None:
        if not identity.admin:
            raise HTTPException(503, "Thread authorization is unavailable")
        return
    owner = await repo.owner(thread_id)
    if owner:
        if owner[1] != agent_id or (not identity.admin and owner[0] != identity.user_id):
            raise HTTPException(403, "Thread belongs to another user or agent")
        if create and user_id and owner[0] != user_id:
            raise HTTPException(403, "Thread user_id cannot change")
        return
    saver = getattr(agent, "checkpointer", None)
    checkpoint = (
        await saver.aget_tuple(RunnableConfig(configurable={"thread_id": thread_id}))
        if saver
        else None
    )
    if checkpoint:
        metadata = checkpoint.metadata or {}
        existing_user = metadata.get("user_id")
        existing_agent = metadata.get("agent_id")
        if not identity.admin and (existing_user != identity.user_id or existing_agent != agent_id):
            raise HTTPException(403, "Thread ownership cannot be verified")
        if existing_user and existing_agent:
            await repo.claim(thread_id, existing_user, existing_agent)
            return
    if create and user_id:
        await repo.claim(thread_id, user_id, agent_id)
    elif not identity.admin:
        raise HTTPException(404, "Thread not found")


class TokenInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


@router.post("/token")
async def issue_token(body: TokenInput, request: Request) -> dict[str, Any]:
    if not settings.AUTH_SECRET or not settings.APP_TOKEN_SECRET:
        raise HTTPException(503, "AUTH_SECRET and APP_TOKEN_SECRET must be configured")
    if not principal(request).admin:
        raise HTTPException(403, "Only a trusted service can provision app tokens")
    now = datetime.now(UTC)
    expiry = now + timedelta(seconds=settings.APP_TOKEN_TTL_SECONDS)
    token = jwt.encode(
        {
            "sub": body.user_id,
            "iat": now,
            "exp": expiry,
            "jti": str(uuid4()),
            "aud": "agent-service-app",
            "iss": "agent-service-toolkit",
        },
        settings.APP_TOKEN_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_at": int(expiry.timestamp())}
