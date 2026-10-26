import asyncio
import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from core import settings
from core.skills import SkillDefinition
from schema.video import VideoTaskPage
from schema.video_skill import VideoSkillDialogue
from service.agent_runner import graph_events


def call(name, args=None, call_id="call"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


@pytest.fixture(params=["chatbot", "mem0_chatbot"])
def setup(request, monkeypatch):
    module = importlib.import_module(f"agents.{request.param}")
    model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content="你好")))
    model.bind_tools.return_value = model
    memory = Mock(search=AsyncMock(return_value=[]), remember=AsyncMock())
    monkeypatch.setattr(module, "get_model", lambda *args, **kwargs: model)
    if request.param == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "VIDEO_SKILL_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "_video_skill",
        SkillDefinition(
            "manage-user-videos",
            "生成查询本人视频及处理草稿和分页",
            "FULL_VIDEO_RULES_MARKER",
            "a" * 64,
        ),
    )
    graph = getattr(module, request.param).copy()
    graph.checkpointer = MemorySaver()
    config = {"configurable": {"user_id": "7", "authenticated_user_id": "7", "thread_id": "skills"}}
    return graph, model, config, memory, module


@pytest.mark.asyncio
async def test_ordinary_chat_does_not_load_rules_or_operation_schema(setup):
    graph, model, config, _, _ = setup
    await graph.ainvoke({"messages": [HumanMessage(content="你好")]}, config)
    model.ainvoke.assert_awaited_once()
    model.with_structured_output.assert_not_called()
    assert "FULL_VIDEO_RULES_MARKER" not in str(model.ainvoke.await_args)
    tools = model.bind_tools.call_args.args[0]
    assert any(isinstance(t, dict) and t["name"] == "load_video_skill" for t in tools)
    assert "VideoGenerationDraft" not in str(tools)


@pytest.mark.asyncio
async def test_unavailable_skill_does_not_block_ordinary_chat(setup, monkeypatch):
    graph, model, config, _, _ = setup
    monkeypatch.setattr(settings, "_video_skill", None)
    result = await graph.ainvoke({"messages": [HumanMessage(content="你好")]}, config)
    assert result["messages"][0].content == "你好"
    model.ainvoke.assert_awaited_once()
    model.ainvoke.side_effect = [call("load_video_skill")]
    result = await graph.ainvoke({"messages": [HumanMessage(content="列出视频")]}, config)
    assert result["messages"][0].response_metadata["video_status"] == "unavailable"


@pytest.mark.asyncio
async def test_on_demand_count_uses_owned_service_without_leaking_skill_history(setup, monkeypatch):
    from video import runtime

    graph, model, config, memory, _ = setup
    service = Mock(
        list_tasks=AsyncMock(
            return_value=VideoTaskPage(items=[], total=42, page=1, page_size=1, total_pages=42)
        )
    )
    monkeypatch.setattr(runtime, "_service", service)
    model.ainvoke.side_effect = [
        call("load_video_skill"),
        call("video_operation", {"action": "count"}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="我有多少视频", id="input-1")]}, config
    )
    assert "42" in result["messages"][0].content
    assert model.ainvoke.await_count == 2
    assert "FULL_VIDEO_RULES_MARKER" not in str(model.ainvoke.await_args_list[0])
    assert "FULL_VIDEO_RULES_MARKER" in str(model.ainvoke.await_args_list[1])
    assert service.list_tasks.await_args.args[0] == 7
    memory.remember.assert_not_awaited()
    checkpoint = await graph.checkpointer.aget_tuple(config)
    saved = checkpoint.checkpoint["channel_values"]["__previous__"]
    assert saved["video_dialogue"]["user_id"] == "7"
    assert len(saved["messages"]) == 2
    assert all(not isinstance(message, ToolMessage) for message in saved["messages"])
    assert "FULL_VIDEO_RULES_MARKER" not in str(saved)


@pytest.mark.asyncio
async def test_draft_correction_preserves_original_input_identity_and_parameters(
    setup, monkeypatch
):
    from video import runtime

    graph, model, config, _, _ = setup
    service = Mock(create=AsyncMock(side_effect=RuntimeError("synthetic failure")))
    monkeypatch.setattr(runtime, "_service", service)
    model.ainvoke.side_effect = [
        call("load_video_skill"),
        call(
            "video_operation",
            {"action": "draft", "authorized": True, "generation": {"duration": 10}},
        ),
        call("load_video_skill"),
        call(
            "video_operation",
            {"action": "create", "authorized": True, "generation": {"prompt": "海边"}},
        ),
        call("load_video_skill"),
        call(
            "video_operation",
            {"action": "create", "authorized": True, "generation": {"prompt": "海边"}},
        ),
    ]
    await graph.ainvoke({"messages": [HumanMessage(content="生成十秒视频", id="draft")]}, config)
    for _ in range(2):
        await graph.ainvoke({"messages": [HumanMessage(content="海边", id="stable-input")]}, config)
    first, second = service.create.await_args_list
    assert first.args == second.args
    assert first.args[2].duration == 10 and first.args[2].prompt == "海边"
    assert '"duration": 10' in model.ainvoke.await_args_list[3].args[0][-1].content


