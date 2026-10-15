"""User-scoped management of Mem0 facts and their audit history."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MemoryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
MemoryUserId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: MemoryText


class MemoryRecord(BaseModel):
    id: UUID
    memory: str
    user_id: str
    agent_id: str
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryList(BaseModel):
    memories: list[MemoryRecord]
    next_cursor: UUID | None = None


class MemoryHistoryEntry(BaseModel):
    id: int
    memory_id: UUID
    event: Literal["ADD", "UPDATE", "DELETE"]
    old_memory: str | None = None
    new_memory: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryHistory(BaseModel):
    history: list[MemoryHistoryEntry]
    next_cursor: int | None = None


class MemoryDeleteResult(BaseModel):
    deleted_count: int = Field(ge=0)
