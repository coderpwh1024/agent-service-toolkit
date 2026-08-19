
from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel
from agents.lazy_agent import LazyLoadingAgent

from schema import AgentInfo


DEFAULT_AGENT = "research-assistant"

AgentGraph = CompiledStateGraph | Pregel
AgentGraphLike = CompiledStateGraph | Pregel | LazyLoadingAgent
