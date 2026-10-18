"""Contracts for Mem0 templates stored in Nacos Prompt management."""

from string import Formatter
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

PromptText = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]


class Mem0Prompts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_system: PromptText
    memory_status_template: PromptText
    intent: PromptText
    match: PromptText
    extraction: PromptText
    extraction_system: PromptText

    @field_validator("memory_status_template")
    @classmethod
    def validate_status_template(cls, value: str) -> str:
        fields = [
            (name, spec, conversion)
            for _, name, spec, conversion in Formatter().parse(value)
            if name is not None
        ]
        if fields != [("status", "", None)]:
            raise ValueError("memory_status_template must contain exactly one {status} placeholder")
        return value


class Mem0PromptKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_system: PromptText = "mem0-chat-system"
    memory_status_template: PromptText = "mem0-memory-status"
    intent: PromptText = "mem0-memory-intent"
    match: PromptText = "mem0-memory-match"
    extraction: PromptText = "mem0-fact-extraction-instructions"
    extraction_system: PromptText = "mem0-fact-extraction-system"
