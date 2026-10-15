"""Internal contracts for conversational memory selection and confirmation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.memory import MemoryText


class MemoryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["none", "list", "history", "update", "delete", "delete_all"]
    query: str = Field(default="", max_length=1000)
    text: MemoryText | None = None
    selection: int | None = Field(default=None, ge=1, le=10)
    other_user: bool = False


class MemoryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matching_indices: list[int] = Field(default_factory=list, max_length=10)


class MemoryTarget(BaseModel):
    id: UUID
    memory: str
    updated_at: str | None = None


class MemorySnapshot(BaseModel):
    count: int = Field(ge=0)
    revision: str


class MemoryMutation(BaseModel):
    action: Literal["update", "delete", "delete_all"]
    target: MemoryTarget | None = None
    text: MemoryText | None = None
    revision: str | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "MemoryMutation":
        if self.action == "delete_all":
            if not self.revision or self.target is not None or self.text is not None:
                raise ValueError("Bulk deletion requires only a memory snapshot revision")
        elif self.target is None or (self.action == "update" and self.text is None):
            raise ValueError("A target and, for updates, replacement text are required")
        return self


class MemoryMutationResult(BaseModel):
    status: Literal["updated", "unchanged", "deleted", "cleared"]
    deleted_count: int = 0


class PendingMemoryCommand(BaseModel):
    action: Literal["update", "delete", "delete_all", "history"]
    stage: Literal["select", "confirm"]
    choices: list[MemoryTarget] = Field(default_factory=list, max_length=10)
    text: MemoryText | None = None
    snapshot: MemorySnapshot | None = None
    expires_at: datetime


class MemoryDialogue(BaseModel):
    user_id: str
    pending: PendingMemoryCommand | None = None
    choices: list[MemoryTarget] = Field(default_factory=list, max_length=10)
    last_target: MemoryTarget | None = None
