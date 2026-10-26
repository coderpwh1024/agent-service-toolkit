import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import RunnableConfig

from core.model_metrics import ModelTiming, invoke_chat_model


@pytest.mark.asyncio
async def test_usage_logged_without_prompt_or_response(caplog):
    reply = AIMessage(
        content="private-answer",
        usage_metadata={
            "input_tokens": 2000,
            "output_tokens": 10,
            "total_tokens": 2010,
            "input_token_details": {"cache_read": 1536, "cache_creation": 128},
        },
    )
    model = FakeMessagesListChatModel(responses=[reply])
    caplog.set_level(logging.INFO, logger="core.model_metrics")
    result = await invoke_chat_model(
        model, [HumanMessage(content="private-input")], {}, stage="main_initial"
    )
    assert result is reply
    assert "cache_read_tokens=1536" in caplog.text and "cache_write_tokens=128" in caplog.text
    assert "input_tokens=2000" in caplog.text
    assert "private-input" not in caplog.text and "private-answer" not in caplog.text
    assert caplog.text.count("Model first output") == 1


@pytest.mark.asyncio
async def test_callback_preserves_existing_handlers_and_config():
    class Handler(AsyncCallbackHandler):
        ended = False

        async def on_llm_end(self, response, **kwargs):
            self.ended = True

    handler = Handler()
    cfg = RunnableConfig(
        callbacks=[handler],
        tags=["original"],
        configurable={"thread_id": "thread", "user_id": "user"},
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="ok")])
    await invoke_chat_model(model, [HumanMessage(content="hello")], cfg, stage="main_initial")
    assert handler.ended
    assert cfg["callbacks"] == [handler]
    assert cfg["tags"] == ["original"] and cfg["configurable"]["thread_id"] == "thread"


@pytest.mark.asyncio
async def test_tool_chunk_is_timed_separately_from_visible_text(caplog):
    timing = ModelTiming("video_operation")
    caplog.set_level(logging.INFO, logger="core.model_metrics")
    chunk = ChatGenerationChunk(
        message=AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "video_operation",
                    "args": "{",
                    "id": "call",
                    "index": 0,
                }
            ],
        )
    )
    await timing.on_llm_new_token("", chunk=chunk)
    await timing.on_llm_new_token("正文")
    await timing.on_llm_new_token("继续")
    assert timing.first_tool_ms is not None and timing.first_text_ms is not None
    assert caplog.text.count("Model first output") == 1
    assert "kind=tool" in caplog.text and "正文" not in caplog.text


@pytest.mark.asyncio
async def test_cancel_and_error_are_propagated_without_payloads(caplog):
    caplog.set_level(logging.INFO, logger="core.model_metrics")
    for exc in [asyncio.CancelledError(), RuntimeError("secret-value")]:
        model = AsyncMock()
        model.ainvoke.side_effect = exc
        with pytest.raises(type(exc)):
            await invoke_chat_model(model, [], {}, stage="main_initial")
    assert "cancelled" in caplog.text and "error_type=RuntimeError" in caplog.text
    assert "secret-value" not in caplog.text


@pytest.mark.asyncio
async def test_missing_usage_is_unknown_and_rich_content_is_not_dropped(caplog):
    caplog.set_level(logging.INFO, logger="core.model_metrics")
    reply = AIMessage(content=[{"type": "text", "text": "rich-text"}])
    result = await invoke_chat_model(
        FakeMessagesListChatModel(responses=[reply]), [], {}, stage="main_initial"
    )
    assert result.content == reply.content
    assert "cache_read_tokens=None" in caplog.text
    assert "Model first output" in caplog.text
