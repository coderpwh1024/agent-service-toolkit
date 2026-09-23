"""Concurrent audio, agent execution and playback for one mobile connection."""

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import ValidationError

from core.settings import Settings
from schema import StreamInput
from schema.voice import (
    ZERO_UUID,
    ApprovalInput,
    AudioSegment,
    CancelResponse,
    CloseSession,
    CommitInput,
    Configure,
    Ping,
    PlaybackFinished,
    PlaybackProgress,
    SpeechHint,
    TextInput,
    VoiceSession,
    VoiceTurn,
    client_event_adapter,
    decode_audio,
    encode_audio,
)
from service.agent_runner import graph_events, thread_guard
from service.utils import messages_from_checkpoint
from voice.persistence import VoiceRepository
from voice.providers.alibaba_realtime import AlibabaRealtime
from voice.speech_output import SpeechText

logger = logging.getLogger(__name__)


class VoiceConnection:
    def __init__(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        repo: VoiceRepository,
        agent: Any,
        provider: AlibabaRealtime,
        settings: Settings,
        expires_at: int | None = None,
    ):
        self.task: asyncio.Task | None = None
        self.ws = websocket
        self.session = session
        self.repo = repo
        self.agent = agent
        self.provider = provider
        self.settings = settings
        self.expires_at = expires_at
        self.connection_id = str(uuid4())
        self.closed = asyncio.Event()
        self.outgoing: asyncio.PriorityQueue = asyncio.PriorityQueue(settings.VOICE_QUEUE_SIZE)
        self.audio_in: asyncio.Queue[bytes | None] = asyncio.Queue(settings.VOICE_QUEUE_SIZE)
        self.inputs: asyncio.Queue = asyncio.Queue(2)
        self.sequence = 0
        self.audio_sequence = 0
        self.input_sequence = -1
        self.input_samples = 0
        self.started = time.monotonic()
        self.activity = self.started
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.turns: dict[str, VoiceTurn] = {}
        self.invalid: set[str] = set()
        self.current: VoiceTurn | None = None
        self.agent_task: asyncio.Task | None = None
        self.tts_task: asyncio.Task | None = None
        self.pending: dict[str, Any] = {}
        self.speech_hint_task: asyncio.Task | None = None
        self.close_reason = "disconnected"

    def remember(self, event_id: str) -> bool:
        if event_id in self.seen:
            return False
        self.seen[event_id] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        return True

    async def emit(self, event_type: str, *, urgent: bool = False, **data: Any) -> None:
        if self.closed.is_set():
            return
        self.sequence += 1
        event = {
            "type": event_type,
            "event_id": str(uuid4()),
            "sequence": self.sequence,
            "session_id": self.session.session_id,
            "connection_id": self.connection_id,
            **data,
        }
        await self.outgoing.put((0 if urgent else 10, self.sequence, event))

    async def emit_audio(self, pcm: bytes, turn: VoiceTurn, segment: AudioSegment) -> None:
        for offset in range(0, len(pcm), 4096):
            if turn.response_id in self.invalid or self.closed.is_set():
                return
            part = pcm[offset : offset + 4096]
            self.audio_sequence += 1
            self.sequence += 1
            frame = encode_audio(
                part,
                self.connection_id,
                turn.response_id,
                segment.index,
                self.audio_sequence,
            )
            await self.outgoing.put((10, self.sequence, (turn.response_id, frame)))
            segment.samples += len(part) // 2
            self.activity = time.monotonic()

    async def sender(self) -> None:
        while True:
            _, _, item = await self.outgoing.get()
            async with asyncio.timeout(5):
                if isinstance(item, tuple):
                    response_id, frame = item
                    if response_id not in self.invalid:
                        await self.ws.send_bytes(frame)
                else:
                    if item.get("response_id") in self.invalid and item["type"] in {
                        "text.delta",
                        "audio.segment.started",
                        "audio.segment.done",
                        "response.started",
                    }:
                        continue
                    await self.ws.send_json(item)

    async def cancel_response(self, response_id: str | None, reason: str) -> None:
        if not response_id or response_id in self.invalid:
            return
        turn = self.turns.get(response_id)
        if turn is None:
            return
        if (
            turn.segments
            and all(
                s.complete and s.samples > 0 and s.played_samples == s.samples
                for s in turn.segments
            )
            and turn.status == "completed"
        ):
            return
        self.invalid.add(response_id)
        turn.interrupted = True
        turn.status = "cancelled"
        turn.audio_status = "cancelled"
        if self.current is turn:
            if self.tts_task and not self.tts_task.done():
                self.tts_task.cancel()
            if (
                self.session.agent_id == "chatbot"
                and self.agent_task
                and not self.agent_task.done()
            ):
                self.agent_task.cancel()
        await self.emit(
            "response.cancelled",
            urgent=True,
            response_id=response_id,
            reason=reason,
            execution_status=turn.execution_status,
        )
        await self.repo.save_turn(turn)

    async def submit(self, text: str, input_id: str, resume: dict | None = None) -> None:
        if self.pending and resume is None:
            await self.emit("error", code="approval_required", recoverable=True)
            return
        if not text.strip():
            return
        if len(text) > 6000:
            await self.emit("error", code="input_too_long", recoverable=True)
            return
        if await self.repo.has_input(self.session.session_id, input_id):
            await self.emit("input.accepted", input_id=input_id, duplicate=True)
            return
        if self.inputs.full() or len(self.turns) >= 500:
            await self.emit("error", code="input_queue_full", recoverable=True)
            return
        await self.cancel_response(self.current.response_id if self.current else None, "new_input")
        turn = VoiceTurn(
            session_id=self.session.session_id,
            thread_id=self.session.thread_id,
            input_id=input_id,
            input_text=text.strip(),
        )
        await self.repo.save_turn(turn)
        self.turns[turn.response_id] = turn
        self.inputs.put_nowait((turn, resume))
        self.activity = time.monotonic()
        await self.emit(
            "input.accepted",
            input_id=input_id,
            turn_id=turn.turn_id,
            response_id=turn.response_id,
            duplicate=False,
        )

    async def restore_approvals(self) -> None:
        state = await self.agent.aget_state(
            RunnableConfig(configurable={"thread_id": self.session.thread_id})
        )
        self.pending = {
            item.id: item.value for task in state.tasks for item in getattr(task, "interrupts", ())
        }
        for interrupt_id, value in self.pending.items():
            await self.emit("approval.required", interrupt_id=interrupt_id, value=value)

    async def receiver(self) -> None:
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                kind, connection, response, segment, seq, pcm = decode_audio(message["bytes"])
                if (
                    kind != 1
                    or connection != self.connection_id
                    or response != str(ZERO_UUID)
                    or segment != 0
                    or seq != self.input_sequence + 1
                ):
                    raise ValueError("Invalid input audio sequence or connection")
                self.input_sequence = seq
                self.input_samples += len(pcm) // 2
                if self.input_samples > (time.monotonic() - self.started + 3) * 16000:
                    raise ValueError("Audio must be paced in realtime")
                async with asyncio.timeout(2):
                    await self.audio_in.put(pcm)
                continue
            text = message.get("text", "")
            if len(text.encode()) > 16384:
                raise ValueError("Control event is too large")
            try:
                event = client_event_adapter.validate_json(text)
            except ValidationError:
                await self.emit("error", code="invalid_event", recoverable=True)
                continue
            if not self.remember(event.event_id):
                await self.emit("event.ack", client_event_id=event.event_id, duplicate=True)
                continue
            match event:
                case TextInput():
                    await self.submit(event.text, event.event_id)
                case CommitInput():
                    if self.session.turn_detection != "manual":
                        await self.emit(
                            "error", code="commit_requires_manual_mode", recoverable=True
                        )
                    else:
                        async with asyncio.timeout(2):
                            await self.audio_in.put(None)
                case CancelResponse():
                    await self.cancel_response(str(event.response_id), "client")
                case SpeechHint():
                    if self.speech_hint_task:
                        self.speech_hint_task.cancel()
                    self.speech_hint_task = asyncio.create_task(self.resume_after_hint())
                case PlaybackProgress():
                    turn = self.turns.get(str(event.response_id))
                    if turn and event.segment_index <= len(turn.segments):
                        segment = turn.segments[event.segment_index - 1]
                        if segment.played_samples <= event.played_samples <= segment.samples:
                            segment.played_samples = event.played_samples
                            if segment.complete and segment.samples == segment.played_samples:
                                await self.repo.save_turn(turn)
                case PlaybackFinished():
                    turn = self.turns.get(str(event.response_id))
                    if (
                        turn
                        and turn.status == "completed"
                        and all(s.complete for s in turn.segments)
                    ):
                        for segment in turn.segments:
                            segment.played_samples = segment.samples
                        await self.repo.save_turn(turn)
                case ApprovalInput():
                    if event.interrupt_id not in self.pending:
                        await self.emit("error", code="stale_approval", recoverable=True)
                    else:
                        await self.submit(
                            "[用户提交业务确认]", event.event_id, {event.interrupt_id: event.value}
                        )
                        self.pending.pop(event.interrupt_id, None)
                case Ping():
                    await self.emit("pong", client_event_id=event.event_id)
                case CloseSession():
                    self.close_reason = "client_closed"
                    return
                case Configure():
                    await self.emit("error", code="already_configured", recoverable=True)

    async def resume_after_hint(self) -> None:
        response_id = self.current.response_id if self.current else None
        await asyncio.sleep(0.8)
        if response_id and response_id not in self.invalid:
            await self.emit("playback.resume", response_id=response_id)

    async def upload(self, asr: Any) -> None:
        while True:
            pcm = await self.audio_in.get()
            if pcm is None:
                await self.provider.commit_audio(asr)
            else:
                await self.provider.append_audio(asr, pcm)

    async def recognize(self, asr: Any) -> None:
        while True:
            event = await self.provider.receive(asr, timeout=False)
            item_id = event.get("item_id", event.get("event_id", ""))
            match event.get("type"):
                case "input_audio_buffer.speech_started":
                    self.activity = time.monotonic()
                    await self.emit("input.started", input_id=item_id)
                    await self.cancel_response(
                        self.current.response_id if self.current else None, "speech"
                    )
                case "input_audio_buffer.speech_stopped":
                    await self.emit("input.ended", input_id=item_id)
                case "conversation.item.input_audio_transcription.text":
                    await self.emit(
                        "transcript.partial",
                        input_id=item_id,
                        text=event.get("text", "") + event.get("stash", ""),
                    )
                case "conversation.item.input_audio_transcription.completed":
                    input_id = f"{self.connection_id}:{item_id}"
                    if self.remember(input_id):
                        text = event.get("transcript", "")
                        await self.emit("transcript.final", input_id=input_id, text=text)
                        await self.submit(text, input_id)
                case "session.finished":
                    self.close_reason = "asr_finished"
                    return

    async def speak(self, turn: VoiceTurn, text_queue: asyncio.Queue[str | None]) -> None:
        try:
            async with self.provider.synthesizer(self.session) as tts:
                while True:
                    text = await text_queue.get()
                    if turn.response_id in self.invalid:
                        turn.audio_status = "cancelled"
                        return
                    if text is None:
                        turn.audio_status = "completed"
                        return
                    segment = AudioSegment(index=len(turn.segments) + 1, text=text)
                    turn.segments.append(segment)
                    await self.emit(
                        "audio.segment.started",
                        response_id=turn.response_id,
                        segment_index=segment.index,
                        text=text,
                    )
                    async for pcm in self.provider.synthesize(tts, text):
                        await self.emit_audio(pcm, turn, segment)
                    segment.complete = True
                    await self.emit(
                        "audio.segment.done",
                        response_id=turn.response_id,
                        segment_index=segment.index,
                        samples=segment.samples,
                    )
        except asyncio.CancelledError:
            turn.audio_status = "cancelled"
            raise
        except Exception:
            logger.exception("Voice synthesis failed for response %s", turn.response_id)
            turn.audio_status = "failed"
            turn.error_code = "tts_failed"
            await self.emit(
                "error", response_id=turn.response_id, code="tts_failed", recoverable=True
            )

    async def enqueue_speech(self, text_queue: asyncio.Queue[str | None], text: str | None) -> bool:
        tts_task = self.tts_task
        if tts_task is None or tts_task.done():
            return False
        put_task = asyncio.create_task(text_queue.put(text))
        try:
            done, _ = await asyncio.wait((put_task, tts_task), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            put_task.cancel()
            await asyncio.gather(put_task, return_exceptions=True)
            raise
        if put_task in done:
            return True
        put_task.cancel()
        await asyncio.gather(put_task, return_exceptions=True)
        return False

    async def generate(
        self,
        turn: VoiceTurn,
        resume: dict | None,
        text_queue: asyncio.Queue[str | None],
    ) -> None:
        from service.service import _handle_input

        speech = SpeechText()
        streamed: set[str | None] = set()
        final_ai = None
        token_mode = self.session.agent_id == "chatbot"

        async def output(text: str, *, final: bool = False):
            if turn.response_id in self.invalid:
                return
            if text:
                turn.generated_text += text
                await self.emit("text.delta", response_id=turn.response_id, text=text)
            for phrase in speech.feed(text, final=final):
                if not await self.enqueue_speech(text_queue, phrase):
                    break

        try:
            async with asyncio.timeout(180), thread_guard(self.repo, self.session.thread_id):
                data = StreamInput(
                    message=turn.input_text,
                    model=self.session.model,
                    thread_id=self.session.thread_id,
                    user_id=self.session.user_id,
                )
                kwargs, run_id = await _handle_input(
                    data, self.agent, self.session.agent_id, self.repo, auto_resume=False
                )
                turn.run_id = str(run_id)
                if resume is not None:
                    state = await self.agent.aget_state(kwargs["config"])
                    pending_ids = {
                        i.id for task in state.tasks for i in getattr(task, "interrupts", ())
                    }
                    if not set(resume) <= pending_ids:
                        raise ValueError("Stale approval")
                    kwargs["input"] = Command(resume=resume)
                else:
                    state = await self.agent.aget_state(kwargs["config"])
                    if any(getattr(task, "interrupts", ()) for task in state.tasks):
                        await self.restore_approvals()
                        turn.execution_status = "waiting_approval"
                        return
                    kwargs["input"]["messages"][-1].id = f"voice-input-{turn.turn_id}"
                await self.emit(
                    "response.started",
                    response_id=turn.response_id,
                    turn_id=turn.turn_id,
                    run_id=turn.run_id,
                )
                async for event in graph_events(
                    self.agent, kwargs, str(run_id), user_message=turn.input_text
                ):
                    if event.type == "interrupt":
                        self.pending[event.content["interrupt_id"]] = event.content["value"]
                        await self.emit("approval.required", **event.content)
                    elif event.type == "token" and token_mode and not event.namespace:
                        streamed.add(event.message_id)
                        await output(event.content)
                    elif event.type == "message":
                        message = event.content
                        if message.type == "ai":
                            for tool in message.tool_calls:
                                await self.emit(
                                    "tool.started",
                                    response_id=turn.response_id,
                                    tool_call_id=tool.get("id"),
                                    name=tool.get("name"),
                                )
                            if not message.tool_calls and not event.namespace:
                                final_ai = message
                                if token_mode and event.message_id not in streamed:
                                    await output(message.content)
                        elif message.type == "tool":
                            await self.emit(
                                "tool.finished",
                                response_id=turn.response_id,
                                tool_call_id=message.tool_call_id,
                            )
                        elif message.type == "custom":
                            await self.emit(
                                "agent.custom",
                                response_id=turn.response_id,
                                data=message.custom_data,
                            )
                    elif event.type == "error":
                        raise ValueError("Agent emitted an invalid message")
                if not token_mode and not self.pending:
                    saver = getattr(self.agent, "checkpointer", None)
                    if saver:
                        checkpoint = await saver.aget_tuple(kwargs["config"])
                        history = (
                            messages_from_checkpoint(checkpoint.checkpoint) if checkpoint else []
                        )
                        for message in reversed(history):
                            if isinstance(message, AIMessage) and not message.tool_calls:
                                if not message.additional_kwargs.get("voice_delivery"):
                                    from service.utils import langchain_to_chat_message

                                    final_ai = langchain_to_chat_message(message)
                                    break
                    if final_ai:
                        await output(final_ai.content)
                await output("", final=True)
                turn.execution_status = "waiting_approval" if self.pending else "completed"
        except asyncio.CancelledError:
            turn.execution_status = "cancelled"
            turn.interrupted = True
            raise
        except Exception:
            logger.exception("Voice agent run failed: %s", turn.turn_id)
            turn.execution_status = "failed"
            turn.error_code = "agent_failed"
            await self.emit(
                "error", response_id=turn.response_id, code="agent_failed", recoverable=True
            )
        finally:
            await self.repo.save_turn(turn)

    async def execute(self, turn: VoiceTurn, resume: dict | None) -> None:
        self.current = turn
        turn.status = "running"
        turn.execution_status = "running"
        turn.audio_status = "running"
        queue: asyncio.Queue[str | None] = asyncio.Queue(16)
        self.tts_task = asyncio.create_task(self.speak(turn, queue))
        self.agent_task = asyncio.create_task(self.generate(turn, resume, queue))
        try:
            await asyncio.shield(self.agent_task)
        except asyncio.CancelledError:
            if not self.agent_task.done():
                raise
        if self.tts_task and not self.tts_task.done():
            await self.enqueue_speech(queue, None)
        with contextlib.suppress(asyncio.CancelledError):
            await self.tts_task
        if not turn.interrupted:
            turn.status = (
                "failed"
                if turn.execution_status == "failed" or turn.audio_status == "failed"
                else turn.execution_status
            )
        await self.repo.save_turn(turn)
        await self.emit(
            "response.done",
            response_id=turn.response_id,
            status=turn.status,
            execution_status=turn.execution_status,
            audio_status=turn.audio_status,
            error_code=turn.error_code,
        )

    async def worker(self) -> None:
        while not self.closed.is_set():
            turn, resume = await self.inputs.get()
            await self.execute(turn, resume)

    async def timer(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = datetime.now(UTC)
            if now >= self.session.expires_at or (
                self.expires_at and now.timestamp() >= self.expires_at
            ):
                self.close_reason = "expired"
                return
            if time.monotonic() - self.activity > self.settings.VOICE_IDLE_SECONDS:
                if not self.agent_task or self.agent_task.done():
                    self.close_reason = "idle_timeout"
                    return

    async def stop(self, reason: str = "server_closed") -> None:
        self.close_reason = reason
        self.closed.set()

    async def run(self) -> None:
        tasks: list[asyncio.Task] = []
        worker = None
        try:
            async with asyncio.timeout(10):
                raw = await self.ws.receive_text()
                if len(raw.encode()) > 16384:
                    raise ValueError("Control event is too large")
                first = client_event_adapter.validate_json(raw)
                if not isinstance(first, Configure):
                    raise ValueError("session.configure must be the first event")
            self.remember(first.event_id)
            self.session.status = "connected"
            self.session.connection_id = self.connection_id
            await self.repo.save_session(self.session)
            async with self.provider.recognize(self.session) as asr:
                tasks = [
                    asyncio.create_task(f())
                    for f in (
                        self.sender,
                        self.receiver,
                        lambda: self.upload(asr),
                        lambda: self.recognize(asr),
                        self.timer,
                        self.closed.wait,
                    )
                ]
                worker = asyncio.create_task(self.worker())
                await self.emit(
                    "session.ready",
                    protocol_version=1,
                    input_format="pcm16_16000_mono",
                    output_format="pcm16_24000_mono",
                    audio_header_bytes=45,
                )
                await self.restore_approvals()
                done, _ = await asyncio.wait([*tasks, worker], return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ValueError, ValidationError):
            self.close_reason = "invalid_protocol"
        except Exception:
            logger.exception("Realtime voice connection failed: %s", self.session.session_id)
            self.close_reason = "upstream_error"
        finally:
            self.closed.set()
            for turn in self.turns.values():
                if turn.status in ("queued", "running") or any(
                    s.played_samples < s.samples for s in turn.segments
                ):
                    self.invalid.add(turn.response_id)
                    turn.interrupted = True
                    turn.status = "cancelled"
                    turn.audio_status = "cancelled"
                    if turn.execution_status == "queued":
                        turn.execution_status = "cancelled"
            if self.tts_task:
                self.tts_task.cancel()
            if self.agent_task and self.session.agent_id == "chatbot":
                self.agent_task.cancel()
            if self.speech_hint_task:
                self.speech_hint_task.cancel()
                tasks.append(self.speech_hint_task)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.ws.send_json(
                    {
                        "type": "session.closed",
                        "reason": self.close_reason,
                        "session_id": self.session.session_id,
                    }
                )
                await self.ws.close(
                    code=1000
                    if self.close_reason
                    in {"client_closed", "idle_timeout", "expired", "server_closed"}
                    else 1011
                )
            if self.agent_task:
                await asyncio.gather(self.agent_task, return_exceptions=True)
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if self.tts_task:
                await asyncio.gather(self.tts_task, return_exceptions=True)
            for turn in self.turns.values():
                await self.repo.save_turn(turn)
            self.session.status = (
                "closed"
                if self.close_reason in {"client_closed", "server_closed", "expired"}
                else "disconnected"
            )
            await self.repo.save_session(self.session)
