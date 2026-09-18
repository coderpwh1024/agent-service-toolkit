"""Versioned mobile voice contract and framed PCM transport."""

import struct
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from schema.models import AllModelEnum

PROTOCOL_VERSION = 1
AUDIO_HEADER = struct.Struct("!4sB16s16sII")
MAX_AUDIO_BYTES = 6400
ZERO_UUID = UUID(int=0)


class VoiceSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(default="chatbot", min_length=1, max_length=100)
    model: AllModelEnum | None = None
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    voice: str = Field(default="Cherry", min_length=1, max_length=100)
    language: Literal["zh", "en", "auto"] = "zh"
    turn_detection: Literal["server_vad", "manual"] = "server_vad"


class VoiceSession(BaseModel):
    agent_id: str = "chatbot"
    model: AllModelEnum | None = None
    voice: str = "Cherry"
    language: Literal["zh", "en", "auto"] = "zh"
    turn_detection: Literal["server_vad", "manual"] = "server_vad"
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    status: Literal["ready", "connected", "disconnected", "closed"] = "ready"
    connection_id: str | None = None
    protocol_version: Literal[1] = 1


class AudioSegment(BaseModel):
    index: int
    text: str
    samples: int = 0
    played_samples: int = 0
    complete: bool = False


class VoiceTurn(BaseModel):
    turn_id: str = Field(default_factory=lambda: str(uuid4()))
    response_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    input_id: str
    input_text: str
    run_id: str | None = None
    status: Literal["queued", "running", "completed", "cancelled", "failed", "waiting_approval"] = (
        "queued"
    )
    execution_status: Literal[
        "queued", "running", "completed", "cancelled", "failed", "waiting_approval"
    ] = "queued"
    generated_text: str = ""
    segments: list[AudioSegment] = Field(default_factory=list)
    interrupted: bool = False
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)


class Configure(Event):
    type: Literal["session.configure"]
    protocol_version: Literal[1] = 1
    input_format: Literal["pcm16_16000_mono"] = "pcm16_16000_mono"
    output_format: Literal["pcm16_24000_mono"] = "pcm16_24000_mono"


class TextInput(Event):
    type: Literal["input.text"]
    text: str = Field(min_length=1, max_length=6000)


class CommitInput(Event):
    type: Literal["input.commit"]


class CancelResponse(Event):
    type: Literal["response.cancel"]
    response_id: UUID


class SpeechHint(Event):
    type: Literal["input.speech_hint"]


class PlaybackProgress(Event):
    type: Literal["playback.progress"]
    response_id: UUID
    segment_index: int = Field(ge=1)
    played_samples: int = Field(ge=0)


class PlaybackFinished(Event):
    type: Literal["playback.finished"]
    response_id: UUID


class ApprovalInput(Event):
    type: Literal["approval.submit"]
    interrupt_id: str = Field(min_length=1, max_length=128)
    value: Any


class Ping(Event):
    type: Literal["ping"]


class CloseSession(Event):
    type: Literal["session.close"]


ClientEvent = Annotated[
    Configure
    | TextInput
    | CommitInput
    | CancelResponse
    | SpeechHint
    | PlaybackProgress
    | PlaybackFinished
    | ApprovalInput
    | Ping
    | CloseSession,
    Field(discriminator="type"),
]
client_event_adapter: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


def encode_audio(
    pcm: bytes,
    connection_id: str,
    response_id: str,
    segment_index: int,
    sequence: int,
    *,
    kind: int = 2,
) -> bytes:
    if not pcm or len(pcm) > MAX_AUDIO_BYTES or len(pcm) % 2:
        raise ValueError("Invalid PCM frame size")
    return (
        AUDIO_HEADER.pack(
            b"VCE1",
            kind,
            UUID(connection_id).bytes,
            UUID(response_id).bytes,
            segment_index,
            sequence,
        )
        + pcm
    )


def decode_audio(data: bytes) -> tuple[int, str, str, int, int, bytes]:
    if not AUDIO_HEADER.size < len(data) <= AUDIO_HEADER.size + MAX_AUDIO_BYTES:
        raise ValueError("Invalid audio frame size")
    magic, kind, connection, response, segment, sequence = AUDIO_HEADER.unpack_from(data)
    pcm = data[AUDIO_HEADER.size :]
    if magic != b"VCE1" or kind not in (1, 2) or len(pcm) % 2:
        raise ValueError("Invalid audio frame")
    return kind, str(UUID(bytes=connection)), str(UUID(bytes=response)), segment, sequence, pcm
