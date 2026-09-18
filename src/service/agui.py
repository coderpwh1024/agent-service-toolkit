"""AG-UI protocol endpoint for the agent service.

Exposes any agent in the service over the AG-UI protocol (https://docs.ag-ui.com)
so it can be used with AG-UI compatible frontends like CopilotKit. The
LangGraph -> AG-UI event translation is handled by the official `ag-ui-langgraph`
package; this module only wires it into the service's agent registry, auth, and
tracing.

See docs/AGUI.md for usage, including how to connect a client.
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from ag_ui.core import EventType, RunAgentInput
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]

from agents import DEFAULT_AGENT, AgentGraph, get_agent
from core import settings
from service.access import authorize_thread, bind_user, repository
from service.agent_runner import thread_guard
from service.utils import ensure_model_available
from voice.persistence import VoiceRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agui")

# Managed by the protocol (thread_id comes from RunAgentInput) or the checkpointer,
# so clients may not override them via forwardedProps.configurable.
RESERVED_CONFIGURABLE_KEYS = {"thread_id", "checkpoint_id", "checkpoint_ns"}


def _base_config(input_data: RunAgentInput, agent_id: str) -> RunnableConfig:
    """Build the base RunnableConfig for an AG-UI run.

    Clients can pass configurable values (e.g. `model`, `user_id`, or custom agent
    config) in `forwardedProps.configurable` - the AG-UI equivalent of the vanilla
    API's `model` / `user_id` / `agent_config` fields. `thread_id` is taken from
    the AG-UI input by the `ag-ui-langgraph` package itself.
    """
    forwarded: dict[str, Any] = input_data.forwarded_props or {}
    configurable = forwarded.get("configurable") or {}
    if not isinstance(configurable, dict):
        raise HTTPException(status_code=422, detail="forwardedProps.configurable must be an object")
    if overlap := RESERVED_CONFIGURABLE_KEYS & configurable.keys():
        raise HTTPException(
            status_code=422,
            detail=f"forwardedProps.configurable contains reserved keys: {overlap}",
        )

    if (model := configurable.get("model")) is not None:
        ensure_model_available(model)

    callbacks: list[Any] = []
    if settings.LANGFUSE_TRACING:
        callbacks.append(CallbackHandler())

    configurable = dict(configurable)
    user_id = configurable.setdefault("user_id", str(uuid4()))

    return RunnableConfig(
        configurable=configurable,
        # Recorded in checkpoint metadata so AG-UI threads show up in /threads too.
        metadata={"user_id": user_id, "agent_id": agent_id},
        callbacks=callbacks,
    )


async def _event_stream(
    agent_id: str,
    graph: AgentGraph,
    input_data: RunAgentInput,
    config: RunnableConfig,
    encoder: EventEncoder,
    repo: VoiceRepository | None = None,
) -> AsyncGenerator[str, None]:
    async with thread_guard(repo, input_data.thread_id):
        agent = LangGraphAgent(name=agent_id, graph=graph, config=config)  # type: ignore[arg-type]
        async for event in agent.run(input_data):
            if event.type == EventType.RAW:
                continue
            yield encoder.encode(event)


@router.post("/run", operation_id="agui_run_default")
@router.post("/{agent_id}/run", operation_id="agui_run")
async def agui_run(
    input_data: RunAgentInput, request: Request, agent_id: str = DEFAULT_AGENT
) -> StreamingResponse:
    """
    Run an agent over the AG-UI protocol, streaming AG-UI events via SSE.

    Point an AG-UI client (e.g. CopilotKit's runtime or HttpAgent) at this endpoint.
    Use the same threadId across runs to continue a conversation - threads are
    persisted in the service's checkpointer and shared with the vanilla API.
    """
    try:
        graph: AgentGraph = get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    props = dict(input_data.forwarded_props or {})
    configurable = props.get("configurable") or {}
    if not isinstance(configurable, dict):
        raise HTTPException(422, "configurable must be an object")
    configurable = dict(configurable)
    configurable["user_id"] = bind_user(request.state.principal, configurable.get("user_id"))
    props["configurable"] = configurable
    input_data.forwarded_props = props
    repo = repository(request)
    await authorize_thread(
        repo,
        graph,
        input_data.thread_id,
        agent_id,
        request.state.principal,
        configurable["user_id"],
        create=True,
    )
    if repo and input_data.run_id:
        await repo.register_run(
            str(input_data.run_id), configurable["user_id"], input_data.thread_id
        )
    config = _base_config(input_data, agent_id)
    encoder = EventEncoder(accept=request.headers.get("accept", ""))
    return StreamingResponse(
        _event_stream(agent_id, graph, input_data, config, encoder, repo),
        media_type=encoder.get_content_type(),
    )
