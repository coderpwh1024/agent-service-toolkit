import importlib
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from agents import DEFAULT_AGENT, get_all_agent_info
from service.agent_runner import graph_events
from service.utils import messages_from_checkpoint

agent_module = importlib.import_module("agents.mem0_chatbot")


@pytest.fixture
def agent_setup(monkeypatch):
    memory = Mock(search=AsyncMock(return_value=["喜欢茶"]), remember=AsyncMock())
    model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content="你好")))
    monkeypatch.setattr(agent_module, "get_mem0_service", lambda: memory)
    monkeypatch.setattr(agent_module, "get_model", lambda _: model)
    graph = agent_module.mem0_chatbot.copy()
    graph.checkpointer = MemorySaver()
    return graph, memory, model


def config(user="alice", thread="thread-a"):
    return {"configurable": {"user_id": user, "thread_id": thread}}


def test_mem0_agent_is_first_and_default():
    assert DEFAULT_AGENT == "mem0-chatbot"
    assert get_all_agent_info()[0].key == DEFAULT_AGENT


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["chatbot", "mem0_chatbot"])
@pytest.mark.parametrize("thinking", [False, True])
async def test_voice_chat_applies_the_configured_thinking_profile(
    monkeypatch, agent_name, thinking
):
    module = importlib.import_module(f"agents.{agent_name}")
    model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content="你好")))
    factory = Mock(return_value=model)
    monkeypatch.setattr(module, "get_model", factory)
    if agent_name == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    graph = getattr(module, agent_name).copy()
    graph.checkpointer = MemorySaver()
    invocation = config()
    invocation["configurable"].update(model="qwen3.8-max", voice_enable_thinking=thinking)

    await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, invocation)

    factory.assert_called_once_with("qwen3.8-max", enable_thinking=thinking)


@pytest.mark.asyncio
async def test_scoped_facts_and_checkpoint_history(agent_setup):
    graph, memory, model = agent_setup
    await graph.ainvoke({"messages": [HumanMessage(content="我喜欢茶")]}, config())
    memory.search.assert_awaited_once_with("我喜欢茶", "alice")
    memory.remember.assert_awaited_once_with(
        [{"role": "user", "content": "我喜欢茶"}], "alice", "thread-a"
    )
    prompt = model.ainvoke.call_args.args[0]
    assert isinstance(prompt[0], SystemMessage)
    assert "Untrusted" in prompt[1].content
    assert "喜欢茶" in prompt[1].content
    checkpoint = await graph.checkpointer.aget_tuple(config())
    history = messages_from_checkpoint(checkpoint.checkpoint)
    assert [m.content for m in history] == ["我喜欢茶", "你好"]
    assert history[-1].response_metadata["memory_status"] == "saved"
    await graph.ainvoke({"messages": [HumanMessage(content="继续")]}, config())
    assert [m.content for m in model.ainvoke.call_args.args[0][-3:]] == ["我喜欢茶", "你好", "继续"]
    await graph.ainvoke({"messages": [HumanMessage(content="新会话")]}, config(thread="thread-b"))
    assert len([m for m in model.ainvoke.call_args.args[0] if isinstance(m, HumanMessage)]) == 1
    memory.search.assert_awaited_with("新会话", "alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["search", "remember"])
async def test_memory_failure_preserves_response_and_reports_status(agent_setup, caplog, failure):
    graph, memory, model = agent_setup
    getattr(memory, failure).side_effect = ConnectionError("private-user-text")
    result = await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, config())
    assert result["messages"][0].content == "你好"
    expected = "saved_search_failed" if failure == "search" else "write_failed"
    assert result["messages"][0].response_metadata["memory_status"] == expected
    assert "private-user-text" not in caplog.text


@pytest.mark.asyncio
async def test_empty_results_delivery_notes_and_disabled_memory(agent_setup, monkeypatch):
    graph, memory, model = agent_setup
    memory.search.return_value = []
    await graph.ainvoke(
        {
            "messages": [
                AIMessage(content="播放记录", additional_kwargs={"voice_delivery": True}),
                HumanMessage(content="hello"),
            ]
        },
        config(),
    )
    memory.remember.assert_awaited_once_with(
        [{"role": "user", "content": "hello"}], "alice", "thread-a"
    )
    assert all("Untrusted retrieved" not in m.content for m in model.ainvoke.call_args.args[0])
    monkeypatch.setattr(agent_module, "get_mem0_service", lambda: None)
    result = await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, config())
    assert result["messages"][0].response_metadata["memory_status"] == "disabled"


@pytest.mark.asyncio
async def test_identity_required_before_memory_access(agent_setup):
    graph, memory, model = agent_setup
    with pytest.raises(ValueError, match="user_id"):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="hello")]}, {"configurable": {"thread_id": "t"}}
        )
    memory.search.assert_not_awaited()
    model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_failure_does_not_save_memory(agent_setup):
    graph, memory, model = agent_setup
    model.ainvoke.side_effect = RuntimeError("model unavailable")
    with pytest.raises(RuntimeError, match="model unavailable"):
        await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, config())
    memory.remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_emits_only_chat_tokens(agent_setup, monkeypatch):
    graph, memory, _ = agent_setup
    model = FakeListChatModel(responses=["你好。"])
    monkeypatch.setattr(agent_module, "get_model", lambda _: model)
    events = [
        event
        async for event in graph_events(
            graph,
            {"input": {"messages": [HumanMessage(content="hello")]}, "config": config()},
            "run",
        )
    ]
    assert "".join(e.content for e in events if e.type == "token") == "你好。"
    assert [e.content.content for e in events if e.type == "message"] == ["你好。"]
