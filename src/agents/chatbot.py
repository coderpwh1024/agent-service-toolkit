from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.func import entrypoint

from agents.email_commands import handle_email_command
from agents.research_delegation import answer_with_research
from core import get_model, settings
from skills.video_chat import VideoSkillSession


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
    thinking = configurable.get(
        "voice_enable_thinking",
        configurable.get("enable_thinking", settings.CHAT_LLM_ENABLE_THINKING),
    )
    model = get_model(
        model_name,
        enable_thinking=thinking
        if isinstance(thinking, bool)
        else settings.CHAT_LLM_ENABLE_THINKING,
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
    video = (
        VideoSkillSession(human, video_dialogue, messages[:-1], get_stream_writer())
        if human is not None
        else None
    )
    response = await answer_with_research(
        model, messages, config, video=video, enable_research=False
    )
    if video is not None:
        video_dialogue = video.saved
    return entrypoint.final(
        value={"messages": [response]},
        save={"messages": messages + [response], "video_dialogue": video_dialogue},
    )
