"""Asynchronous DashScope ASR and TTS with bounded connection lifetimes."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

from core.settings import Settings
from schema.voice import VoiceSession
from voice.catalog import speech_endpoint, speech_protocol
from voice.providers.alibaba_inference import InferenceStream
from voice.providers.errors import SpeechProviderError


class AlibabaRealtime:
    FIRST_AUDIO_TIMEOUT = 5.0

    def __init__(self, settings: Settings):
        self.settings = settings

    @asynccontextmanager
    async def connection(self, model: str, role: Literal["stt", "tts"] = "stt"):
        protocol = speech_protocol(
            model, role, getattr(self.settings, f"VOICE_REALTIME_{role.upper()}_PROTOCOL")
        )
        url = urlsplit(
            speech_endpoint(
                self.settings.VOICE_REALTIME_URL,
                protocol,
                getattr(self.settings, f"VOICE_REALTIME_{role.upper()}_URL"),
            )
        )
        key = self.settings.DASHSCOPE_API_KEY
        if not key:
            raise SpeechProviderError("DASHSCOPE_API_KEY is required")
        endpoint = urlunsplit(
            url._replace(query=urlencode({"model": model}) if protocol == "realtime" else "")
        )
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

    async def receive(
        self, ws: ClientConnection | InferenceStream, *, timeout: bool = True
    ) -> dict[str, Any]:
        if isinstance(ws, InferenceStream):
            return await ws.receive(timeout=timeout)
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
            if (
                speech_protocol(
                    self.settings.VOICE_REALTIME_STT_MODEL,
                    "stt",
                    self.settings.VOICE_REALTIME_STT_PROTOCOL,
                )
                == "inference"
            ):
                stream = InferenceStream(ws, session, self.settings, "stt")
                await stream.start()
                yield stream
                return
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

    async def append_audio(self, ws: ClientConnection | InferenceStream, pcm: bytes) -> None:
        if isinstance(ws, InferenceStream):
            await ws.append_audio(pcm)
            return
        await self.send(ws, "input_audio_buffer.append", audio=base64.b64encode(pcm).decode())

    async def commit_audio(self, ws: ClientConnection | InferenceStream) -> None:
        if isinstance(ws, InferenceStream):
            await ws.commit_audio()
            return
        await self.send(ws, "input_audio_buffer.commit")

    @asynccontextmanager
    async def synthesizer(self, session: VoiceSession):
        async with self.connection(self.settings.VOICE_REALTIME_TTS_MODEL, "tts") as ws:
            if (
                speech_protocol(
                    self.settings.VOICE_REALTIME_TTS_MODEL,
                    "tts",
                    self.settings.VOICE_REALTIME_TTS_PROTOCOL,
                )
                == "inference"
            ):
                yield InferenceStream(ws, session, self.settings, "tts")
                return
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

    async def synthesize(
        self, ws: ClientConnection | InferenceStream, text: str
    ) -> AsyncIterator[bytes]:
        if isinstance(ws, InferenceStream):
            async for chunk in ws.synthesize(
                text, min(self.FIRST_AUDIO_TIMEOUT, self.settings.VOICE_UPSTREAM_TIMEOUT)
            ):
                yield chunk
            return
        await self.send(ws, "input_text_buffer.append", text=text)
        await self.send(ws, "input_text_buffer.commit")
        first_audio_deadline = asyncio.get_running_loop().time() + min(
            self.FIRST_AUDIO_TIMEOUT, self.settings.VOICE_UPSTREAM_TIMEOUT
        )
        received_audio = False
        async with asyncio.timeout(120):
            while True:
                async with asyncio.timeout_at(None if received_audio else first_audio_deadline):
                    event = await self.receive(ws)
                if event.get("type") == "response.audio.delta":
                    try:
                        data = base64.b64decode(event["delta"], validate=True)
                    except (ValueError, KeyError) as exc:
                        raise SpeechProviderError("Invalid upstream audio") from exc
                    if len(data) % 2:
                        raise SpeechProviderError("Unaligned upstream PCM")
                    if data:
                        received_audio = True
                        yield data
                elif event.get("type") == "response.done":
                    response = event.get("response", {})
                    if response.get("status") in ("failed", "incomplete", "cancelled"):
                        raise SpeechProviderError("Speech synthesis did not complete")
                    return
                elif event.get("type") == "session.finished":
                    raise SpeechProviderError("Speech synthesis ended before the response")
