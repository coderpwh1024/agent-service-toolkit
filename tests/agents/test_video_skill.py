import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from core import settings
from core.skills import SkillDefinition
from schema.video import VideoCreate, VideoTask, VideoTaskPage
from schema.video_skill import VideoSkillDialogue, VideoSkillIntent
from skills.video import handle_video_skill


def task(record_id=123, **changes):
    return VideoTask.model_validate(
        {
            "id": record_id,
            "user_id": 7,
            "task_id": f"task-{record_id}",
            "status": "PENDING",
            "storage_status": "NOT_READY",
            "aliyun_video_url": None,
            "qiniu_video_url": None,
            "qiniu_object_key": None,
            "error_code": None,
            "create_time": datetime.now(UTC),
            "update_time": datetime.now(UTC),
            **changes,
        }
    )


def config():
    return {"configurable": {"user_id": "7", "authenticated_user_id": "7", "thread_id": "video"}}


@pytest.fixture
def video_skill(monkeypatch):
    from video import runtime

    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "VIDEO_SKILL_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "_video_skill",
        SkillDefinition(
            "manage-user-videos",
            "用户视频管理",
            Path("skills/manage-user-videos/SKILL.md").read_text(),
            "a" * 64,
        ),
    )
    service = Mock(
        create=AsyncMock(return_value=task()),
        edit=AsyncMock(return_value=task(124)),
        query=AsyncMock(return_value=task()),
        list_tasks=AsyncMock(
            return_value=VideoTaskPage(
                items=[task(125), task(123)], total=42, page=1, page_size=20, total_pages=3
            )
        ),
    )
    service.repo.latest_id = AsyncMock(return_value=123)
    monkeypatch.setattr(runtime, "_service", service)
    structured = Mock(ainvoke=AsyncMock(return_value=VideoSkillIntent(action="none")))
    structured.with_config.return_value = structured
    model = Mock(
        with_structured_output=Mock(return_value=structured),
        ainvoke=AsyncMock(return_value=AIMessage(content="普通聊天")),
    )
    return service, model, structured


