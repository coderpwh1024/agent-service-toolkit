import pytest

from app.agents import DEFAULT_AGENT, get_agent, get_all_agent_info
from app.agents.mem0_chat import mem0_chat
from app.api.dependencies import get_agent as api_agent

REMOVED_AGENTS = [
    "chatbot",
    "research-assistant",
    "rag-assistant",
    "command-agent",
    "bg-task-agent",
    "langgraph-supervisor-agent",
    "langgraph-supervisor-hierarchy-agent",
    "interrupt-agent",
    "knowledge-base-agent",
    "github-mcp-agent",
    "nonexistent-agent",
]


def test_only_mem0_is_advertised() -> None:
    assert [agent.key for agent in get_all_agent_info()] == [DEFAULT_AGENT]
    assert get_agent(DEFAULT_AGENT) is mem0_chat


@pytest.mark.parametrize("agent_id", REMOVED_AGENTS)
def test_removed_agents_cannot_be_resolved(agent_id: str) -> None:
    with pytest.raises(KeyError):
        get_agent(agent_id)


def test_legacy_wire_value_only_resolves_at_the_api_boundary() -> None:
    assert api_agent("chatbot") is get_agent(DEFAULT_AGENT)
