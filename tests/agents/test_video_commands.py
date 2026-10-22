import importlib
import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.video_commands import handle_video_command
from core import settings
from schema.video import VideoCreate, VideoTask
from schema.video_commands import VideoDialogue, VideoIntent
from video import runtime
from video.service import VideoBusyError, VideoConflictError


def task(**changes):
    values = dict(
        id=123,
        user_id=7,
        task_id="task-123",
        status="PENDING",
        storage_status="NOT_READY",
        aliyun_video_url=None,
        qiniu_video_url=None,
        qiniu_object_key=None,
        error_code=None,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
    )
    values.update(changes)
    return VideoTask(**values)


def config(user="7", authenticated="7", thread="video-thread"):
    return {
        "configurable": {
            "user_id": user,
            "authenticated_user_id": authenticated,
            "thread_id": thread,
        }
    }


@pytest.fixture
def video_runtime(monkeypatch):
    service = Mock(create=AsyncMock(return_value=task()), query=AsyncMock(return_value=task()))
    service.repo = Mock(latest_id=AsyncMock(return_value=123))
    monkeypatch.setattr(runtime, "_service", service)
    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    structured = Mock(
        ainvoke=AsyncMock(
            return_value=VideoIntent(
                action="create",
                generation=VideoCreate(prompt="海边日落", duration=10),
            )
        )
    )
    structured.with_config.return_value = structured
    model = Mock(
        with_structured_output=Mock(return_value=structured),
        ainvoke=AsyncMock(return_value=AIMessage(content="普通对话")),
    )
    return service, model, structured


@pytest.mark.asyncio
async def test_voice_create_uses_authenticated_identity_and_stable_idempotency(video_runtime):
    service, model, structured = video_runtime
    message = HumanMessage(content="生成十秒海边日落的视频", id="voice-input-turn-123")
    response, saved = await handle_video_command(message, None, model, config())
    await handle_video_command(message, None, model, config())
    first, second = service.create.await_args_list
    assert first.args == second.args
    assert first.args[0] == 7 and first.args[1].startswith("voice-video-")
    assert first.args[2] == VideoCreate(prompt="海边日落", duration=10)
    assert saved["last_record_id"] == 123
    assert "排队" in response.content
    assert structured.with_config.call_args.kwargs["tags"] == ["skip_stream"]
    assert "untrusted" in structured.ainvoke.await_args.args[0][0].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user, authenticated", [("7", None), ("7", "8"), ("alice", "alice"), ("0", "0")]
)
async def test_invalid_identity_cannot_generate_or_query(video_runtime, user, authenticated):
    service, model, _ = video_runtime
    response, _ = await handle_video_command(
        HumanMessage(content="生成海边的视频"),
        None,
        model,
        config(user, authenticated),
    )
    assert response.response_metadata["video_status"] == "unauthenticated"
    service.create.assert_not_awaited()
    service.query.assert_not_awaited()
    model.with_structured_output.assert_not_called()


@pytest.mark.asyncio
async def test_draft_collects_scene_and_preserves_original_parameters_as_reference(video_runtime):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action="draft")
    response, saved = await handle_video_command(
        HumanMessage(content="帮我生成一个十秒的视频"),
        None,
        model,
        config(),
    )
    assert response.response_metadata["video_status"] == "draft"
    service.create.assert_not_awaited()
    structured.ainvoke.return_value = VideoIntent(
        action="create",
        generation=VideoCreate(prompt="海边日落", duration=10),
    )
    _, saved = await handle_video_command(
        HumanMessage(content="海边日落", id="voice-scene"),
        saved,
        model,
        config(),
    )
    reference = json.loads(structured.ainvoke.await_args.args[0][1].content)
    assert "十秒" in reference["pending_request"]
    assert service.create.await_args.args[2].duration == 10
    assert saved["draft_request"] is None


@pytest.mark.asyncio
async def test_cancel_only_discards_draft(video_runtime):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action="cancel")
    saved = VideoDialogue(
        user_id="7",
        draft_request="生成一个视频",
        draft_expires_at=datetime.now(UTC).timestamp() + 60,
    ).model_dump()
    response, saved = await handle_video_command(
        HumanMessage(content="取消"), saved, model, config()
    )
    assert response.response_metadata["video_status"] == "cancelled"
    assert saved["draft_request"] is None
    response, _ = await handle_video_command(
        HumanMessage(content="取消视频生成"),
        saved,
        model,
        config(),
    )
    assert response.response_metadata["video_status"] == "unsupported"
    service.create.assert_not_awaited()
    service.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_remembers_task_and_scopes_latest_lookup(video_runtime):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action="query")
    saved = VideoDialogue(user_id="7", last_record_id=123).model_dump()
    await handle_video_command(HumanMessage(content="刚才的视频好了没"), saved, model, config())
    service.query.assert_awaited_once_with(123, 7)
    service.repo.latest_id.assert_not_awaited()
    structured.ainvoke.return_value = VideoIntent(action="query", latest=True)
    await handle_video_command(HumanMessage(content="查询最近的视频"), None, model, config())
    service.repo.latest_id.assert_awaited_once_with(7)
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_record_has_priority_and_ignores_other_user_checkpoint(video_runtime):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action="query", record_id=456, latest=True)
    saved = VideoDialogue(user_id="8", last_record_id=999).model_dump()
    service.query.return_value = task(id=456)
    await handle_video_command(HumanMessage(content="查询456号视频"), saved, model, config())
    service.query.assert_awaited_once_with(456, 7)
    service.repo.latest_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_selection_and_foreign_record_do_not_disclose_data(video_runtime):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action="query")
    response, _ = await handle_video_command(HumanMessage(content="查视频"), None, model, config())
    assert response.response_metadata["video_status"] == "missing"
    service.query.assert_not_awaited()
    structured.ainvoke.return_value = VideoIntent(action="query", record_id=888)
    service.query.side_effect = LookupError("private other user")
    response, _ = await handle_video_command(
        HumanMessage(content="查888号视频"), None, model, config()
    )
    assert "private" not in response.content
    assert response.response_metadata["video_status"] == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["none", "unsupported"])