async def invoke(video_skill, text, intent, saved=None, **kwargs):
    _, model, structured = video_skill
    structured.ainvoke.return_value = VideoSkillIntent.model_validate(intent)
    return await handle_video_skill(
        HumanMessage(content=text, id="voice-input-fixed"),
        saved,
        model,
        config(),
        history=kwargs.pop("history", []),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_generation_uses_no_watermark_and_replay_key(video_skill):
    service, _, structured = video_skill
    intent = {
        "action": "create",
        "authorized": True,
        "generation": {"prompt": "海边日落", "duration": 10},
    }
    events = []
    response, _ = await invoke(
        video_skill,
        "按上面描述做十秒",
        intent,
        history=[HumanMessage(content="海边日落")],
        writer=events.append,
    )
    await invoke(video_skill, "按上面描述做十秒", intent)
    first, second = service.create.await_args_list
    assert first.args == second.args
    assert first.args[0] == 7 and first.args[1].startswith("voice-video-")
    assert first.args[2] == VideoCreate(prompt="海边日落", duration=10, watermark=False)
    assert response.response_metadata["video"]["record_id"] == 123
    assert events[0].content[0]["type"] == "video.task"
    structured.with_config.assert_called_with(tags=["skip_stream"])


@pytest.mark.asyncio
async def test_rejected_duration_retains_scene_and_accepts_correction(video_skill):
    service, _, structured = video_skill
    response, saved = await invoke(
        video_skill,
        "生成三十秒海边视频",
        {
            "action": "create",
            "authorized": True,
            "generation": {"prompt": "海边日落", "duration": 30},
        },
    )
    assert "3 至 15 秒" in response.content
    service.create.assert_not_awaited()
    _, saved = await invoke(
        video_skill,
        "改为十五秒",
        {"action": "create", "authorized": True, "generation": {"duration": 15}},
        saved,
    )
    assert service.create.await_args.args[2].prompt == "海边日落"
    assert service.create.await_args.args[2].duration == 15
    payload = json.loads(structured.ainvoke.await_args.args[0][-1].content)
    assert payload["dialogue"]["draft_generation"]["duration"] == 30
    assert payload["dialogue"]["draft_generation"]["prompt"] == "海边日落"
    assert saved["draft_request"] is None


@pytest.mark.asyncio
async def test_missing_scene_retains_duration_and_receives_recent_history(video_skill):
    service, _, structured = video_skill
    response, saved = await invoke(
        video_skill,
        "生成十秒视频",
        {"action": "draft", "authorized": True, "generation": {"duration": 10}},
    )
    assert response.response_metadata["video_status"] == "draft"
    history = [HumanMessage(content="海边日落，海鸥飞过"), AIMessage(content="收到描述")]
    await invoke(
        video_skill,
        "上面就是视频内容",
        {"action": "create", "authorized": True, "generation": {"prompt": "海边日落，海鸥飞过"}},
        saved,
        history=history,
    )
    payload = json.loads(structured.ainvoke.await_args.args[0][-1].content)
    assert payload["recent_history"][0]["content"] == history[0].content
    assert service.create.await_args.args[2].duration == 10


@pytest.mark.asyncio
async def test_new_request_can_discard_rejected_draft(video_skill):
    service, _, _ = video_skill
    _, saved = await invoke(
        video_skill,
        "生成三十秒海边视频",
        {"action": "create", "authorized": True, "generation": {"prompt": "海边", "duration": 30}},
    )
    await invoke(
        video_skill,
        "放弃刚才的，重新生成森林视频",
        {
            "action": "create",
            "authorized": True,
            "reset_draft": True,
            "generation": {"prompt": "森林"},
        },
        saved,
    )
    assert service.create.await_args.args[2] == VideoCreate(prompt="森林")


@pytest.mark.asyncio
async def test_list_count_next_page_and_ordinal_use_database(video_skill):
    service, _, _ = video_skill
    response, saved = await invoke(
        video_skill, "我生成过哪些视频", {"action": "list", "query": {"status": "SUCCEEDED"}}
    )
    assert "42" in response.content
    assert saved["listed_ids"] == [125, 123]
    await invoke(video_skill, "第二条", {"action": "detail", "selection": 2}, saved)
    service.query.assert_awaited_once_with(123, 7)
    await invoke(video_skill, "下一页", {"action": "list", "next_page": True}, saved)
    assert service.list_tasks.await_args.args[1].page == 2
    assert service.list_tasks.await_args.args[1].status == "SUCCEEDED"
    response, _ = await invoke(video_skill, "有几个视频", {"action": "count"}, saved)
    assert "42" in response.content
    assert service.list_tasks.await_args.args[1].page_size == 1


@pytest.mark.asyncio
async def test_invalid_ordinal_and_empty_list_explain_result(video_skill):
    service, _, _ = video_skill
    saved = VideoSkillDialogue(user_id="7", listed_ids=[123]).model_dump()
    response, _ = await invoke(video_skill, "第二条", {"action": "detail", "selection": 2}, saved)
    assert "不在刚才" in response.content
    service.query.assert_not_awaited()
    service.list_tasks.return_value = VideoTaskPage(
        items=[], total=0, page=1, page_size=20, total_pages=0
    )
    response, _ = await invoke(video_skill, "列出视频", {"action": "list"})
    assert "0 个" in response.content and "没有视频记录" in response.content


@pytest.mark.asyncio
async def test_edit_uses_owned_record_and_does_not_generate_text_video(video_skill):
    service, _, _ = video_skill
    response, _ = await invoke(
        video_skill,
        "编号三改成水墨风格并保留声音",
        {
            "action": "edit",
            "authorized": True,
            "record_id": 3,
            "editing": {"prompt": "改为水墨风格", "audio_setting": "origin"},
        },
    )
    assert response.response_metadata["video"]["record_id"] == 124
    assert service.edit.await_args.args[0] == 7
    assert service.edit.await_args.args[2] == 3
    assert service.edit.await_args.kwargs == {"prompt": "改为水墨风格", "audio_setting": "origin"}
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_draft_preserves_selected_source(video_skill):
    service, _, _ = video_skill
    saved = VideoSkillDialogue(user_id="7", last_record_id=125, listed_ids=[125, 123]).model_dump()
    response, saved = await invoke(
        video_skill, "编辑第二条", {"action": "edit", "authorized": True, "selection": 2}, saved
    )
    assert response.response_metadata["video_status"] == "draft"
    assert saved["last_record_id"] == 123
    await invoke(
        video_skill,
        "改成水墨风格",
        {"action": "edit", "authorized": True, "editing": {"prompt": "水墨风格"}},
        saved,
    )
    assert service.edit.await_args.args[2] == 123


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, expected",
    [
        ("duration", "3 至 15 秒"),
        ("voice_clone", "声音克隆"),
        ("source", "外部视频"),
        ("editing", "精确裁剪"),
        ("selection", "整数视频编号"),
    ],
)
async def test_unsupported_explains_reason_and_never_submits(video_skill, reason, expected):
    service, _, _ = video_skill
    response, saved = await invoke(
        video_skill,
        "生成视频的原始要求",
        {
            "action": "unsupported",
            "authorized": True,
            "reason": reason,
            "generation": {"prompt": "原始场景"},
        },
    )
    assert expected in response.content
    assert saved["draft_generation"]["prompt"] == "原始场景"
    service.create.assert_not_awaited()
    service.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_none_and_unauthorized_create_do_not_execute(video_skill):
    service, _, _ = video_skill
    response, _ = await invoke(video_skill, "讨论一下视频生成", {"action": "none"})
    assert response is None
    response, _ = await invoke(
        video_skill, "引用一句生成视频", {"action": "create", "generation": {"prompt": "海边"}}
    )
    assert response.response_metadata["video_status"] == "clarification"
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_authentication_failure_does_not_access_service(video_skill):
    service, model, structured = video_skill
    structured.ainvoke.return_value = VideoSkillIntent(action="list")
    response, _ = await handle_video_skill(
        HumanMessage(content="视频列表"),
        None,
        model,
        {"configurable": {"user_id": "7", "authenticated_user_id": "8"}},
        history=[],
    )
    assert response.response_metadata["video_status"] == "unauthenticated"
    service.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_parser_failure_has_no_submission_and_does_not_leak(video_skill):
    service, _, structured = video_skill
    structured.ainvoke.side_effect = TimeoutError("secret-private-value")
    response, _ = await invoke(video_skill, "生成海边视频", {"action": "none"})
    assert "没有提交" in response.content and "secret" not in response.content
    service.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["chatbot", "mem0_chatbot"])
async def test_agents_use_skill_for_counts_without_old_keywords(
    video_skill, monkeypatch, agent_name
):
    service, model, structured = video_skill
    module = importlib.import_module(f"agents.{agent_name}")
    monkeypatch.setattr(module, "get_model", lambda *_, **kwargs: model)
    if agent_name == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    graph = getattr(module, agent_name).copy()
    graph.checkpointer = MemorySaver()
    structured.ainvoke.return_value = VideoSkillIntent(action="count")
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="我现在有几个视频啊")]}, config()
    )
    assert "42" in result["messages"][0].content
    service.list_tasks.assert_awaited_once()
    model.ainvoke.assert_not_awaited()
