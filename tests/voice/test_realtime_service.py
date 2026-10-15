import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from langgraph.func import entrypoint
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.types import interrupt
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from core import settings
from schema.voice import ZERO_UUID, VoiceOption, VoiceTurn, decode_audio, encode_audio
from service import app


class MemoryRepository:
    def __init__(self):
        self.sessions = {}
        self.records = {}
        self.owners = {}
        self.runs = {}
        self.locks = set()

    @asynccontextmanager
    async def guard(self, key):
        if key in self.locks:
            raise HTTPException(409, "busy")
        self.locks.add(key)
        try:
            yield
        finally:
            self.locks.remove(key)

    async def owner(self, thread_id):
        return self.owners.get(thread_id)

    async def claim(self, thread_id, user_id, agent_id):
        self.owners.setdefault(thread_id, (user_id, agent_id))
        if self.owners[thread_id] != (user_id, agent_id):
            raise HTTPException(403, "wrong owner")

    async def register_run(self, run_id, user_id, thread_id):
        self.runs.setdefault(run_id, (user_id, thread_id))

    async def owns_run(self, run_id, user_id):
        return self.runs.get(run_id, (None, None))[0] == user_id

    async def save_session(self, session):
        self.sessions[session.session_id] = session.model_copy(deep=True)

    async def get_session(self, session_id):
        session = self.sessions.get(session_id)
        return session.model_copy(deep=True) if session else None

    async def active_sessions(self, user_id):
        return sum(
            s.user_id == user_id and s.status in {"created", "connected"}
            for s in self.sessions.values()
        )

    async def save_turn(self, turn):
        self.records[turn.turn_id] = turn.model_copy(deep=True)

    async def has_input(self, session_id, input_id):
        return any(
            t.session_id == session_id and t.input_id == input_id for t in self.records.values()
        )

    async def turns(self, thread_id, limit=50):
        return [t.model_copy(deep=True) for t in self.records.values() if t.thread_id == thread_id][
            -limit:
        ]


class FakeSpeech:
    def __init__(self, config):
        self.events = asyncio.Queue()

    @asynccontextmanager
    async def recognize(self, session):
        yield self

    @asynccontextmanager
    async def synthesizer(self, session):
        yield self

    async def append_audio(self, ws, pcm):
        await self.events.put({"type": "input_audio_buffer.speech_started", "item_id": "speech-1"})
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.text",
                "item_id": "speech-1",
                "text": "你",
                "stash": "好",
            }
        )
        for _ in range(2):
            await self.events.put(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "speech-1",
                    "transcript": "你好",
                }
            )

    async def commit_audio(self, ws):
        await self.append_audio(ws, b"\0\0")

    async def receive(self, ws, timeout=False):
        return await self.events.get()

    async def synthesize(self, ws, text):
        yield b"\0\0" * 480
        await asyncio.sleep(0.002)
        yield b"\0\0" * 480


class SlowFirstSpeech(FakeSpeech):
    def __init__(self, config):
        super().__init__(config)
        self.synthesis_calls = 0

    async def synthesize(self, ws, text):
        self.synthesis_calls += 1
        if self.synthesis_calls == 1:
            await asyncio.sleep(2.1)
        yield b"\0\0" * 480


class FailingSpeech(FakeSpeech):
    async def synthesize(self, ws, text):
        await asyncio.sleep(0.05)
        if False:
            yield b""
        raise RuntimeError("synthetic TTS failure")


class WakeSpeech(FakeSpeech):
    transcript = "小美，小美，介绍一下你自己"

    async def append_audio(self, ws, pcm):
        await self.events.put(
            {"type": "input_audio_buffer.speech_started", "item_id": "wake-speech"}
        )
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "wake-speech",
                "transcript": self.transcript,
            }
        )


class RejectedWakeSpeech(FakeSpeech):
    async def append_audio(self, ws, pcm):
        await self.events.put(
            {"type": "input_audio_buffer.speech_started", "item_id": "false-wake"}
        )
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "false-wake",
                "transcript": "今天天气不错",
            }
        )


class SegmentedWakeSpeech(FakeSpeech):
    transcripts = ("小美", "小美", "小美小美小美", "小美，小美，介绍一下你自己")

    def __init__(self, config):
        super().__init__(config)
        self.index = 0

    async def append_audio(self, ws, pcm):
        transcript = self.transcripts[self.index]
        self.index += 1
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": f"wake-{self.index}",
                "transcript": transcript,
            }
        )


class EchoFillerSpeech(FakeSpeech):
    second_transcript = "嗯"

    def __init__(self, config):
        super().__init__(config)
        self.utterances = 0

    async def append_audio(self, ws, pcm):
        self.utterances += 1
        item_id = f"speech-{self.utterances}"
        transcript = "你好" if self.utterances == 1 else self.second_transcript
        await self.events.put({"type": "input_audio_buffer.speech_started", "item_id": item_id})
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "transcript": transcript,
            }
        )


