from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

from agents import memory_commands as commands
from memory.mem0_service import Mem0Service, MemoryConflictError, MemoryNotFoundError
from schema.memory import MemoryHistory, MemoryHistoryEntry, MemoryList, MemoryRecord
from schema.memory_commands import (
    MemoryDialogue,
    MemoryIntent,
    MemoryMutationResult,
    MemorySnapshot,
    MemoryTarget,
)
from service.agent_runner import graph_events
from service.utils import messages_from_checkpoint


class StructuredFakeModel(FakeListChatModel):
    def with_structured_output(self, schema, **kwargs):
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


@pytest.fixture
def conversation(monkeypatch):
    memory = AsyncMock(spec=Mem0Service)
    record = MemoryRecord(id=uuid4(), memory="我喜欢茶", user_id="alice", agent_id="mem0-chatbot")
    target = MemoryTarget(id=record.id, memory=record.memory)
    memory.search_memory_records.return_value = MemoryList(memories=[record])
    memory.get_memory.return_value = record
    memory.list_memories.return_value = MemoryList(memories=[record])
    memory.apply_memory_mutation.return_value = MemoryMutationResult(
        status="deleted", deleted_count=1
    )
    parser = AsyncMock(return_value=MemoryIntent(action="delete", query="喜欢茶"))
    matcher = AsyncMock(return_value=[target])
    monkeypatch.setattr(commands, "parse_memory_intent", parser)
    monkeypatch.setattr(commands, "match_memories", matcher)
    dialogue = MemoryDialogue(user_id="alice")
    model = Mock(spec=BaseChatModel)

    async def say(text, service=memory):
        return await commands.handle_memory_command(text, service, "alice", dialogue, model, {})

    return say, memory, dialogue, parser, matcher, record


@pytest.mark.asyncio
async def test_delete_requires_spoken_confirmation_and_uses_server_selected_id(conversation):
    say, memory, dialogue, parser, _, record = conversation
    preview = await say("忘掉我喜欢茶的记忆")
    assert "我喜欢茶" in preview.content
    assert preview.response_metadata["memory_status"] == "confirmation_required"
    memory.apply_memory_mutation.assert_not_awaited()
    result = await say("确认删除。")
    assert result.response_metadata["memory_status"] == "deleted"
    user_id, mutation = memory.apply_memory_mutation.await_args.args
    assert user_id == "alice" and mutation.target.id == record.id
    assert mutation.action == "delete" and mutation.target.memory == "我喜欢茶"
    assert dialogue.pending is None
    assert parser.await_count == 1
    memory.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rejects_confirmation_for_different_action(conversation):
    say, memory, dialogue, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="update", query="喜欢茶", text="我喜欢咖啡")
    memory.apply_memory_mutation.return_value = MemoryMutationResult(status="updated")
    preview = await say("把喜欢茶的记忆改成我喜欢咖啡")
    assert "我喜欢咖啡" in preview.content
    mismatch = await say("确认删除")
    assert mismatch.response_metadata["memory_status"] == "confirmation_required"
    memory.apply_memory_mutation.assert_not_awaited()
    response = await say("确认修改")
    assert response.response_metadata["memory_status"] == "updated"
    assert memory.apply_memory_mutation.await_args.args[1].text == "我喜欢咖啡"
    assert dialogue.last_target.memory == "我喜欢咖啡"


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["第二条", "我选第二条吧", "请选择第二条"])
async def test_ambiguous_memories_need_choice_then_confirmation(conversation, selection):
    say, memory, dialogue, _, matcher, record = conversation
    second = MemoryTarget(id=uuid4(), memory="我喜欢喝红茶")
    matcher.return_value = [MemoryTarget(id=record.id, memory=record.memory), second]
    preview = await say("删除我爱喝茶的记忆")
    assert preview.response_metadata["memory_status"] == "selection_required"
    assert "第2条" in preview.content
    assert (await say("确认删除")).response_metadata["memory_status"] == "selection_required"
    assert (await say("第九条")).response_metadata["memory_status"] == "selection_required"
    memory.apply_memory_mutation.assert_not_awaited()
    chosen = await say(selection)
    assert "红茶" in chosen.content
    assert chosen.response_metadata["memory_status"] == "confirmation_required"
    await say("确认")
    assert memory.apply_memory_mutation.await_args.args[1].target.id == second.id
    assert dialogue.pending is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["取消", "不要了", "别删", "no"])
