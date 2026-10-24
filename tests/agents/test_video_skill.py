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
@pytest.mark.parametrize("model", [None, "wan3.0-video", "wan3.0-video-prime"])
async def test_wan3_skill_preserves_frame_inputs_and_native_parameters(video_skill, model) -> None:
    service, _, _ = video_skill
    generation = {
        "first_frame_url": "https://images.example/start.jpg",
        "last_frame_url": "https://images.example/end.jpg",
        "duration": 30,
        "ratio": "adaptive",
        "audio": False,
        "prompt_extend": False,
        "seed": -1,
    }
    if model is not None:
        generation["model"] = model
    response, saved = await invoke(
        video_skill,
        "根据这两张图片生成首尾帧视频",
        {
            "action": "create",
            "authorized": True,
            "generation": generation,
        },
    )
    assert response.response_metadata["video_status"] == "result"
    body = service.create.await_args.args[2]
    assert body.model == (model or "wan3.0-video") and body.duration == 30
    assert body.audio is False and body.prompt_extend is False
    assert str(body.first_frame_url) == generation["first_frame_url"]
    assert str(body.last_frame_url) == generation["last_frame_url"]
    assert saved["draft_generation"] is None


@pytest.mark.asyncio
async def test_wan3_missing_first_frame_stays_draft_until_corrected(video_skill) -> None:
    service, _, _ = video_skill
    response, saved = await invoke(
        video_skill,
        "使用万相按尾帧生成",
        {
            "action": "create",
            "authorized": True,
            "generation": {
                "model": "wan3.0-video",
                "prompt": "海边",
                "last_frame_url": "https://images.example/end.jpg",
            },
        },
    )
    assert response.response_metadata["video_status"] == "invalid"
    service.create.assert_not_awaited()
    response, _ = await invoke(
        video_skill,
        "这是首帧图片",
        {
            "action": "create",
            "authorized": True,
            "generation": {"first_frame_url": "https://images.example/start.jpg"},
        },
        saved,
    )
    assert response.response_metadata["video_status"] == "result"
    assert str(service.create.await_args.args[2].last_frame_url) == "https://images.example/end.jpg"


@pytest.mark.asyncio
async def test_wan3_list_model_is_preserved_on_next_page(video_skill) -> None:
    service, _, _ = video_skill
    _, saved = await invoke(
        video_skill,
        "查万相高速版视频",
        {"action": "list", "query": {"model": "wan3.0-video-prime"}},
    )
    assert service.list_tasks.await_args.args[1].model == "wan3.0-video-prime"
    await invoke(video_skill, "下一页", {"action": "list", "next_page": True}, saved)
    query = service.list_tasks.await_args.args[1]
    assert query.model == "wan3.0-video-prime" and query.page == 2


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
async def test_skill_returns_backend_derived_metadata_without_requiring_user_metadata(video_skill):
    service, _, _ = video_skill
    service.create.return_value = task(
        title="海边日落",
        cover_url="https://cdn.example/videos/happyhorse/task-123.jpg",
        status="SUCCEEDED",
        storage_status="STORED",
    )
    response, _ = await invoke(
        video_skill,
        "生成海边日落视频",
        {"action": "create", "authorized": True, "generation": {"prompt": "海边日落。镜头跟随"}},
    )
    body = service.create.await_args.args[2]
    assert body.title is None and body.cover_url is None
    assert response.response_metadata["video"]["title"] == "海边日落"
    assert response.response_metadata["video"]["cover_url"].endswith("task-123.jpg")
    assert "封面" in response.content