def build_chatbot(responses=None, sleep=0):
    model = FakeListChatModel(responses=responses or ["你好。这是一段语音回答。"], sleep=sleep)

    @entrypoint(checkpointer=MemorySaver())
    async def chatbot(inputs, *, previous, config):
        messages = (previous or {}).get("messages", []) + inputs["messages"]
        response = await model.ainvoke(messages, config)
        return entrypoint.final(
            value={"messages": [response]}, save={"messages": messages + [response]}
        )

    return chatbot


@pytest.fixture
def voice_env(monkeypatch):
    from service import agui, service
    from service import voice as voice_routes

    repo = MemoryRepository()
    agents = {"chatbot": build_chatbot()}
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("test-admin-secret"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 48))
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "VOICE_REALTIME_VOICES",
        [
            VoiceOption(id="Cherry", name="芊悦", description="阳光积极、亲切自然"),
            VoiceOption(id="Serena", name="苏瑶", description="温柔自然"),
        ],
    )
    monkeypatch.setattr(settings, "VOICE_SESSION_SECONDS", 60)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", FakeSpeech)
    for module in (service, voice_routes, agui):
        monkeypatch.setattr(module, "get_agent", lambda name: agents[name])
    monkeypatch.setattr(app.state, "voice_repository", repo, raising=False)
    monkeypatch.setattr(app.state, "voice_connections", {}, raising=False)
    client = TestClient(app)

    def headers(user="alice"):
        response = client.post(
            "/auth/token",
            json={"user_id": user},
            headers={"Authorization": "Bearer test-admin-secret"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

    yield client, repo, agents, headers
    client.close()


def create(client, headers, **body):
    response = client.post("/voice/sessions", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def event(ws, kind, **data):
    ws.send_json({"type": kind, "event_id": str(uuid4()), **data})


def read_until(ws, kind):
    seen = []
    for _ in range(2000):
        raw = ws.receive()
        if raw.get("bytes") is not None:
            seen.append({"type": "audio", "frame": decode_audio(raw["bytes"])})
        elif raw.get("text") is not None:
            item = json.loads(raw["text"])
            seen.append(item)
            if item["type"] == kind:
                return seen
            assert item["type"] != "session.closed", item
        else:
            pytest.fail(str(raw))
    pytest.fail(f"Missing event: {kind}")


def connect(client, session, headers):
    return client.websocket_connect(f"/voice/sessions/{session['session_id']}/ws", headers=headers)


def configure(ws):
    event(ws, "session.configure")
    return read_until(ws, "session.ready")[-1]


def test_voice_roundtrip_streams_and_persists(voice_env):
    client, repo, agents, headers = voice_env
    auth = headers()
    session = create(client, auth)
    assert session["voice"] == "Cherry"
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        frame = encode_audio(b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, 0, kind=1)
        ws.send_bytes(frame)
        events = read_until(ws, "response.done")
        assert [e["text"] for e in events if e["type"] == "transcript.partial"] == ["你好"]
        assert sum(e["type"] == "transcript.final" for e in events) == 1
        audio = [e for e in events if e["type"] == "audio"]
        assert audio and all(e["frame"][1] == ready["connection_id"] for e in audio)
        assert (
            "".join(e["text"] for e in events if e["type"] == "text.delta")
            == "你好。这是一段语音回答。"
        )
        response_id = events[-1]["response_id"]
        event(ws, "playback.finished", response_id=response_id)
        event(ws, "ping")
        read_until(ws, "pong")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert len(repo.records) == 1
    turn = next(iter(repo.records.values()))
    assert turn.status == "completed"
    assert turn.audio_status == "completed"
    assert all(s.played_samples == s.samples > 0 for s in turn.segments)
    history = client.post(
        "/chatbot/history", json={"thread_id": session["thread_id"]}, headers=auth
    )
    assert history.status_code == 200
    assert [m["type"] for m in history.json()["data"]["messages"]] == ["human", "ai"]


@pytest.mark.parametrize(
    ("status", "interrupted", "error_code", "audio_status"),
    [
        ("completed", False, None, "completed"),
        ("failed", False, "agent_failed", "completed"),
        ("completed", False, "tts_backpressure", "failed"),
        ("cancelled", True, None, "cancelled"),
    ],
)
def test_legacy_voice_turn_infers_audio_status(status, interrupted, error_code, audio_status):
    turn = VoiceTurn.model_validate(
        {
            "session_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "input_id": str(uuid4()),
            "input_text": "legacy",
            "status": status,
            "execution_status": status,
            "interrupted": interrupted,
            "error_code": error_code,
        }
    )

    assert turn.audio_status == audio_status


def test_slow_tts_applies_backpressure_without_truncating_audio(voice_env, monkeypatch):
    from service import voice as voice_routes

    client, repo, agents, headers = voice_env
    response = "".join(f"这是第{index}句。" for index in range(1, 21))
    agents["chatbot"] = build_chatbot([response])
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", SlowFirstSpeech)
    auth = headers()
    session = create(client, auth)

    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="请完整朗读")
        events = read_until(ws, "response.done")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    done = events[-1]
    completed_segments = [item for item in events if item["type"] == "audio.segment.done"]
    assert "".join(item["text"] for item in events if item["type"] == "text.delta") == response
    assert len(completed_segments) == 20
    assert done["status"] == "completed"
    assert done["execution_status"] == "completed"
    assert done["audio_status"] == "completed"
    assert done["error_code"] is None
    assert not any(
        item["type"] == "error" and item.get("code") == "tts_backpressure" for item in events
    )
    turn = next(iter(repo.records.values()))
    assert len(turn.segments) == 20
    assert all(segment.complete and segment.samples > 0 for segment in turn.segments)


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_audio_starts_while_model_is_still_generating_and_reports_latency(
    voice_env, monkeypatch, enable_thinking
):
    import voice.session as voice_session
    from service import voice as voice_routes
    from service.agent_runner import AgentEvent

    client, _, _, headers = voice_env
    signals = []
    overlapped = []

    class SignalingSpeech(FakeSpeech):
        def __init__(self, config):
            super().__init__(config)
            self.emitted = asyncio.Event()
            signals.append(self.emitted)

        async def synthesize(self, ws, text):
            yield b"\0\0" * 480
            self.emitted.set()

    async def stalled_graph(*args, **kwargs):
        assert args[1]["config"]["configurable"]["voice_enable_thinking"] is enable_thinking
        yield AgentEvent("token", "我在认真听", "message-1")
        try:
            await asyncio.wait_for(signals[0].wait(), timeout=1)
        except TimeoutError:
            overlapped.append(False)
        else:
            overlapped.append(True)
        yield AgentEvent("token", "，你慢慢说。", "message-1")

    monkeypatch.setattr(voice_routes, "AlibabaRealtime", SignalingSpeech)
    monkeypatch.setattr(settings, "VOICE_LLM_ENABLE_THINKING", enable_thinking)
    monkeypatch.setattr(voice_session, "graph_events", stalled_graph)
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="测试边生成边播报")
        events = read_until(ws, "response.done")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert overlapped == [True]
    first_audio = next(index for index, item in enumerate(events) if item["type"] == "audio")
    last_text = max(index for index, item in enumerate(events) if item["type"] == "text.delta")
    assert first_audio < last_text
    assert (
        "".join(item["text"] for item in events if item["type"] == "text.delta")
        == "我在认真听，你慢慢说。"
    )
    latency = events[-1]["latency_ms"]
    assert 0 <= latency["first_text_ms"] <= latency["first_speech_ms"]
    assert latency["first_speech_ms"] <= latency["first_audio_ms"] <= latency["first_audio_sent_ms"]
    assert latency["text_to_audio_ms"] >= 0


def test_tts_failure_finishes_without_deadlock_or_false_success(voice_env, monkeypatch):
    from service import voice as voice_routes

    client, repo, agents, headers = voice_env
    agents["chatbot"] = build_chatbot(["".join(f"这是第{index}句。" for index in range(1, 21))])
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", FailingSpeech)
    auth = headers()
    session = create(client, auth)

    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="测试语音失败")
        events = read_until(ws, "response.done")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    done = events[-1]
    error = next(item for item in events if item["type"] == "error")
    assert error["response_id"] == done["response_id"]
    assert error["code"] == "tts_failed"
    assert done["status"] == "failed"
    assert done["execution_status"] == "completed"
    assert done["audio_status"] == "failed"
    assert done["error_code"] == "tts_failed"
    turn = next(iter(repo.records.values()))
    assert turn.status == "failed"
    assert turn.execution_status == "completed"
    assert turn.audio_status == "failed"


