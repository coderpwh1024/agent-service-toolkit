"""Keep fixed instructions ahead of per-turn context and optional cache boundaries."""

from langchain_core.messages import BaseMessage, SystemMessage

from core import settings
from schema.models import AlibabaModelName


def prepare_chat_prompt(
    prompt: list[BaseMessage],
    *,
    fixed_rules: str,
    runtime_context: str,
    model_name: str,
) -> list[BaseMessage]:
    messages = list(prompt)
    fixed_count = 0
    while (
        fixed_count < len(messages)
        and isinstance(messages[fixed_count], SystemMessage)
        and messages[fixed_count].name != "runtime_context"
    ):
        fixed_count += 1
    if fixed_rules:
        messages.insert(fixed_count, SystemMessage(content=fixed_rules))
        fixed_count += 1
    if runtime_context:
        runtime_index = next(
            (index for index, message in enumerate(messages) if message.name == "runtime_context"),
            None,
        )
        if runtime_index is None:
            messages.insert(
                max(fixed_count, len(messages) - 1),
                SystemMessage(content=runtime_context, name="runtime_context"),
            )
        else:
            current = messages[runtime_index]
            messages[runtime_index] = current.model_copy(
                update={"content": f"{current.content}\n{runtime_context}"}
            )
    if fixed_count and settings.DASHSCOPE_EXPLICIT_CACHE_ENABLED and model_name in AlibabaModelName:
        last = messages[fixed_count - 1]
        if isinstance(last.content, str):
            messages[fixed_count - 1] = last.model_copy(
                update={
                    "content": [
                        {
                            "type": "text",
                            "text": last.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                }
            )
    return messages
