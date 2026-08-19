from dataclasses import dataclass
from agents.chatbot import chatbot
from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel
from agents.lazy_agent import LazyLoadingAgent

from schema import AgentInfo

DEFAULT_AGENT = "research-assistant"

AgentGraph = CompiledStateGraph | Pregel
AgentGraphLike = CompiledStateGraph | Pregel | LazyLoadingAgent


@dataclass
class Agent:
    description: str
    graph_like: AgentGraphLike


agents: dict[str, Agent] = {
    "chatbot": Agent(description="一个简单的聊天机器", graph_like=chatbot),
}