def test_voice_capabilities_expose_compatible_ids_and_display_metadata(voice_env):
    client, _, _, headers = voice_env
    auth = headers()

    response = client.get("/voice/capabilities", headers=auth)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["voices"] == ["Cherry", "Serena"]
    assert data["default_voice"] == "Cherry"
    assert data["voice_options"] == [
        {"id": "Cherry", "name": "芊悦", "description": "阳光积极、亲切自然"},
        {"id": "Serena", "name": "苏瑶", "description": "温柔自然"},
    ]
    assert data["wake_word"]["enabled"] is True
    assert data["wake_word"]["keyword"] == "小美小美"
    assert data["client_vad"]["enabled"] is True
    assert data["audio_metrics_seconds"] > 0


@pytest.mark.parametrize(
    "transcript",
    [
        "小美小美介绍一下你自己",
        "小美，小美，介绍一下你自己",
        "小 美！小 美！小美小美，介绍一下你自己",
    ],
)
def test_wake_session_validates_metadata_and_confirms_transcript(
    voice_env, monkeypatch, transcript
):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(WakeSpeech, "transcript", transcript)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", WakeSpeech)
    auth = headers()
    rejected = client.post(
        "/voice/sessions",
        headers=auth,
        json={"activation": "wake_word", "wake_word": "错误唤醒词"},
    )
    assert rejected.status_code == 422

    session = create(
        client,
        auth,
        activation="wake_word",
        wake_word="小美",
        wake_engine="sherpa-onnx-1.13.8",
        pre_roll_samples=16000,
    )
    assert session["wake_status"] == "pending"
    assert session["wake_word"] == "小美小美"
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        frame = encode_audio(
            b"\0\0" * 320,
            ready["connection_id"],
            str(ZERO_UUID),
            0,
            0,
            kind=1,
        )
        ws.send_bytes(frame)
        events = read_until(ws, "response.done")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert any(item["type"] == "wake.accepted" for item in events)
    assert (
        next(item for item in events if item["type"] == "wake.accepted")["command"]
        == "介绍一下你自己"
    )
    output = "".join(item["text"] for item in events if item["type"] == "text.delta")
    assert output.startswith(settings.VOICE_WAKE_GREETING)
    turn = next(iter(repo.records.values()))
    assert turn.input_text == "介绍一下你自己"
    assert repo.sessions[session["session_id"]].wake_status == "accepted"


