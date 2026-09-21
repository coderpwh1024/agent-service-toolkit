"""Mobile realtime voice endpoints, identity checks and service lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket

from agents import get_agent, get_all_agent_info
from core import settings
from memory.postgres import get_postgres_connection_string
from schema import ApiResponse, api_success
from schema.voice import VoiceSession, VoiceSessionInput, client_event_adapter
from service.access import authenticate, authorize_thread, bind_user, principal, repository
from service.agent_runner import thread_guard
from service.utils import ensure_model_available
from voice.persistence import VoiceRepository
from voice.providers.alibaba_realtime import AlibabaRealtime
from voice.session import VoiceConnection

router = APIRouter(prefix="/voice", tags=["voice"])


@asynccontextmanager
async def voice_lifespan(app: FastAPI):
    app.state.voice_repository = None
    app.state.voice_connections = {}
    if not settings.VOICE_ENABLED and not settings.APP_TOKEN_SECRET:
        yield
        return
    if settings.VOICE_ENABLED and not settings.AUTH_SECRET:
        raise ValueError("AUTH_SECRET is required for realtime voice")
    if settings.VOICE_ENABLED and not settings.APP_TOKEN_SECRET:
        raise ValueError("APP_TOKEN_SECRET is required for realtime voice")
    if settings.APP_TOKEN_SECRET and len(settings.APP_TOKEN_SECRET.get_secret_value()) < 32:
        raise ValueError("APP_TOKEN_SECRET must contain at least 32 characters")
    if settings.VOICE_ENABLED and not settings.DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY is required for realtime voice")
    repo = VoiceRepository(get_postgres_connection_string())
    try:
        await repo.open()
        app.state.voice_repository = repo
        yield
    finally:
        connections = list(app.state.voice_connections.values())
        await asyncio.gather(*(c.stop() for c in connections), return_exceptions=True)
        tasks = [getattr(c, "task", None) for c in connections]
        await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)
        app.state.voice_repository = None
        await repo.close()


def require_voice(request: Request) -> VoiceRepository:
    repo = repository(request)
    if not settings.VOICE_ENABLED or repo is None:
        raise HTTPException(503, "Realtime voice is disabled")
    return repo


async def owned_session(repo: VoiceRepository, session_id: UUID, identity) -> VoiceSession:
    session = await repo.get_session(str(session_id))
    if session is None:
        raise HTTPException(404, "Voice session not found")
    if not identity.admin and session.user_id != identity.user_id:
        raise HTTPException(403, "Voice session belongs to another user")
    return session


@router.get("/capabilities", response_model=ApiResponse[dict[str, Any]])
async def capabilities(request: Request) -> ApiResponse[dict[str, Any]]:
    principal(request)
    return api_success(
        {
            "enabled": settings.VOICE_ENABLED,
            "protocol_version": 1,
            "input_format": "pcm16_16000_mono",
            "output_format": "pcm16_24000_mono",
            "audio_header_bytes": 45,
            "max_audio_payload_bytes": 6400,
            "voices": settings.VOICE_REALTIME_VOICES,
            "stt_model": settings.VOICE_REALTIME_STT_MODEL,
            "tts_model": settings.VOICE_REALTIME_TTS_MODEL,
            "session_seconds": settings.VOICE_SESSION_SECONDS,
            "agents": [
                {
                    "id": a.key,
                    "description": a.description,
                    "speech_mode": "streaming" if a.key == "chatbot" else "final_message",
                    "cancel_execution": a.key == "chatbot",
                }
                for a in get_all_agent_info()
            ],
        }
    )


@router.get("/protocol", response_model=ApiResponse[dict[str, Any]])
async def protocol(request: Request) -> ApiResponse[dict[str, Any]]:
    principal(request)
    return api_success(client_event_adapter.json_schema())


@router.post("/sessions", response_model=ApiResponse[VoiceSession])
async def create_session(body: VoiceSessionInput, request: Request) -> ApiResponse[VoiceSession]:
    identity = principal(request)
    repo = require_voice(request)
    user_id = bind_user(identity, body.user_id)
    try:
        agent = get_agent(body.agent_id)
    except KeyError:
        raise HTTPException(404, "Agent not found") from None
    if body.voice not in settings.VOICE_REALTIME_VOICES:
        raise HTTPException(422, "Unsupported voice")
    if body.model is not None:
        ensure_model_available(body.model)
    thread_id = body.thread_id or str(uuid4())
    async with repo.guard(f"voice-user:{user_id}"), thread_guard(repo, thread_id):
        if await repo.active_sessions(user_id) >= 2:
            raise HTTPException(429, "At most two unexpired voice sessions per user")
        await authorize_thread(
            repo, agent, thread_id, body.agent_id, identity, user_id, create=True
        )
        session = VoiceSession(
            **body.model_dump(exclude={"thread_id", "user_id"}),
            thread_id=thread_id,
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.VOICE_SESSION_SECONDS),
        )
        await repo.save_session(session)
    return api_success(session)


@router.get("/sessions/{session_id}", response_model=ApiResponse[VoiceSession])
async def get_session(session_id: UUID, request: Request) -> ApiResponse[VoiceSession]:
    session = await owned_session(require_voice(request), session_id, principal(request))
    return api_success(session)


@router.get("/sessions/{session_id}/turns", response_model=ApiResponse[dict[str, Any]])
async def get_turns(session_id: UUID, request: Request) -> ApiResponse[dict[str, Any]]:
    repo = require_voice(request)
    session = await owned_session(repo, session_id, principal(request))
    return api_success(
        {
            "turns": [
                t for t in await repo.turns(session.thread_id) if t.session_id == session.session_id
            ]
        }
    )


@router.delete("/sessions/{session_id}", response_model=ApiResponse[dict[str, str]])
async def close_session(session_id: UUID, request: Request) -> ApiResponse[dict[str, str]]:
    repo = require_voice(request)
    session = await owned_session(repo, session_id, principal(request))
    connection = request.app.state.voice_connections.get(str(session_id))
    if connection:
        await connection.stop()
        return api_success({"status": "closing"})
    async with repo.guard(f"voice-session:{session_id}"):
        session.status = "closed"
        await repo.save_session(session)
    return api_success({"status": "closed"})


@router.websocket("/sessions/{session_id}/ws")
async def voice_websocket(websocket: WebSocket, session_id: UUID):
    connection = None
    registered = False
    try:
        identity = authenticate(websocket.headers.get("authorization"))
        repo = getattr(websocket.app.state, "voice_repository", None)
        if not settings.VOICE_ENABLED or repo is None:
            raise HTTPException(503, "Realtime voice is disabled")
        session = await owned_session(repo, session_id, identity)
        if session.status == "closed" or session.expires_at <= datetime.now(UTC):
            raise HTTPException(410, "Voice session expired or closed")
        connections = websocket.app.state.voice_connections
        if len(connections) >= settings.VOICE_MAX_SESSIONS:
            raise HTTPException(429, "Voice capacity reached")
        async with repo.guard(f"voice-session:{session_id}"):
            session = await owned_session(repo, session_id, identity)
            if session.status == "closed":
                raise HTTPException(410, "Voice session is closed")
            connection = VoiceConnection(
                websocket,
                session,
                repo,
                get_agent(session.agent_id),
                AlibabaRealtime(settings),
                settings,
                identity.expires_at,
            )
            connection.task = asyncio.current_task()
            connections[str(session_id)] = connection
            registered = True
            await websocket.accept(subprotocol=None)
            await connection.run()
    except HTTPException as exc:
        await websocket.close(
            code={401: 4401, 403: 4403, 404: 4404, 409: 4409, 410: 4410, 429: 4429}.get(
                exc.status_code, 1013
            )
        )
    finally:
        if registered:
            websocket.app.state.voice_connections.pop(str(session_id), None)