@pytest.mark.asyncio
async def test_cover_failure_explains_incomplete_assets_without_regeneration(video_skill):
    service, _, _ = video_skill
    service.query.return_value = task(
        title="海边日落",
        status="SUCCEEDED",
        storage_status="FAILED",
        qiniu_video_url="https://cdn.example/v.mp4",
        error_code="COVER_ARCHIVE_FAILED",
    )
    response, _ = await invoke(
        video_skill,
        "编号123的视频好了吗",
        {"action": "detail", "record_id": 123},
    )
    assert "封面" in response.content and "重试" in response.content
    assert response.response_metadata["video"]["cover_url"] is None
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_list_with_missing_metadata_does_not_claim_complete_cover(video_skill):
    service, _, _ = video_skill
    service.list_tasks.return_value = VideoTaskPage(
        items=[task(status="SUCCEEDED", storage_status="STORED")],
        total=1,
        page=1,
        page_size=20,
        total_pages=1,
    )
    response, _ = await invoke(video_skill, "列出我的视频", {"action": "list"})
    assert "补齐" in response.content and "查询详情" in response.content
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_draft_is_retained_and_published_for_create_and_detail(video_skill):
    service, _, _ = video_skill
    metadata = {"title": "海边日落", "cover_url": "https://cdn.example/cover.jpg"}
    _, saved = await invoke(
        video_skill,
        "标题用海边日落，用我提供的封面，生成三十秒",
        {
            "action": "create",
            "authorized": True,
            "generation": {"prompt": "海边", "duration": 30, **metadata},
        },
    )
    service.create.assert_not_awaited()
    assert saved["draft_generation"]["title"] == metadata["title"]
    service.create.return_value = task(**metadata)
    service.query.return_value = task(**metadata)
    events = []
    response, saved = await invoke(
        video_skill,
        "改成十五秒",
        {"action": "create", "authorized": True, "generation": {"duration": 15}},
        saved,
        writer=events.append,
    )
    body = service.create.await_args.args[2]
    assert body.title == metadata["title"] and str(body.cover_url) == metadata["cover_url"]
    assert response.response_metadata["video"]["title"] == metadata["title"]
    assert response.response_metadata["video"]["cover_url"] == metadata["cover_url"]
    assert events[0].content[0]["video"]["cover_url"] == metadata["cover_url"]
    response, _ = await invoke(video_skill, "查询刚才的视频", {"action": "detail"}, saved)
    assert response.response_metadata["video"]["title"] == metadata["title"]
    assert response.response_metadata["video"]["cover_url"] == metadata["cover_url"]
    assert metadata["cover_url"] not in response.content