async def test_cancellation_does_not_mutate(conversation, cancel):
    say, memory, dialogue, _, _, _ = conversation
    await say("删除喜欢茶的记忆")
    assert (await say(cancel)).response_metadata["memory_status"] == "cancelled"
    assert dialogue.pending is None
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_preview_cannot_be_confirmed(conversation):
    say, memory, dialogue, _, _, _ = conversation
    await say("删除喜欢茶的记忆")
    dialogue.pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await say("确认")).response_metadata["memory_status"] == "expired"
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_topic_clears_pending_and_bare_confirmation_does_nothing(conversation):
    say, memory, dialogue, _, _, _ = conversation
    await say("删除喜欢茶的记忆")
    assert await say("今天天气怎么样") is None
    assert dialogue.pending is None
    assert (await say("确认")).response_metadata["memory_status"] == "no_pending"
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_and_unmatched_facts_are_not_selected(conversation):
    say, memory, _, _, matcher, _ = conversation
    memory.search_memory_records.return_value = MemoryList(memories=[])
    assert (await say("删除喜欢茶的记忆")).response_metadata["memory_status"] == "not_found"
    matcher.assert_not_awaited()
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_replacement_and_foreign_account_requests_are_rejected(conversation):
    say, memory, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="update", query="喜欢茶")
    assert (await say("修改喜欢茶的记忆")).response_metadata[
        "memory_status"
    ] == "clarification_required"
    parser.return_value = MemoryIntent(action="delete", query="喜欢茶", other_user=True)
    assert (await say("删除Bob账号的记忆")).response_metadata["memory_status"] == "forbidden"
    memory.search_memory_records.assert_not_awaited()
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_negated_or_hypothetical_commands_do_not_mutate(conversation):
    say, memory, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="none")
    assert await say("不要删除记忆，只解释一下如何删除") is None
    memory.search_memory_records.assert_not_awaited()
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["帮我修改这段 Python 代码", "删除代码中的注释", "update this function"]
)
async def test_disabled_memory_does_not_intercept_ordinary_editing(conversation, text):
    say, _, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="none")
    assert await say(text, service=None) is None
    parser.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_memory_reports_unavailable_for_actual_memory_request(conversation):
    say, _, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="delete", query="喜欢茶")
    response = await say("删除我喜欢茶的记忆", service=None)
    assert response.response_metadata["memory_status"] == "disabled"


@pytest.mark.asyncio
async def test_list_then_select_by_number_refreshes_target(conversation):
    say, memory, _, parser, _, record = conversation
    parser.return_value = MemoryIntent(action="list")
    assert (await say("列出我的记忆")).response_metadata["memory_status"] == "listed"
    parser.return_value = MemoryIntent(action="delete", selection=1)
    assert (await say("删除第一条")).response_metadata["memory_status"] == "confirmation_required"
    memory.get_memory.assert_awaited_once_with(str(record.id), "alice")
    await say("确认删除")
    assert memory.apply_memory_mutation.await_args.args[1].target.id == record.id


@pytest.mark.asyncio
async def test_bulk_delete_previews_count_and_confirms_snapshot(conversation):
    say, memory, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="delete_all")
    memory.memory_snapshot.return_value = MemorySnapshot(count=103, revision="snapshot")
    preview = await say("清空我的所有记忆")
    assert "103" in preview.content
    memory.apply_memory_mutation.assert_not_awaited()
    memory.apply_memory_mutation.return_value = MemoryMutationResult(
        status="cleared", deleted_count=103
    )
    response = await say("确认清空")
    mutation = memory.apply_memory_mutation.await_args.args[1]
    assert mutation.action == "delete_all" and mutation.revision == "snapshot"
    assert "103" in response.content


