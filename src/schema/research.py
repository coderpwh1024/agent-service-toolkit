"""Contracts for the Mem0 assistant's isolated research tasks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)


class ResearchSource(BaseModel):
    title: str
    url: HttpUrl
    snippet: str = Field(default="", max_length=1000)


class ResearchResult(BaseModel):
    status: Literal["success", "empty", "timeout", "unavailable", "blocked", "invalid"]
    summary: str = ""
    sources: list[ResearchSource] = Field(default_factory=list)
