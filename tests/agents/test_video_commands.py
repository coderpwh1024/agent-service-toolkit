import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.func import entrypoint

from core import settings
from video import runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["chatbot", "mem0_chatbot"])
@pytest.mark.parametrize("legacy_dialogue", [False, True])
async def test_video_requests_use_ordinary_chat_without_video_execution(
    monkeypatch, agent_name, legacy_dialogue
):
    module = importlib.import_module(f"agents.{agent_name}")
    model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content="普通聊天回答")))
    service = Mock(create=AsyncMock(), query=AsyncMock(), list_tasks=AsyncMock())
    monkeypatch.setattr(runtime, "_service", service)
    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    monkeypatch.setattr(module, "get_model", lambda *_, **kwargs: model)
    if agent_name == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    graph = getattr(module, agent_name).copy()
    graph.checkpointer = MemorySaver()
    config = {
        "configurable": {
            "user_id": "7",
            "authenticated_user_id": "7",
            "thread_id": "video-thread",
        }
    }
    saved = None
    if legacy_dialogue:
        saved = {
            "user_id": "7",
            "last_record_id": 123,
            "draft_request": "生成一个十秒的视频",
            "draft_expires_at": datetime.now(UTC).timestamp() + 600,
        }

        @entrypoint(checkpointer=graph.checkpointer)
        async def seed_checkpoint(inputs):
            return entrypoint.final(value={}, save={"messages": [], "video_dialogue": saved})

        await seed_checkpoint.ainvoke({}, config)

    requests = [
        "生成一个五秒海边日落的视频",
        "刚才的视频好了吗",
        "查询第123号视频",
        "查询我最近的视频",
        "列出已经生成的视频",
        "我现在有几个视频啊",
        "海边日落",
    ]
    for index, text in enumerate(requests):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=text, id=f"voice-input-{index}")]}, config
        )
        response = result["messages"][0]
        assert response.content == "普通聊天回答"
        assert "video_status" not in response.response_metadata
        assert "video" not in response.response_metadata
        assert model.ainvoke.await_args.args[0][-1].content == text

    assert model.ainvoke.await_count == len(requests)
    model.with_structured_output.assert_not_called()
    service.create.assert_not_awaited()
    service.query.assert_not_awaited()
    service.list_tasks.assert_not_awaited()
    service.repo.latest_id.assert_not_called()
    checkpoint = await graph.checkpointer.aget_tuple(config)
    assert checkpoint.checkpoint["channel_values"]["__previous__"]["video_dialogue"] == saved