@pytest.mark.asyncio
async def test_save_only_draft_does_not_submit_generation(setup, monkeypatch):
    from video import runtime

    graph, model, config, _, _ = setup
    service = Mock(create=AsyncMock(), edit=AsyncMock())
    monkeypatch.setattr(runtime, "_service", service)
    model.ainvoke.side_effect = [
        call("load_video_skill"),
        call(
            "video_operation",
            {"action": "draft", "authorized": True, "generation": {"duration": 10}},
        ),
    ]
    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="先保存十秒要求，场景稍后补充，不要提交生成", id="draft-only")
            ]
        },
        config,
    )
    assert result["messages"][0].response_metadata["video_status"] == "draft"
    checkpoint = await graph.checkpointer.aget_tuple(config)
    saved = checkpoint.checkpoint["channel_values"]["__previous__"]["video_dialogue"]
    assert saved["draft_generation"]["duration"] == 10
    service.create.assert_not_awaited()
    service.edit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        [
            call(
                "video_operation",
                {"action": "create", "authorized": True, "generation": {"prompt": "海边"}},
            )
        ],
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "load_video_skill", "args": {}, "id": "1"},
                    {"name": "research_assistant", "args": {"query": "公开问题"}, "id": "2"},
                ],
            )
        ],
        [call("load_video_skill", {"user_id": "8"})],
        [call("load_video_skill"), call("unknown")],
        [call("load_video_skill"), call("load_video_skill")],
        [
            call("load_video_skill"),
            call("video_operation", {"action": "detail", "record_id": 2.04}),
        ],
        [
            call("load_video_skill"),
            AIMessage(
                content="",
                invalid_tool_calls=[
                    {
                        "name": "video_operation",
                        "args": "{broken",
                        "id": "invalid",
                        "error": "invalid json",
                    }
                ],
            ),
        ],
    ],
)
async def test_invalid_selection_has_no_side_effects(setup, monkeypatch, responses):
    from video import runtime

    graph, model, config, _, _ = setup
    service = Mock(create=AsyncMock(), edit=AsyncMock(), query=AsyncMock(), list_tasks=AsyncMock())
    monkeypatch.setattr(runtime, "_service", service)
    model.ainvoke.side_effect = responses
    result = await graph.ainvoke({"messages": [HumanMessage(content="操作视频")]}, config)
    assert result["messages"][0].response_metadata["video_status"] == "invalid"
    service.create.assert_not_awaited()
    service.edit.assert_not_awaited()
    service.query.assert_not_awaited()
    service.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_loaded_model_failure_and_timeout_are_controlled(setup):
    graph, model, config, _, _ = setup
    for error, status in [
        (TimeoutError("secret-value"), "timeout"),
        (RuntimeError("secret-value"), "unavailable"),
    ]:
        model.ainvoke.side_effect = [call("load_video_skill"), error]
        result = await graph.ainvoke({"messages": [HumanMessage(content="查询视频")]}, config)
        response = result["messages"][0]
        assert response.response_metadata["video_status"] == status
        assert "secret-value" not in response.content


@pytest.mark.asyncio
async def test_cancellation_stops_loaded_model_before_submission(setup, monkeypatch):
    from video import runtime

    graph, model, config, _, _ = setup
    service = Mock(create=AsyncMock())
    monkeypatch.setattr(runtime, "_service", service)
    started, stopped = asyncio.Event(), asyncio.Event()
    count = 0

    async def answer(*args):
        nonlocal count
        count += 1
        if count == 1:
            return call("load_video_skill")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    model.ainvoke.side_effect = answer
    invocation = asyncio.create_task(
        graph.ainvoke({"messages": [HumanMessage(content="生成视频")]}, config)
    )
    async with asyncio.timeout(2):
        await started.wait()
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation
    assert stopped.is_set()
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_final_video_result_and_custom_event_are_visible(setup, monkeypatch):
    from video import runtime

    graph, _, config, _, module = setup

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(
        responses=[call("load_video_skill"), call("video_operation", {"action": "count"})]
    )
    monkeypatch.setattr(module, "get_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(
        runtime,
        "_service",
        Mock(
            list_tasks=AsyncMock(
                return_value=VideoTaskPage(items=[], total=0, page=1, page_size=1, total_pages=0)
            )
        ),
    )
    events = [
        event
        async for event in graph_events(
            graph,
            {"input": {"messages": [HumanMessage(content="视频数量")]}, "config": config},
            "skill-test",
        )
    ]
    assert not any(event.type == "token" for event in events)
    messages = [event.content for event in events if event.type == "message"]
    assert any(message.type == "custom" for message in messages)
    assert len([message for message in messages if message.type == "ai"]) == 1
    assert "FULL_VIDEO_RULES_MARKER" not in str(messages)
    assert not any(message.type == "tool" for message in messages)


def test_foreign_checkpoint_is_not_exposed_in_loaded_context(setup):
    from skills.video import video_skill_reference

    config = {"configurable": {"user_id": "7", "authenticated_user_id": "7"}}
    saved = VideoSkillDialogue(user_id="8", last_record_id=123, listed_ids=[456]).model_dump()
    reference = video_skill_reference(saved, config, [])
    assert '"last_record_id": null' in reference and '"listed_ids": []' in reference


@pytest.mark.parametrize(
    "owner,expires,pending", [("7", 600, True), ("7", -1, False), ("8", 600, False)]
)
def test_main_sees_only_owned_active_context_flags(setup, owner, expires, pending):
    from skills.video_chat import VideoSkillSession

    _, _, config, _, _ = setup
    saved = VideoSkillDialogue(
        user_id=owner,
        draft_request="private-scene-marker",
        draft_expires_at=datetime.now(UTC).timestamp() + expires,
        draft_action="create",
        last_record_id=987654321,
    ).model_dump()
    session = VideoSkillSession(HumanMessage(content="修改参数"), saved, [])
    rules = session.rules(config)
    assert "private-scene-marker" not in rules and "987654321" not in rules
    assert (
        '"pending_video_draft": true' in rules
        if pending
        else '"pending_video_draft": true' not in rules
    )
    assert "FULL_VIDEO_RULES_MARKER" not in rules
