from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents import DEFAULT_AGENT, AgentGraph
from app.agents import get_agent as main_agent
from app.core import settings
from app.core.agent_ids import canonical_agent_id
from app.core.exceptions import ServiceError
from app.repositories.voice import VoiceRepository
from app.services.auth import Principal, authenticate


def principal(request: Request) -> Principal:
    try:
        return authenticate(request.headers.get("authorization"))
    except ServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def repository(request: Request) -> VoiceRepository | None:
    return getattr(request.app.state, "voice_repository", None)


def verify_bearer(
    request: Request,
    http_auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(
            HTTPBearer(
                description="Provide a trusted AUTH_SECRET or a short-lived app access token.",
                auto_error=False,
            )
        ),
    ],
) -> None:
    header = f"Bearer {http_auth.credentials}" if http_auth else None
    try:
        request.state.principal = authenticate(header, settings)
    except ServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def get_agent(agent_id: str = DEFAULT_AGENT) -> AgentGraph:
    try:
        return main_agent(canonical_agent_id(agent_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found") from None