@pytest.mark.parametrize("transcript", ["小美小美", "小美，小美，小美！", "小美，小美，嗯"])
def test_wake_without_command_only_speaks_greeting(voice_env, monkeypatch, transcript):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(WakeSpeech, "transcript", transcript)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", WakeSpeech)
    auth = headers()
    session = create(
        client,
        auth,
        activation="wake_word",
        wake_word="小美",
        wake_engine="sherpa-onnx-1.13.8",
    )
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        ws.send_bytes(
            encode_audio(
                b"\0\0" * 320,
                ready["connection_id"],
                str(ZERO_UUID),
                0,
                0,
                kind=1,
            )
        )
        events = read_until(ws, "response.done")
        assert sum(item["type"] == "wake.accepted" for item in events) == 1
        assert any(item["type"] == "audio" for item in events)
        assert (
            "".join(item["text"] for item in events if item["type"] == "text.delta")
            == settings.VOICE_WAKE_GREETING
        )
        event(ws, "playback.finished", response_id=events[-1]["response_id"])
        event(ws, "ping")
        read_until(ws, "pong")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert len(repo.records) == 1
    turn = next(iter(repo.records.values()))
    assert turn.input_text == ""
    assert turn.run_id is None
    assert turn.audio_status == "completed"
    assert repo.sessions[session["session_id"]].wake_status == "accepted"


def test_split_wake_and_extra_calls_greet_once_and_preserve_following_command(
    voice_env, monkeypatch
):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", SegmentedWakeSpeech)
    auth = headers()
    session = create(client, auth, activation="wake_word", wake_word="小美，小美")
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        all_events = []
        for sequence in range(4):
            ws.send_bytes(
                encode_audio(
                    b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, sequence, kind=1
                )
            )
            events = read_until(ws, "response.done" if sequence in {1, 3} else "transcript.final")
            event(ws, "ping")
            events.extend(read_until(ws, "pong"))
            all_events.extend(events)
            if sequence == 0:
                assert not any(e["type"] == "wake.accepted" for e in events)
                assert repo.records == {}
            if sequence == 2:
                assert next(e for e in events if e["type"] == "transcript.final")["text"] == ""
                assert not any(
                    e["type"] in {"response.cancelled", "input.accepted"} for e in events
                )
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert sum(e["type"] == "wake.accepted" for e in all_events) == 1
    assert (
        sum(
            e["type"] == "text.delta" and e.get("text") == settings.VOICE_WAKE_GREETING
            for e in all_events
        )
        == 1
    )
    assert [turn.input_text for turn in repo.records.values()] == ["", "介绍一下你自己"]


@pytest.mark.parametrize("transcript", [None, "小美"])
def test_wake_confirmation_timeout_closes_without_greeting(voice_env, monkeypatch, transcript):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(settings, "VOICE_WAKE_CONFIRM_SECONDS", 2)
    monkeypatch.setattr(WakeSpeech, "transcript", transcript)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", WakeSpeech)
    auth = headers()
    session = create(client, auth, activation="wake_word", wake_word="小美小美")
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        if transcript is not None:
            ws.send_bytes(
                encode_audio(b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, 0, kind=1)
            )
        events = read_until(ws, "wake.rejected")
    assert not any(e["type"] in {"wake.accepted", "response.started", "audio"} for e in events)
    assert repo.records == {}


def test_wake_greeting_tts_failure_keeps_session_usable(voice_env, monkeypatch):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(WakeSpeech, "transcript", "小美小美")
    monkeypatch.setattr(WakeSpeech, "synthesize", FailingSpeech.synthesize)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", WakeSpeech)
    auth = headers()
    session = create(client, auth, activation="wake_word", wake_word="小美小美")
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        ws.send_bytes(
            encode_audio(b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, 0, kind=1)
        )
        events = read_until(ws, "response.done")
        assert events[-1]["audio_status"] == "failed"
        assert any(e.get("code") == "tts_failed" for e in events)
        event(ws, "ping")
        read_until(ws, "pong")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert next(iter(repo.records.values())).execution_status == "completed"


