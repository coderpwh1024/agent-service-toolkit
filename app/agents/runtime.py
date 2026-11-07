from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel

from app.agents.mem0_chat import mem0_chat
from app.core.agent_ids import MAIN_AGENT_ID
from app.schemas import AgentInfo

DEFAULT_AGENT = MAIN_AGENT_ID


AgentGraph = CompiledStateGraph | Pregel


def get_agent(agent_id: str = DEFAULT_AGENT) -> AgentGraph:
    if agent_id != DEFAULT_AGENT:
        raise KeyError(agent_id)
    return mem0_chat


def get_all_agent_info() -> list[AgentInfo]:
    return [
        AgentInfo(
            key=DEFAULT_AGENT,
            description="A conversational assistant with user-scoped Mem0 memory in PostgreSQL.",
        )
    ]