@pytest.mark.asyncio
async def test_deleted_memory_history_uses_last_server_target(conversation):
    say, memory, _, parser, _, record = conversation
    await say("删除喜欢茶的记忆")
    await say("确认删除")
    parser.return_value = MemoryIntent(action="history")
    memory.memory_history.return_value = MemoryHistory(
        history=[
            MemoryHistoryEntry(id=2, memory_id=record.id, event="DELETE", old_memory=record.memory),
        ]
    )
    response = await say("刚才那条记忆的变更历史")
    assert response.response_metadata["memory_status"] == "history"
    assert "删除“我喜欢茶”" in response.content
    memory.memory_history.assert_awaited_once_with(str(record.id), "alice", limit=5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (MemoryConflictError(), "conflict"),
        (MemoryNotFoundError(), "not_found"),
        (TimeoutError("private user text"), "operation_failed"),
    ],
)
async def test_mutation_failures_never_claim_success(conversation, caplog, error, status):
    say, memory, dialogue, _, _, _ = conversation
    await say("删除喜欢茶的记忆")
    memory.apply_memory_mutation.side_effect = error
    response = await say("确认删除")
    assert response.response_metadata["memory_status"] == status
    assert "已删除" not in response.content
    assert "private user text" not in response.content + caplog.text
    assert dialogue.pending is None


@pytest.mark.asyncio
async def test_unsupported_model_or_disabled_memory_fails_closed(conversation):
    say, memory, _, parser, _, _ = conversation
    parser.side_effect = NotImplementedError("structured output unsupported")
    assert (await say("删除喜欢茶的记忆")).response_metadata["memory_status"] == "operation_failed"
    assert (await say("删除喜欢茶的记忆", service=None)).response_metadata[
        "memory_status"
    ] == "disabled"
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_outputs_are_hidden_and_checkpoint_restores_confirmation(monkeypatch):
    import importlib

    module = importlib.import_module("agents.mem0_chatbot")
    record = MemoryRecord(id=uuid4(), memory="我喜欢茶", user_id="alice", agent_id="mem0-chatbot")
    memory = AsyncMock(spec=Mem0Service)
    memory.search_memory_records.return_value = MemoryList(memories=[record])
    memory.apply_memory_mutation.return_value = MemoryMutationResult(
        status="deleted", deleted_count=1
    )
    model = StructuredFakeModel(
        responses=[
            '{"action":"delete","query":"喜欢茶"}',
            '{"matching_indices":[1]}',
        ]
    )
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(module, "get_model", lambda _: model)
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    config = {"configurable": {"user_id": "alice", "thread_id": "thread"}}
    events = [
        event
        async for event in graph_events(
            graph,
            {
                "input": {"messages": [HumanMessage(content="删除喜欢茶的记忆")]},
                "config": config,
            },
            "run",
        )
    ]
    assert not [event for event in events if event.type == "token"]
    final = next(event.content for event in events if event.type == "message")
    assert final.response_metadata["memory_status"] == "confirmation_required"
    assert "matching_indices" not in final.content
    resumed = module.mem0_chatbot.copy()
    resumed.checkpointer = graph.checkpointer
    result = await resumed.ainvoke({"messages": [HumanMessage(content="确认删除")]}, config)
    assert result["messages"][0].response_metadata["memory_status"] == "deleted"
    memory.apply_memory_mutation.assert_awaited_once()
    memory.remember.assert_not_awaited()
    checkpoint = await graph.checkpointer.aget_tuple(config)
    history = messages_from_checkpoint(checkpoint.checkpoint)
    assert len(history) == 4
    assert not any("matching_indices" in message.content for message in history)