@pytest.mark.asyncio
async def test_list_event_returns_metadata_and_legacy_nulls(video_skill):
    service, _, _ = video_skill
    service.list_tasks.return_value = VideoTaskPage(
        items=[task(title="海边", cover_url="https://cdn.example/cover.jpg"), task(124)],
        total=2,
        page=1,
        page_size=20,
        total_pages=1,
    )
    events = []
    response, _ = await invoke(video_skill, "列出视频", {"action": "list"}, writer=events.append)
    items = events[0].content[0]["items"]
    assert items[0]["title"] == "海边" and items[0]["cover_url"] == "https://cdn.example/cover.jpg"
    assert items[1]["title"] is None and items[1]["cover_url"] is None
    assert "https://" not in response.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata, expected",
    [({"title": " "}, "标题"), ({"cover_url": "ftp://cdn.example/c.jpg"}, "封面")],
)
async def test_invalid_metadata_keeps_draft_and_does_not_submit(video_skill, metadata, expected):
    service, _, _ = video_skill
    response, saved = await invoke(
        video_skill,
        "按这个标题和封面生成视频",
        {
            "action": "create",
            "authorized": True,
            "record_id": 123,
            "generation": {"prompt": "海边", **metadata},
        },
    )
    assert response.response_metadata["video_status"] == "invalid" and expected in response.content
    assert saved["draft_generation"][next(iter(metadata))] == next(iter(metadata.values()))
    service.create.assert_not_awaited()
    service.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_passes_explicit_metadata_to_new_task(video_skill):
    service, _, _ = video_skill
    metadata = {"title": "水墨海边", "cover_url": "https://cdn.example/ink.jpg"}
    service.edit.return_value = task(124, **metadata)
    response, _ = await invoke(
        video_skill,
        "将编号三改成水墨风格，用这个标题和封面",
        {
            "action": "edit",
            "authorized": True,
            "record_id": 3,
            "editing": {"prompt": "水墨风格", **metadata},
        },
    )
    assert service.edit.await_args.kwargs == {"prompt": "水墨风格", **metadata}
    assert response.response_metadata["video"]["title"] == metadata["title"]


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
@pytest.mark.parametrize("action", ["list", "detail"])
async def test_keyword_search_returns_candidates_instead_of_previous_task(video_skill, action):
    service, _, _ = video_skill
    service.list_tasks.return_value = VideoTaskPage(
        items=[task(125, title="海边海鸥"), task(123, title="日落海鸥")],
        total=8,
        page=1,
        page_size=5,
        total_pages=2,
    )
    saved = VideoSkillDialogue(user_id="7", last_record_id=999).model_dump()
    events = []
    response, saved = await invoke(
        video_skill,
        "找海边有海鸥的视频",
        {"action": action, "query": {"q": "海边 海鸥", "status": "SUCCEEDED"}},
        saved,
        writer=events.append,
    )
    query = service.list_tasks.await_args.args[1]
    assert query.q == "海边 海鸥" and query.page_size == 5 and query.status == "SUCCEEDED"
    assert "海边海鸥" in response.content and "下一页" in response.content
    assert events[0].content[0]["type"] == "video.list"
    assert events[0].content[0]["total"] == 8
    assert saved["listed_ids"] == [125, 123]
    service.query.assert_not_awaited()
    await invoke(video_skill, "下一页", {"action": "list", "next_page": True}, saved)
    query = service.list_tasks.await_args.args[1]
    assert query.q == "海边 海鸥" and query.page == 2 and query.page_size == 5
    assert query.status == "SUCCEEDED"
    await invoke(video_skill, "第二个", {"action": "detail", "selection": 2}, saved)
    service.query.assert_awaited_once_with(123, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit, latest, expected", [(None, False, 5), (2, False, 2), (None, True, 1)]
)
async def test_search_result_limit_and_latest_are_applied(video_skill, limit, latest, expected):
    service, _, _ = video_skill
    query = {"q": "海边"}
    if limit is not None:
        query["page_size"] = limit
    await invoke(
        video_skill,
        "找海边的视频",
        {"action": "list", "query": query, "latest": latest},
    )
    used = service.list_tasks.await_args.args[1]
    assert used.q == "海边" and used.page_size == expected and used.page == 1
    service.repo.latest_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyword_count_and_empty_search_do_not_fall_back(video_skill):
    service, _, _ = video_skill
    service.list_tasks.return_value = VideoTaskPage(
        items=[], total=0, page=1, page_size=5, total_pages=0
    )
    saved = VideoSkillDialogue(user_id="7", last_record_id=999, listed_ids=[123]).model_dump()
    response, saved = await invoke(
        video_skill, "找森林视频", {"action": "detail", "query": {"q": "森林"}}, saved
    )
    assert "没有视频记录" in response.content and saved["listed_ids"] == []
    service.query.assert_not_awaited()
    response, _ = await invoke(
        video_skill, "有几个森林视频", {"action": "count", "query": {"q": "森林"}}, saved
    )
    assert "0 个" in response.content
    query = service.list_tasks.await_args.args[1]
    assert query.q == "森林" and query.page_size == 1


@pytest.mark.asyncio
async def test_edit_by_description_requires_selection_before_submission(video_skill):
    service, _, _ = video_skill
    saved = VideoSkillDialogue(user_id="7", last_record_id=999).model_dump()
    response, saved = await invoke(
        video_skill,
        "把海边视频改为水墨风格",
        {
            "action": "edit",
            "authorized": True,
            "query": {"q": "海边"},
            "editing": {"prompt": "水墨风格"},
        },
        saved,
    )
    assert "尚未提交编辑" in response.content
    service.edit.assert_not_awaited()
    service.query.assert_not_awaited()
    assert saved["draft_editing"]["prompt"] == "水墨风格"
    await invoke(
        video_skill,
        "编辑第二个",
        {"action": "edit", "authorized": True, "selection": 2},
        saved,
    )
    assert service.edit.await_args.args[2] == 123


@pytest.mark.asyncio
async def test_search_service_failure_does_not_submit_or_return_old_task(video_skill):
    service, _, _ = video_skill
    service.list_tasks.side_effect = RuntimeError("search unavailable")
    saved = VideoSkillDialogue(user_id="7", last_record_id=999).model_dump()
    response, _ = await invoke(
        video_skill, "找海边视频", {"action": "list", "query": {"q": "海边"}}, saved
    )
    assert response.response_metadata["video_status"] == "unavailable"
    assert "video" not in response.response_metadata
    service.create.assert_not_awaited()
    service.query.assert_not_awaited()


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
