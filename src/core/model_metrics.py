"""Measure model output and cache usage without buffering or logging conversation content."""

import asyncio
import hashlib
import json
import logging
from time import monotonic
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk
from langchain_core.runnables import Runnable, RunnableBinding, RunnableConfig
from langchain_core.runnables.config import merge_configs

from core import settings

logger = logging.getLogger(__name__)


def _has_text(content: str | list[str | dict[str, Any]]) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    return any(
        bool(block.strip())
        if isinstance(block, str)
        else block.get("type") == "text" and bool(str(block.get("text", "")).strip())
        for block in content
    )


class ModelTiming(AsyncCallbackHandler):
    run_inline = True

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.call_id = uuid4().hex
        self.started = monotonic()
        self.first_text_ms: int | None = None
        self.first_tool_ms: int | None = None
        self.first_reported = False

    def observe(self, text: bool, tool: bool, source: str) -> None:
        elapsed = int((monotonic() - self.started) * 1000)
        if text and self.first_text_ms is None:
            self.first_text_ms = elapsed
        if tool and self.first_tool_ms is None:
            self.first_tool_ms = elapsed
        if (text or tool) and not self.first_reported:
            self.first_reported = True
            logger.info(
                "Model first output stage=%s call_id=%s kind=%s source=%s duration_ms=%d",
                self.stage,
                self.call_id,
                "text" if text else "tool",
                source,
                elapsed,
            )

    async def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        **kwargs: Any,
    ) -> None:
        message = chunk.message if isinstance(chunk, ChatGenerationChunk) else None
        tool = bool(getattr(message, "tool_call_chunks", None))
        self.observe(_has_text(token), tool, "stream")


async def invoke_chat_model(
    model: Runnable[LanguageModelInput, AIMessage],
    prompt: list[BaseMessage],
    config: RunnableConfig,
    *,
    stage: str,
) -> AIMessage:
    timing = ModelTiming(stage)
    callback_config = merge_configs(config, {"callbacks": [timing]})
    model_name = str(config.get("configurable", {}).get("model", settings.DEFAULT_MODEL))
    bound_tools = model.kwargs.get("tools", []) if isinstance(model, RunnableBinding) else []
    tools_digest = hashlib.sha256(
        json.dumps(bound_tools, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]
    fixed_content = []
    for message in prompt:
        if not isinstance(message, SystemMessage) or message.name == "runtime_context":
            break
        fixed_content.append(message.content)
    prompt_digest = hashlib.sha256(
        json.dumps(fixed_content, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]
    try:
        response = await model.ainvoke(prompt, callback_config)
    except asyncio.CancelledError:
        logger.info("Model call cancelled stage=%s", stage)
        raise
    except Exception as exc:
        logger.warning("Model call failed stage=%s error_type=%s", stage, type(exc).__name__)
        raise
    text = _has_text(response.content)
    timing.observe(text, bool(response.tool_calls), "message")
    usage = response.usage_metadata or {}
    details = usage.get("input_token_details") or {}
    cached = details.get("cache_read")
    logger.info(
        "Model call completed stage=%s call_id=%s model=%s prompt_digest=%s tools_digest=%s "
        "input_tokens=%s cache_read_tokens=%s cache_write_tokens=%s output_tokens=%s "
        "first_text_ms=%s first_tool_ms=%s duration_ms=%d",
        stage,
        timing.call_id,
        model_name,
        prompt_digest,
        tools_digest,
        usage.get("input_tokens"),
        cached,
        details.get("cache_creation"),
        usage.get("output_tokens"),
        timing.first_text_ms,
        timing.first_tool_ms,
        int((monotonic() - timing.started) * 1000),
    )
    return response