async def test_discussion_and_unsupported_actions_cannot_create(video_runtime, action):
    service, model, structured = video_runtime
    structured.ainvoke.return_value = VideoIntent(action=action)
    response, _ = await handle_video_command(
        HumanMessage(content="生成视频的原理是什么"),
        None,
        model,
        config(),
    )
    assert (
        response is None
        if action == "none"
        else response.response_metadata["video_status"] == "unsupported"
    )
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_cannot_accidentally_authorize_creation(video_runtime):
    service, model, _ = video_runtime
    response, _ = await handle_video_command(
        HumanMessage(content="查询视频进度"), None, model, config()
    )
    assert response.response_metadata["video_status"] == "invalid"
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_parser_failure_and_cancellation_have_no_side_effects(video_runtime):
    import asyncio

    service, model, structured = video_runtime
    structured.ainvoke.side_effect = ValueError("private text")
    response, _ = await handle_video_command(
        HumanMessage(content="生成视频"), None, model, config()
    )
    assert response.response_metadata["video_status"] == "invalid"
    structured.ainvoke.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await handle_video_command(HumanMessage(content="生成视频"), None, model, config())
    service.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status",
    [
        (VideoConflictError(), "conflict"),
        (VideoBusyError(), "busy"),
        (ConnectionError("private"), "unavailable"),
    ],
)
async def test_execution_failures_do_not_automatically_retry(video_runtime, error, status):
    service, model, _ = video_runtime
    service.create.side_effect = error
    response, _ = await handle_video_command(
        HumanMessage(content="生成一个海边视频", id="voice-1"),
        None,
        model,
        config(),
    )
    assert response.response_metadata["video_status"] == status
    assert service.create.await_count == 1
    assert "private" not in response.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, spoken",
    [
        ({"status": "RUNNING"}, "生成中"),
        ({"status": "SUBMISSION_UNKNOWN"}, "不确定"),
        ({"status": "FAILED"}, "失败"),
        ({"status": "UNKNOWN"}, "过期"),
        ({"status": "SUCCEEDED", "storage_status": "FAILED"}, "转存七牛云失败"),
        (
            {
                "status": "SUCCEEDED",
                "storage_status": "STORED",
                "qiniu_video_url": "https://cdn.example/video.mp4",
            },
            "转存七牛云",
        ),
    ],
)
async def test_speaks_only_real_state_and_emits_link_without_reading_url(
    video_runtime, changes, spoken
):
    service, model, structured = video_runtime
    service.query.return_value = task(**changes)
    structured.ainvoke.return_value = VideoIntent(action="query", record_id=123)
    events = []
    response, _ = await handle_video_command(
        HumanMessage(content="查询123号视频"),
        None,
        model,
        config(),
        writer=events.append,
    )
    assert spoken in response.content
    assert "https://" not in response.content
    payload = events[0].content[0]
    assert payload["type"] == "video.task"
    assert payload["video"]["record_id"] == 123
    assert payload["video"]["qiniu_video_url"] == changes.get("qiniu_video_url")
    assert "aliyun_video_url" not in payload["video"]


@pytest.mark.asyncio
async def test_expired_draft_and_unrelated_chat_do_not_trigger_generation(video_runtime):
    service, model, structured = video_runtime
    saved = VideoDialogue(user_id="7", draft_request="生成视频", draft_expires_at=1).model_dump()
    response, saved = await handle_video_command(
        HumanMessage(content="海边日落"), saved, model, config()
    )
    assert response is None and saved["draft_request"] is None
    structured.ainvoke.assert_not_awaited()
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_chinese_logs_exclude_private_scene_and_urls(video_runtime, caplog):
    _, model, _ = video_runtime
    caplog.set_level(logging.INFO, logger="agents.video_commands")
    await handle_video_command(HumanMessage(content="生成私人海边视频"), None, model, config())
    assert "视频语音控制入参" in caplog.text and "视频语音控制出参" in caplog.text
    assert "私人海边" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["chatbot", "mem0_chatbot"])
async def test_agents_preserve_video_reference_across_ordinary_chat(
    video_runtime, monkeypatch, agent_name
):
    service, model, structured = video_runtime
    module = importlib.import_module(f"agents.{agent_name}")
    monkeypatch.setattr(module, "get_model", lambda *_, **kwargs: model)
    if agent_name == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    graph = getattr(module, agent_name).copy()
    graph.checkpointer = MemorySaver()
    await graph.ainvoke(
        {"messages": [HumanMessage(content="生成一个海边视频", id="voice-1")]}, config()
    )
    await graph.ainvoke({"messages": [HumanMessage(content="你好", id="voice-2")]}, config())
    structured.ainvoke.return_value = VideoIntent(action="query")
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="刚才的视频好了吗", id="voice-3")]}, config()
    )
    assert result["messages"][0].response_metadata["video"]["record_id"] == 123
    service.query.assert_awaited_once_with(123, 7)
    assert service.create.await_count == 1
