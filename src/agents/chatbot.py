from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.func import entrypoint

from core import get_model, settings


@entrypoint()
async def chatbot(
    inputs: dict[str, list[BaseMessage]],
    *,
    previous: dict[str, list[BaseMessage]],
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
    response = await model.ainvoke(messages)
    return entrypoint.final(
        value={"messages": [response]}, save={"messages": messages + [response]}
    )