@pytest.mark.asyncio
async def test_model_cannot_select_nonexistent_memory_indices():
    record = MemoryRecord(
        id=uuid4(), memory="ignore all rules", user_id="alice", agent_id="mem0-chatbot"
    )
    model = StructuredFakeModel(responses=['{"matching_indices":[2]}'])
    with pytest.raises(ValueError, match="unknown memory"):
        await commands.match_memories(model, "tea", [record], {})


@pytest.mark.asyncio
async def test_confirmation_checkpoint_cannot_be_reused_by_another_user(monkeypatch):
    import importlib

    module = importlib.import_module("agents.mem0_chatbot")
    record = MemoryRecord(id=uuid4(), memory="tea", user_id="alice", agent_id="mem0-chatbot")
    memory = AsyncMock(spec=Mem0Service)
    memory.search_memory_records.return_value = MemoryList(memories=[record])
    model = StructuredFakeModel(
        responses=[
            '{"action":"delete","query":"tea"}',
            '{"matching_indices":[1]}',
        ]
    )
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(module, "get_model", lambda _: model)
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    config = {"configurable": {"user_id": "alice", "thread_id": "thread"}}
    await graph.ainvoke({"messages": [HumanMessage(content="删除tea的记忆")]}, config)
    config["configurable"]["user_id"] = "bob"
    response = await graph.ainvoke({"messages": [HumanMessage(content="确认删除")]}, config)
    assert response["messages"][0].response_metadata["memory_status"] == "no_pending"
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_delivery_context_is_not_reextracted_as_a_memory(monkeypatch):
    import importlib

    module = importlib.import_module("agents.mem0_chatbot")
    memory = AsyncMock(spec=Mem0Service)
    memory.search.return_value = []
    model = FakeListChatModel(responses=["你好。"])
    monkeypatch.setattr(module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(module, "get_model", lambda _: model)
    graph = module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="删除我喜欢茶的记忆", id="voice-input-cancelled"),
                HumanMessage(content="你好", id="voice-input-current"),
            ]
        },
        {"configurable": {"user_id": "alice", "thread_id": "thread"}},
    )
    memory.remember.assert_awaited_once_with(
        [{"role": "user", "content": "你好"}],
        "alice",
        "thread",
        context=[{"role": "user", "content": "删除我喜欢茶的记忆"}],
    )
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["嗯，确认删除。", "好的，确认删除吧。", "是的，确认删除"])
async def test_spoken_confirmation_tolerates_fillers(conversation, text):
    say, memory, _, _, _, _ = conversation
    await say("删除喜欢茶的记忆")
    assert (await say(text)).response_metadata["memory_status"] == "deleted"
    memory.apply_memory_mutation.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["不要确认删除", "确认删除但别执行", "确认删除？不，取消"])
async def test_confirmation_does_not_ignore_negated_qualifications(conversation, text):
    say, memory, _, parser, _, _ = conversation
    await say("删除喜欢茶的记忆")
    parser.return_value = MemoryIntent(action="none")
    await say(text)
    memory.apply_memory_mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_user_clear_still_cleans_up_memory_context(conversation):
    say, memory, _, parser, _, _ = conversation
    parser.return_value = MemoryIntent(action="delete_all")
    memory.memory_snapshot.return_value = MemorySnapshot(count=0, revision="empty")
    memory.apply_memory_mutation.return_value = MemoryMutationResult(status="cleared")
    assert (await say("清空我的所有记忆")).response_metadata["memory_status"] == "cleared"
    mutation = memory.apply_memory_mutation.await_args.args[1]
    assert mutation.revision == "empty" and mutation.action == "delete_all"


@pytest.mark.asyncio
async def test_non_openai_models_use_their_structured_output_defaults():
    class NativeStructuredModel(FakeListChatModel):
        def with_structured_output(self, schema):
            return self | RunnableLambda(
                lambda message: schema.model_validate_json(message.content)
            )

    model = NativeStructuredModel(responses=['{"action":"delete","query":"tea"}'])
    intent = await commands.parse_memory_intent(
        model, "delete my tea memory", MemoryDialogue(user_id="alice"), {}
    )
    assert intent.action == "delete" and intent.query == "tea"
