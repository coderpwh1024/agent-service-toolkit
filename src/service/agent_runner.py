"""Transport-independent LangGraph events for SSE and realtime voice."""

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from schema import ChatMessage
from service.utils import (
    convert_message_content_to_string,
    langchain_to_chat_message,
    messages_from_checkpoint,
    remove_tool_calls,
)
from voice.persistence import VoiceRepository


@dataclass
class AgentEvent:
    type: str
    content: Any
    message_id: str | None = None
    node: str = ""
    namespace: tuple[str, ...] = ()


def create_ai_message(parts: dict) -> AIMessage:
    valid_keys = set(inspect.signature(AIMessage).parameters)
    return AIMessage(**{k: v for k, v in parts.items() if k in valid_keys})


@asynccontextmanager
async def thread_guard(repo: VoiceRepository | None, thread_id: str):
    if repo:
        async with repo.guard(f"thread:{thread_id}"):
            yield
    else:
        yield


async def delivery_context(repo: VoiceRepository, agent: Any, thread_id: str) -> list:
    saver = getattr(agent, "checkpointer", None)
    checkpoint = (
        await saver.aget_tuple(RunnableConfig(configurable={"thread_id": thread_id}))
        if saver
        else None
    )
    existing = messages_from_checkpoint(checkpoint.checkpoint) if checkpoint else []
    ids = {m.id for m in existing}
    messages: list[BaseMessage] = []
    for turn in await repo.turns(thread_id):
        if not turn.interrupted or turn.execution_status == "running":
            continue
        input_id = f"voice-input-{turn.turn_id}"
        if turn.execution_status == "cancelled" and input_id not in ids:
            messages.append(HumanMessage(content=turn.input_text, id=input_id))
        note_id = f"voice-delivery-{turn.response_id}"
        if note_id not in ids:
            spoken = "".join(
                s.text
                for s in turn.segments
                if s.complete and s.samples > 0 and s.played_samples >= s.samples
            )
            messages.append(
                AIMessage(
                    id=note_id,
                    content="[语音播放记录：上次回答已中断。客户端确认完整播放的内容："
                    + (spoken or "无")
                    + "。其余内容未确认听完，后续对话不要假设用户已经听到。]",
                    additional_kwargs={"voice_delivery": True},
                )
            )
    return messages


async def graph_events(
    agent: Any,
    kwargs: dict[str, Any],
    run_id: str,
    *,
    stream_tokens: bool = True,
    user_message: str = "",
) -> AsyncIterator[AgentEvent]:
    async for stream_event in agent.astream(
        **kwargs, stream_mode=["updates", "messages", "custom"], subgraphs=True
    ):
        if not isinstance(stream_event, tuple):
            continue
        if len(stream_event) == 3:
            namespace, mode, event = stream_event
        else:
            mode, event = stream_event
            namespace = ()
        if mode == "messages":
            if not stream_tokens:
                continue
            msg, metadata = event
            if "skip_stream" in metadata.get("tags", []) or not isinstance(msg, AIMessageChunk):
                continue
            content = remove_tool_calls(msg.content)
            if content:
                yield AgentEvent(
                    "token",
                    convert_message_content_to_string(content),
                    msg.id,
                    metadata.get("langgraph_node", ""),
                    namespace,
                )
            continue
        batches: list[tuple[str, list]] = []
        if mode == "updates":
            for node, updates in event.items():
                if node == "__interrupt__":
                    for interrupt in updates:
                        yield AgentEvent(
                            "interrupt",
                            {
                                "interrupt_id": interrupt.id,
                                "value": interrupt.value,
                            },
                            node=node,
                            namespace=namespace,
                        )
                    continue
                update_messages = (updates or {}).get("messages", [])
                if "supervisor" in node or "sub-agent" in node:
                    if update_messages and isinstance(update_messages[-1], ToolMessage):
                        update_messages = (
                            update_messages[-2:] if "sub-agent" in node else update_messages[-1:]
                        )
                    else:
                        update_messages = []
                batches.append((node, update_messages))
        elif mode == "custom":
            batches.append(("", [event]))
        for node, messages in batches:
            normalized = []
            parts: dict[str, Any] = {}
            for message in messages:
                if isinstance(message, tuple):
                    key, value = message
                    parts[key] = value
                else:
                    if parts:
                        normalized.append(create_ai_message(parts))
                        parts = {}
                    normalized.append(message)
            if parts:
                normalized.append(create_ai_message(parts))
            for message in normalized:
                try:
                    chat_message: ChatMessage = langchain_to_chat_message(message)
                except Exception:
                    yield AgentEvent("error", "Unexpected message format")
                    continue
                chat_message.run_id = run_id
                if chat_message.type == "human" and chat_message.content == user_message:
                    continue
                yield AgentEvent(
                    "message", chat_message, getattr(message, "id", None), node, namespace
                )
