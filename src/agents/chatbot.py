from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.func import entrypoint

from agents.email_commands import handle_email_command
from agents.video_commands import handle_video_command
from core import get_model, settings


@entrypoint()
async def chatbot(
    inputs: dict[str, list[BaseMessage]],
    *,
    previous: dict[str, Any] | None,
    config: RunnableConfig,
):
    messages = inputs["messages"]
    if previous:
        messages = previous["messages"] + messages

    configurable = config["configurable"]
    model_name = configurable.get("model", settings.DEFAULT_MODEL)
    thinking = configurable.get("voice_enable_thinking")
    model = (
        get_model(model_name, enable_thinking=thinking)
        if isinstance(thinking, bool)
        else get_model(model_name)
    )
    human = next(
        (message for message in reversed(inputs["messages"]) if isinstance(message, HumanMessage)),
        None,
    )
    video_dialogue = previous.get("video_dialogue") if previous else None
    if human:
        response, dialogue = await handle_email_command(
            human, previous.get("email_dialogue") if previous else None, model, config
        )
        if response is not None:
            return entrypoint.final(
                value={"messages": [response]},
                save={
                    "messages": messages + [response],
                    "email_dialogue": dialogue,
                    "video_dialogue": video_dialogue,
                },
            )
        response, video_dialogue = await handle_video_command(
            human, video_dialogue, model, config, writer=get_stream_writer()
        )
        if response is not None:
            return entrypoint.final(
                value={"messages": [response]},
                save={"messages": messages + [response], "video_dialogue": video_dialogue},
            )
    response = await model.ainvoke(messages)
    return entrypoint.final(
        value={"messages": [response]},
        save={"messages": messages + [response], "video_dialogue": video_dialogue},
    )
