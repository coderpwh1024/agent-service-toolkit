import asyncio
import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.func import entrypoint

import app.agents.runtime as runtime
from app.agents.runtime import DEFAULT_AGENT
from app.core import settings
from app.main import app


@pytest.fixture
def main_client(monkeypatch):
    module = importlib.import_module("app.agents.mem0_chat")
    monkeypatch.setattr(settings, "AUTH_SECRET", None)
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", None)
    monkeypatch.setattr(settings, "VOICE_ENABLED", False)
    monkeypatch.setattr(settings, "VIDEO_SKILL_ENABLED", False)
    monkeypatch.setattr(app.state, "voice_repository", None, raising=False)
    monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    model = FakeListChatModel(responses=["主 Agent 回答。", "继续回答。"])
    monkeypatch.setattr(module, "get_model", lambda *args, **kwargs: model)
    graph = module.mem0_chat.copy()
    graph.checkpointer = MemorySaver()
    monkeypatch.setattr(runtime, "mem0_chat", graph)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, graph
    finally:
        client.close()


def test_metadata_only_advertises_the_main_agent(main_client):
    client, _ = main_client
    metadata = client.get("/info").json()["data"]
    assert metadata["default_agent"] == DEFAULT_AGENT
    assert [agent["key"] for agent in metadata["agents"]] == [DEFAULT_AGENT]
    capabilities = client.get("/voice/capabilities").json()["data"]
    assert capabilities["default_agent"] == DEFAULT_AGENT
    assert [agent["id"] for agent in capabilities["agents"]] == [DEFAULT_AGENT]
    assert capabilities["agents"][0]["speech_mode"] == "streaming"
    assert capabilities["agents"][0]["cancel_execution"] is True


@pytest.mark.parametrize("agent_id", [DEFAULT_AGENT, "chatbot"])
def test_main_agent_keeps_history_and_thread_metadata(main_client, agent_id):
    client, graph = main_client
    thread_id = f"compatibility-{agent_id}"
    for text in ["你好", "继续"]:
        result = client.post(
            f"/{agent_id}/invoke",
            json={"message": text, "thread_id": thread_id, "user_id": "7"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["type"] == "ai"
        assert result.json()["data"]["run_id"]
    history = client.post(f"/{agent_id}/history", json={"thread_id": thread_id})
    assert history.status_code == 200
    assert [message["content"] for message in history.json()["data"]["messages"]] == [
        "你好",
        "主 Agent 回答。",
        "继续",
        "继续回答。",
    ]
    checkpoint = asyncio.run(
        graph.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    )
    assert checkpoint.metadata["agent_id"] == agent_id
    assert checkpoint.metadata["user_id"] == "7"
    listing = client.get(f"/{agent_id}/threads", params={"user_id": "7"})
    assert listing.status_code == 200
    assert [thread["thread_id"] for thread in listing.json()["data"]["threads"]] == [thread_id]


def test_legacy_chatbot_checkpoint_continues_without_data_migration(main_client):
    client, graph = main_client

    @entrypoint(checkpointer=graph.checkpointer)
    async def chatbot(inputs, *, previous):
        messages = inputs["messages"] + [AIMessage(content="旧版本回答。")]
        return entrypoint.final(value={"messages": messages[-1:]}, save={"messages": messages})

    asyncio.run(
        chatbot.ainvoke(
            {"messages": [HumanMessage(content="旧版本问题")]},
            {
                "configurable": {"thread_id": "legacy", "user_id": "7"},
                "metadata": {"agent_id": "chatbot", "user_id": "7"},
            },
        )
    )
    response = client.post(
        "/chatbot/invoke", json={"message": "继续", "thread_id": "legacy", "user_id": "7"}
    )
    assert response.status_code == 200, response.text
    history = client.post("/chatbot/history", json={"thread_id": "legacy"}).json()["data"]
    assert [message["content"] for message in history["messages"]] == [
        "旧版本问题",
        "旧版本回答。",
        "继续",
        "主 Agent 回答。",
    ]


@pytest.mark.parametrize(
    "agent_id",
    [
        "research-assistant",
        "rag-assistant",
        "command-agent",
        "bg-task-agent",
        "langgraph-supervisor-agent",
        "langgraph-supervisor-hierarchy-agent",
        "interrupt-agent",
        "knowledge-base-agent",
        "github-mcp-agent",
    ],
)
def test_removed_agents_return_not_found_across_chat_endpoints(main_client, agent_id):
    client, _ = main_client
    for route in ["invoke", "stream"]:
        assert client.post(f"/{agent_id}/{route}", json={"message": "你好"}).status_code == 404
    assert client.post(f"/{agent_id}/history", json={"thread_id": "old"}).status_code == 404
    for route in ["threads", "chat-records", f"chat-records/{datetime.now(UTC).date()}"]:
        assert client.get(f"/{agent_id}/{route}", params={"user_id": "7"}).status_code == 404
    assert (
        client.post(
            f"/agui/{agent_id}/run",
            json={
                "threadId": "old",
                "runId": "removed",
                "messages": [],
                "tools": [],
                "context": [],
                "state": {},
                "forwardedProps": {},
            },
        ).status_code
        == 404
    )