@pytest.mark.parametrize("filler", ["嗯", "呢", "那"])
def test_playback_echo_filler_does_not_cancel_or_start_a_turn(voice_env, monkeypatch, filler):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(EchoFillerSpeech, "second_transcript", filler)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", EchoFillerSpeech)
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        for sequence in range(2):
            ws.send_bytes(
                encode_audio(
                    b"\0\0" * 320,
                    ready["connection_id"],
                    str(ZERO_UUID),
                    0,
                    sequence,
                    kind=1,
                )
            )
            events = read_until(ws, "response.done" if sequence == 0 else "transcript.final")
            if sequence == 0:
                response_id = events[-1]["response_id"]
            else:
                assert events[-1]["text"] == ""
                assert not any(item["type"] == "response.cancelled" for item in events)
        event(ws, "ping")
        events = read_until(ws, "pong")
        assert not any(item["type"] == "response.cancelled" for item in events)
        event(ws, "playback.finished", response_id=response_id)
        event(ws, "ping")
        read_until(ws, "pong")
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert len(repo.records) == 1
    assert next(iter(repo.records.values())).response_id == response_id
    assert not next(iter(repo.records.values())).interrupted


def test_meaningful_speech_still_interrupts_playback(voice_env, monkeypatch):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(EchoFillerSpeech, "second_transcript", "请暂停播放")
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", EchoFillerSpeech)
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        for sequence in range(2):
            ws.send_bytes(
                encode_audio(
                    b"\0\0" * 320,
                    ready["connection_id"],
                    str(ZERO_UUID),
                    0,
                    sequence,
                    kind=1,
                )
            )
            events = read_until(ws, "response.done")
            if sequence == 0:
                first_response_id = events[-1]["response_id"]
            else:
                assert any(
                    item["type"] == "response.cancelled"
                    and item["response_id"] == first_response_id
                    for item in events
                )
        event(ws, "session.close")
        read_until(ws, "session.closed")

    assert len(repo.records) == 2
    assert any(turn.input_text == "请暂停播放" for turn in repo.records.values())
    assert any(
        turn.response_id == first_response_id and turn.interrupted for turn in repo.records.values()
    )


def test_rejected_wake_closes_session_and_releases_user_capacity(voice_env, monkeypatch):
    from service import voice as voice_routes

    client, repo, _, headers = voice_env
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", RejectedWakeSpeech)
    auth = headers()
    session = create(
        client,
        auth,
        activation="wake_word",
        wake_word="小美",
        wake_engine="sherpa-onnx-1.13.8",
        pre_roll_samples=16000,
    )

    with connect(client, session, auth) as ws:
        ready = configure(ws)
        ws.send_bytes(
            encode_audio(
                b"\0\0" * 320,
                ready["connection_id"],
                str(ZERO_UUID),
                0,
                0,
                kind=1,
            )
        )
        events = read_until(ws, "wake.rejected")

    assert any(item["type"] == "wake.rejected" for item in events)
    deadline = monotonic() + 2
    while repo.sessions[session["session_id"]].status != "closed" and monotonic() < deadline:
        sleep(0.01)
    rejected = repo.sessions[session["session_id"]]
    assert rejected.wake_status == "rejected"
    assert rejected.status == "closed"
    assert asyncio.run(repo.active_sessions(rejected.user_id)) == 0


def test_audio_metrics_are_aggregated_on_session(voice_env):
    client, repo, _, headers = voice_env
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        configure(ws)
        event(
            ws,
            "audio.metrics",
            frames=125,
            rms_dbfs=-28.5,
            peak_dbfs=-4.0,
            clipped_samples=2,
            aec_enabled=True,
            noise_suppression_enabled=True,
            mode="conversation",
        )
        event(ws, "session.close")
        read_until(ws, "session.closed")

    quality = repo.sessions[session["session_id"]].audio_quality
    assert quality.reports == 1
    assert quality.frames == 125
    assert quality.average_rms_dbfs == -28.5
    assert quality.peak_dbfs == -4.0
    assert quality.clipped_samples == 2
    assert quality.aec_enabled is True
    assert quality.noise_suppression_enabled is True


def test_voice_session_accepts_configured_voice_and_rejects_unknown_voice(voice_env):
    client, _, _, headers = voice_env
    auth = headers()

    selected = create(client, auth, voice="Serena")
    rejected = client.post("/voice/sessions", headers=auth, json={"voice": "Unknown"})

    assert selected["voice"] == "Serena"
    assert rejected.status_code == 422
    assert rejected.json()["message"] == "Unsupported voice"


