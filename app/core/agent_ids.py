MAIN_AGENT_ID = "mem0-chatbot"


def canonical_agent_id(agent_id: str) -> str:
    """Accept the old wire identifier while preserving stored ownership metadata."""
    return MAIN_AGENT_ID if agent_id == "chatbot" else agent_id


def uses_streaming_chat(agent_id: str) -> bool:
    return canonical_agent_id(agent_id) == MAIN_AGENT_ID
