"""Asynchronous DashScope ASR and TTS with bounded connection lifetimes."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

from core.settings import Settings
from schema.voice import VoiceSession


class SpeechProviderError(Exception):
    pass


class AlibabaRealtime:
    def __init__(self, settings: Settings):
        self.settings = settings

    @asynccontextmanager
    async def connection(self, model: str):
        url = urlsplit(self.settings.VOICE_REALTIME_URL)
        if url.scheme != "wss" or url.query or url.fragment:
            raise SpeechProviderError("Realtime endpoint must be a wss URL without a query")
        key = self.settings.DASHSCOPE_API_KEY
        if not key:
            raise SpeechProviderError("DASHSCOPE_API_KEY is required")
        endpoint = urlunsplit(url._replace(query=urlencode({"model": model})))
        async with connect(
            endpoint,
            additional_headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            open_timeout=self.settings.VOICE_UPSTREAM_TIMEOUT,
            close_timeout=2,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
            max_queue=16,
            proxy=self.settings.VOICE_REALTIME_PROXY,
        ) as ws:
            yield ws

    async def send(self, ws: ClientConnection, event_type: str, **data: Any) -> None:
        async with asyncio.timeout(self.settings.VOICE_UPSTREAM_TIMEOUT):
            await ws.send(json.dumps({"event_id": str(uuid4()), "type": event_type, **data}))

    async def receive(self, ws: ClientConnection, *, timeout: bool = True) -> dict[str, Any]:
        async with asyncio.timeout(self.settings.VOICE_UPSTREAM_TIMEOUT if timeout else None):
            event = json.loads(await ws.recv())
        if not isinstance(event, dict):
            raise SpeechProviderError("Invalid upstream event")
        if event.get("type") in ("error", "conversation.item.input_audio_transcription.failed"):
            raise SpeechProviderError("Speech provider rejected the request")
        return event

    async def ready(self, ws: ClientConnection) -> None:
        async with asyncio.timeout(self.settings.VOICE_UPSTREAM_TIMEOUT):
            while True:
                event = await self.receive(ws)
                if event.get("type") == "session.updated":
                    return

    @asynccontextmanager
    async def recognize(self, session: VoiceSession):
        async with self.connection(self.settings.VOICE_REALTIME_STT_MODEL) as ws:
            config: dict[str, Any] = {
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "turn_detection": None
                if session.turn_detection == "manual"
                else {
                    "type": "server_vad",
                    "threshold": self.settings.VOICE_VAD_THRESHOLD,
                    "silence_duration_ms": self.settings.VOICE_VAD_SILENCE_MS,
                },
            }
            if session.language != "auto":
                config["input_audio_transcription"] = {"language": session.language}
            await self.send(ws, "session.update", session=config)
            await self.ready(ws)
            yield ws

    async def append_audio(self, ws: ClientConnection, pcm: bytes) -> None:
        await self.send(ws, "input_audio_buffer.append", audio=base64.b64encode(pcm).decode())

    async def commit_audio(self, ws: ClientConnection) -> None:
        await self.send(ws, "input_audio_buffer.commit")

    @asynccontextmanager
    async def synthesizer(self, session: VoiceSession):
        async with self.connection(self.settings.VOICE_REALTIME_TTS_MODEL) as ws:
            await self.send(
                ws,
                "session.update",
                session={
                    "voice": session.voice,
                    "mode": "commit",
                    "response_format": "pcm",
                    "sample_rate": 24000,
                    "language_type": {"zh": "Chinese", "en": "English", "auto": "Auto"}[
                        session.language
                    ],
                },
            )
            await self.ready(ws)
            yield ws

    async def synthesize(self, ws: ClientConnection, text: str) -> AsyncIterator[bytes]:
        await self.send(ws, "input_text_buffer.append", text=text)
        await self.send(ws, "input_text_buffer.commit")
        async with asyncio.timeout(120):
            while True:
                event = await self.receive(ws)
                if event.get("type") == "response.audio.delta":
                    try:
                        data = base64.b64decode(event["delta"], validate=True)
                    except (ValueError, KeyError) as exc:
                        raise SpeechProviderError("Invalid upstream audio") from exc
                    if len(data) % 2:
                        raise SpeechProviderError("Unaligned upstream PCM")
                    if data:
                        yield data
                elif event.get("type") == "response.done":
                    response = event.get("response", {})
                    if response.get("status") in ("failed", "incomplete", "cancelled"):
                        raise SpeechProviderError("Speech synthesis did not complete")
                    return
                elif event.get("type") == "session.finished":
                    raise SpeechProviderError("Speech synthesis ended before the response")