def test_voice_session_fails_cleanly_when_no_voice_is_configured(voice_env, monkeypatch):
    client, _, _, headers = voice_env
    auth = headers()
    monkeypatch.setattr(settings, "VOICE_REALTIME_VOICES", [])

    response = client.post("/voice/sessions", headers=auth, json={})

    assert response.status_code == 503
    assert response.json()["message"] == "No realtime voices are configured"


def test_tokens_bind_user_and_protect_every_thread_entry(voice_env):
    client, repo, agents, headers = voice_env
    alice, bob = headers(), headers("bob")
    session = create(client, alice)
    assert client.post("/voice/sessions", headers=alice, json={"user_id": "bob"}).status_code == 403
    assert client.get(f"/voice/sessions/{session['session_id']}", headers=bob).status_code == 403
    for route in ("history", "invoke", "stream"):
        body = {"thread_id": session["thread_id"]}
        if route != "history":
            body["message"] = "steal"
        assert client.post(f"/chatbot/{route}", headers=bob, json=body).status_code == 403
    assert (
        client.get("/chatbot/threads", params={"user_id": "alice"}, headers=bob).status_code == 403
    )
    assert client.post("/auth/token", headers=bob, json={"user_id": "alice"}).status_code == 403
    run = client.post("/chatbot/invoke", headers=alice, json={"message": "hello"}).json()["data"]
    assert (
        client.post(
            "/feedback",
            headers=bob,
            json={"run_id": run["run_id"], "key": "rating", "score": 1},
        ).status_code
        == 403
    )
    with pytest.raises(WebSocketDisconnect) as caught:
        with connect(client, session, bob):
            pass
    assert caught.value.code == 4403
    response = client.post(
        "/agui/chatbot/run",
        headers=bob,
        json={
            "threadId": session["thread_id"],
            "runId": "r",
            "messages": [],
            "tools": [],
            "context": [],
            "state": {},
            "forwardedProps": {},
        },
    )
    assert response.status_code == 403


def test_cancel_invalidates_audio_and_recovers_functional_history(voice_env):
    client, repo, agents, headers = voice_env
    agents["chatbot"] = build_chatbot(["第一句话。" + "后续内容" * 100, "新的回答。"], sleep=0.003)
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="第一个问题")
        first = read_until(ws, "audio.segment.done")
        old_id = first[-1]["response_id"]
        event(
            ws,
            "playback.progress",
            response_id=old_id,
            segment_index=1,
            played_samples=first[-1]["samples"],
        )
        event(ws, "response.cancel", response_id=old_id)
        read_until(ws, "response.cancelled")
        event(ws, "input.text", text="第二个问题")
        seen = read_until(ws, "response.done")
        if seen[-1]["response_id"] == old_id:
            seen += read_until(ws, "response.done")
        assert all(e["frame"][2] != old_id for e in seen if e["type"] == "audio")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    turns = list(repo.records.values())
    assert turns[0].interrupted and turns[0].execution_status == "cancelled"
    assert turns[0].audio_status == "cancelled"
    history = client.post(
        "/chatbot/history", headers=auth, json={"thread_id": session["thread_id"]}
    ).json()["data"]
    contents = [m["content"] for m in history["messages"]]
    assert "第一个问题" in contents and "第二个问题" in contents
    assert any("第一句话。" in c and "语音播放记录" in c for c in contents)


def test_reconnect_deduplicates_text_and_rejects_second_socket(voice_env):
    client, repo, agents, headers = voice_env
    auth = headers()
    session = create(client, auth)
    message = {"type": "input.text", "event_id": "stable-input", "text": "只执行一次"}
    with connect(client, session, auth) as ws:
        configure(ws)
        with pytest.raises(WebSocketDisconnect) as caught:
            with connect(client, session, auth):
                pass
        assert caught.value.code == 4409
        ws.send_json(message)
        read_until(ws, "response.done")
    with connect(client, session, auth) as ws:
        configure(ws)
        ws.send_json(message)
        assert read_until(ws, "input.accepted")[-1]["duplicate"]
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert len(repo.records) == 1


def test_business_interrupt_requires_explicit_approval(voice_env):
    client, repo, agents, headers = voice_env

    async def ask(state):
        answer = interrupt("确认执行？")
        return {"messages": [AIMessage(content=f"已确认：{answer}")]}

    graph = StateGraph(MessagesState)
    graph.add_node("ask", ask)
    graph.add_edge("__start__", "ask")
    graph.add_edge("ask", END)
    agents["approval"] = graph.compile(checkpointer=MemorySaver())
    auth = headers()
    session = create(client, auth, agent_id="approval")
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="开始")
        events = read_until(ws, "response.done")
        required = next(e for e in events if e["type"] == "approval.required")
        event(ws, "input.text", text="这不是确认")
        assert read_until(ws, "error")[-1]["code"] == "approval_required"
        event(ws, "approval.submit", interrupt_id=required["interrupt_id"], value="同意")
        events = read_until(ws, "response.done")
        assert "同意" in "".join(e["text"] for e in events if e["type"] == "text.delta")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert len(repo.records) == 2


