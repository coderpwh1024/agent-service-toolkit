"""A streaming chatbot with thread history and user-scoped long-term Mem0 facts."""

import json
import logging
from time import monotonic
from typing import Any, NotRequired, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.func import entrypoint
from pydantic import ValidationError

from agents.email_commands import handle_email_command
from agents.memory_commands import handle_memory_command
from core import get_model, settings
from memory.mem0_service import get_mem0_service
from schema.memory_commands import MemoryDialogue
from skills.video import handle_video_skill

logger = logging.getLogger(__name__)
CHAT_MESSAGE_LIMIT = 20


class Mem0ChatState(TypedDict):
    messages: list[BaseMessage]
    memory_dialogue: NotRequired[dict[str, Any]]
    email_dialogue: NotRequired[dict[str, Any] | None]
    video_dialogue: NotRequired[dict[str, Any] | None]


def _chat_prompt(messages: list[BaseMessage], facts: list[str], status: str) -> list[BaseMessage]:
    prefix: list[BaseMessage] = [
        SystemMessage(content=settings.mem0_prompts.chat_system),
        SystemMessage(content=settings.mem0_prompts.memory_status_template.format(status=status)),
    ]
    recent_messages = [
        message for message in messages if isinstance(message, (HumanMessage, AIMessage))
    ][-CHAT_MESSAGE_LIMIT:]
    if facts:
        prefix.append(
            HumanMessage(
                content=json.dumps({"untrusted_user_memories": facts}, ensure_ascii=False),
                name="retrieved_memories",
            )
        )
    return prefix + recent_messages


def _extraction_context(messages: list[BaseMessage]) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    remaining = settings.MEM0_MAX_CONTEXT_CHARS
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.response_metadata.get("memory_status") in {
            "updated",
            "unchanged",
            "deleted",
            "cleared",
        }:
            break
        if not isinstance(message, (HumanMessage, AIMessage)) or not isinstance(
            message.content, str
        ):
            continue
        if len(message.content) > remaining:
            break
        context.append(
            {
                "role": "user" if isinstance(message, HumanMessage) else "assistant",
                "content": message.content,
            }
        )
        remaining -= len(message.content)
        if len(context) == 10:
            break
    return list(reversed(context))


@entrypoint()
async def mem0_chatbot(
    inputs: dict[str, list[BaseMessage]],
    *,
    previous: Mem0ChatState | None,
    config: RunnableConfig,
) -> entrypoint.final:
    started = monotonic()
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    thread_id = configurable.get("thread_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("A bound user_id is required for mem0-chatbot")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id is required for mem0-chatbot")
    new_messages = inputs["messages"]
    user_messages = [
        {"role": "user", "content": message.content}
        for message in new_messages
        if isinstance(message, HumanMessage)
        and isinstance(message.content, str)
        and message.content.strip()
    ]
    messages = (previous["messages"] if previous else []) + new_messages
    video_dialogue = previous.get("video_dialogue") if previous else None
    memory = get_mem0_service()
    dialogue = MemoryDialogue(user_id=user_id)
    saved_dialogue = previous.get("memory_dialogue") if previous else None
    if saved_dialogue:
        try:
            restored = MemoryDialogue.model_validate(saved_dialogue)
            if restored.user_id == user_id:
                dialogue = restored
        except ValidationError:
            logger.warning("Discarded invalid memory dialogue checkpoint")
    model_name = configurable.get("model", settings.DEFAULT_MODEL)
    thinking = configurable.get("voice_enable_thinking")
    model = (
        get_model(model_name, enable_thinking=thinking)
        if isinstance(thinking, bool)
        else get_model(model_name)
    )
    if user_messages:
        email_response, email_dialogue = await handle_email_command(
            next(
                message for message in reversed(new_messages) if isinstance(message, HumanMessage)
            ),
            previous.get("email_dialogue") if previous else None,
            model,
            config,
        )
        if email_response is not None:
            return entrypoint.final(
                value={"messages": [email_response]},
                save={
                    "messages": messages + [email_response],
                    "memory_dialogue": dialogue.model_dump(mode="json"),
                    "email_dialogue": email_dialogue,
                    "video_dialogue": video_dialogue,
                },
            )
        command_response = await handle_memory_command(
            user_messages[-1]["content"], memory, user_id, dialogue, model, config
        )
        if command_response is not None:
            logger.info(
                "Memory command completed memory_status=%s duration_ms=%d",
                command_response.response_metadata["memory_status"],
                int((monotonic() - started) * 1000),
            )
            return entrypoint.final(
                value={"messages": [command_response]},
                save={
                    "messages": messages + [command_response],
                    "memory_dialogue": dialogue.model_dump(mode="json"),
                    "video_dialogue": video_dialogue,
                },
            )
        video_response, video_dialogue = await handle_video_skill(
            next(
                message for message in reversed(new_messages) if isinstance(message, HumanMessage)
            ),
            video_dialogue,
            model,
            config,
            history=messages[:-1],
            writer=get_stream_writer(),
        )
        if video_response is not None:
            return entrypoint.final(
                value={"messages": [video_response]},
                save={
                    "messages": messages + [video_response],
                    "memory_dialogue": dialogue.model_dump(mode="json"),
                    "video_dialogue": video_dialogue,
                },
            )
    status = "disabled" if memory is None else "available"
    facts: list[str] = []
    logger.info("Mem0 chatbot started memory_enabled=%s", memory is not None)
    if memory and user_messages:
        try:
            facts = await memory.search(user_messages[-1]["content"], user_id)
        except Exception as exc:
            status = "search_failed"
            logger.warning("Mem0 search failed error_type=%s", type(exc).__name__)
    prompt = _chat_prompt(messages, facts, status)
    response = await model.ainvoke(prompt, config)
    if memory and user_messages:
        try:
            await memory.remember(
                [user_messages[-1]], user_id, thread_id, context=_extraction_context(messages[:-1])
            )
            status = "saved" if status == "available" else "saved_search_failed"
        except Exception as exc:
            status = "write_failed"
            logger.warning("Mem0 write failed error_type=%s", type(exc).__name__)
    response.response_metadata["memory_status"] = status
    logger.info(
        "Mem0 chatbot completed memory_status=%s duration_ms=%d",
        status,
        int((monotonic() - started) * 1000),
    )
    return entrypoint.final(
        value={"messages": [response]},
        save={
            "messages": messages + [response],
            "memory_dialogue": dialogue.model_dump(mode="json"),
            "video_dialogue": video_dialogue,
        },
    )
