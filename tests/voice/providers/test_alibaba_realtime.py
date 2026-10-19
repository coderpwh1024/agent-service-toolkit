import asyncio
import base64
from unittest.mock import AsyncMock

import pytest

from core import settings
from voice.providers.alibaba_realtime import AlibabaRealtime


@pytest.mark.asyncio
async def test_first_audio_deadline_survives_commit_acknowledgement(monkeypatch):
    provider = AlibabaRealtime(settings.model_copy(update={"VOICE_UPSTREAM_TIMEOUT": 1}))
    monkeypatch.setattr(AlibabaRealtime, "FIRST_AUDIO_TIMEOUT", 0.02, raising=False)
    events = asyncio.Queue()
    events.put_nowait({"type": "input_text_buffer.committed"})
    provider.send = AsyncMock()

    async def receive(*args, **kwargs):
        return await events.get()

    provider.receive = receive
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2) as watchdog:
            async for _ in provider.synthesize(object(), "测试提交已确认但没有声音。"):
                pytest.fail("No audio was sent")
    assert not watchdog.expired()
    assert provider.send.await_count == 2
    assert events.empty()


@pytest.mark.asyncio
async def test_audio_stream_can_outlive_first_audio_deadline(monkeypatch):
    provider = AlibabaRealtime(settings.model_copy(update={"VOICE_UPSTREAM_TIMEOUT": 1}))
    monkeypatch.setattr(AlibabaRealtime, "FIRST_AUDIO_TIMEOUT", 0.02, raising=False)
    provider.send = AsyncMock()
    pcm = b"\0\0" * 480
    events = iter(
        [
            {"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()},
            {"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()},
            {"type": "response.done", "response": {"status": "completed"}},
        ]
    )

    async def receive(*args, **kwargs):
        return next(events)

    provider.receive = receive
    stream = provider.synthesize(object(), "测试首包之后继续接收语音。")
    assert await anext(stream) == pcm
    await asyncio.sleep(0.04)
    assert [chunk async for chunk in stream] == [pcm]