@pytest.mark.parametrize("bad_frame", [b"bad", b"VCE1" + b"\0" * 44])
def test_invalid_binary_closes_session(voice_env, bad_frame):
    client, repo, agents, headers = voice_env
    auth = headers()
    session = create(client, auth)
    with connect(client, session, auth) as ws:
        configure(ws)
        ws.send_bytes(bad_frame)
        assert read_until(ws, "session.closed")[-1]["reason"] == "invalid_protocol"


def test_expired_session_cannot_connect(voice_env):
    client, repo, agents, headers = voice_env
    auth = headers()
    session = create(client, auth)
    repo.sessions[session["session_id"]].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(WebSocketDisconnect) as caught:
        with connect(client, session, auth):
            pass
    assert caught.value.code == 4410


@pytest.mark.asyncio
async def test_voice_lifespan_requires_both_backend_secrets(monkeypatch):
    from service.voice import voice_lifespan

    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("admin"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", None)
    with pytest.raises(ValueError, match="APP_TOKEN_SECRET"):
        async with voice_lifespan(FastAPI()):
            pass

    monkeypatch.setattr(settings, "AUTH_SECRET", None)
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 48))
    with pytest.raises(ValueError, match="AUTH_SECRET"):
        async with voice_lifespan(FastAPI()):
            pass


def test_mem0_agent_shares_text_and_voice_memory(voice_env, monkeypatch):
    import importlib
    from unittest.mock import AsyncMock, Mock

    module = importlib.import_module("agents.mem0_chatbot")
    client, repo, agents, headers = voice_env
    memory = Mock(search=AsyncMock(return_value=["喜欢茶"]), remember=AsyncMock())
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(
        module,
        "get_model",
        lambda _, **kwargs: FakeListChatModel(
            responses=["记得你喜欢茶。", "记得你喜欢茶。"], sleep=0.001
        ),
    )
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    agents["mem0-chatbot"] = graph
    auth = headers()
    response = client.post("/mem0-chatbot/invoke", headers=auth, json={"message": "我喜欢茶"})
    assert response.status_code == 200
    session = create(client, auth, agent_id="mem0-chatbot")
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="我喜欢什么？")
        seen = read_until(ws, "response.done")
        assert "".join(e["text"] for e in seen if e["type"] == "text.delta") == "记得你喜欢茶。"
        assert any(e["type"] == "audio" for e in seen)
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert [call.args[1] for call in memory.search.await_args_list] == ["alice", "alice"]
    assert [call.args[0] for call in memory.remember.await_args_list] == [
        [{"role": "user", "content": "我喜欢茶"}],
        [{"role": "user", "content": "我喜欢什么？"}],
    ]
    capabilities = client.get("/voice/capabilities", headers=auth).json()["data"]
    agent = next(a for a in capabilities["agents"] if a["id"] == "mem0-chatbot")
    assert agent["speech_mode"] == "streaming" and agent["cancel_execution"]


@pytest.mark.parametrize("agent_id", ["mem0-chatbot"])
def test_mem0_cancel_invalidates_audio(voice_env, agent_id):
    client, repo, agents, headers = voice_env
    agents[agent_id] = build_chatbot(["第一句话。" + "后续内容" * 100, "新的回答。"], sleep=0.003)
    auth = headers()
    session = create(client, auth, agent_id=agent_id)
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="第一个问题")
        first = read_until(ws, "audio.segment.done")
        old_id = first[-1]["response_id"]
        event(ws, "response.cancel", response_id=old_id)
        read_until(ws, "response.cancelled")
        event(ws, "input.text", text="第二个问题")
        seen = read_until(ws, "response.done")
        if seen[-1]["response_id"] == old_id:
            seen += read_until(ws, "response.done")
        assert all(e["frame"][2] != old_id for e in seen if e["type"] == "audio")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    turns = list(repo.records.values())
    assert turns[0].interrupted and turns[0].execution_status == "cancelled"


@pytest.mark.parametrize(
    "action,confirmation,status",
    [
        ("update", "确认修改", "updated"),
        ("delete", "确认删除", "deleted"),
        ("delete", "取消", "cancelled"),
        ("delete", "确认删除", "operation_failed"),
    ],
)
def test_audio_transcripts_manage_memories_and_speak_results(
    voice_env, monkeypatch, action, confirmation, status
):
    import importlib

    from memory.mem0_service import Mem0Service
    from schema.memory import MemoryList, MemoryRecord
    from schema.memory_commands import MemoryMutationResult
    from service import voice as voice_routes

    module = importlib.import_module("agents.mem0_chatbot")
    client, repo, agents, headers = voice_env
    command = "把喜欢茶的记忆改成我喜欢咖啡" if action == "update" else "删除喜欢茶的记忆"

    class CommandSpeech(FakeSpeech):
        def __init__(self, config):
            super().__init__(config)
            self.utterances = 0

        async def append_audio(self, ws, pcm):
            self.utterances += 1
            item_id = f"speech-{self.utterances}"
            text = command if self.utterances == 1 else confirmation
            await self.events.put({"type": "input_audio_buffer.speech_started", "item_id": item_id})
            for _ in range(2):
                await self.events.put(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": item_id,
                        "transcript": text,
                    }
                )

    class PlanningModel(FakeListChatModel):
        def with_structured_output(self, schema, **kwargs):
            return self | RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )

    record = MemoryRecord(id=uuid4(), memory="我喜欢茶", user_id="alice", agent_id="mem0-chatbot")
    memory = AsyncMock(spec=Mem0Service)
    memory.search_memory_records.return_value = MemoryList(memories=[record])
    memory.apply_memory_mutation.return_value = MemoryMutationResult(
        status="updated" if action == "update" else "deleted", deleted_count=int(action == "delete")
    )
    if status == "operation_failed":
        memory.apply_memory_mutation.side_effect = TimeoutError("private-text")
    model = PlanningModel(
        responses=[
            json.dumps(
                {
                    "action": action,
                    "query": "喜欢茶",
                    "text": "我喜欢咖啡" if action == "update" else None,
                }
            ),
            '{"matching_indices":[1]}',
        ]
    )
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(module, "get_model", lambda _, **kwargs: model)
    monkeypatch.setattr(voice_routes, "AlibabaRealtime", CommandSpeech)
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    agents["mem0-chatbot"] = graph
    auth = headers()
    session = create(client, auth, agent_id="mem0-chatbot")
    with connect(client, session, auth) as ws:
        ready = configure(ws)
        ws.send_bytes(
            encode_audio(b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, 0, kind=1)
        )
        preview = read_until(ws, "response.done")
        spoken = "".join(item["text"] for item in preview if item["type"] == "text.delta")
        assert "我喜欢茶" in spoken and "请说确认" in spoken
        assert "matching_indices" not in spoken and '"action"' not in spoken
        assert any(item["type"] == "audio" for item in preview)
        memory.apply_memory_mutation.assert_not_awaited()
        event(ws, "playback.finished", response_id=preview[-1]["response_id"])
        ws.send_bytes(
            encode_audio(b"\0\0" * 320, ready["connection_id"], str(ZERO_UUID), 0, 1, kind=1)
        )
        result = read_until(ws, "response.done")
        spoken = "".join(item["text"] for item in result if item["type"] == "text.delta")
        assert any(item["type"] == "audio" for item in result)
        if status == "updated":
            assert "已更新" in spoken and "我喜欢咖啡" in spoken
        elif status == "deleted":
            assert "已删除" in spoken
        elif status == "cancelled":
            assert "已取消" in spoken
        else:
            assert "未能确认完成" in spoken and "已删除" not in spoken
        event(ws, "session.close")
        read_until(ws, "session.closed")
    assert len(repo.records) == 2
    if status == "cancelled":
        memory.apply_memory_mutation.assert_not_awaited()
    else:
        memory.apply_memory_mutation.assert_awaited_once()
        user_id, mutation = memory.apply_memory_mutation.await_args.args
        assert user_id == "alice" and mutation.target.id == record.id
    memory.remember.assert_not_awaited()


def test_cancelled_memory_preview_does_not_mutate_or_stream_planning_json(voice_env, monkeypatch):
    import importlib

    from memory.mem0_service import Mem0Service

    module = importlib.import_module("agents.mem0_chatbot")
    client, _, agents, headers = voice_env

    class SlowPlanningModel(FakeListChatModel):
        def with_structured_output(self, schema, **kwargs):
            return self | RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )

    model = SlowPlanningModel(responses=['{"action":"delete","query":"tea"}'], sleep=0.03)
    memory = AsyncMock(spec=Mem0Service)
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(module, "get_model", lambda _, **kwargs: model)
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    agents["mem0-chatbot"] = graph
    auth = headers()
    session = create(client, auth, agent_id="mem0-chatbot")
    with connect(client, session, auth) as ws:
        configure(ws)
        event(ws, "input.text", text="删除喜欢茶的记忆")
        started = read_until(ws, "response.started")
        old_id = started[-1]["response_id"]
        event(ws, "response.cancel", response_id=old_id)
        read_until(ws, "response.cancelled")
        event(ws, "input.text", text="确认删除")
        seen = read_until(ws, "response.done")
        if seen[-1]["response_id"] == old_id:
            seen += read_until(ws, "response.done")
        text = "".join(item["text"] for item in seen if item["type"] == "text.delta")
        assert "当前没有等待确认" in text
        assert '"action"' not in text
        assert all(item["frame"][2] != old_id for item in seen if item["type"] == "audio")
        event(ws, "session.close")
        read_until(ws, "session.closed")
    memory.apply_memory_mutation.assert_not_awaited()
