"""The public conversation output contract; no harness-private payloads."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NativeBlock(BaseModel):
    type: Literal["native"]
    sessionId: str
    turnId: str | None
    itemId: str
    model: str
    turnStatus: Literal["streaming", "completed", "interrupted"]


class ThinkingInterval(BaseModel):
    startedAt: int
    finishedAt: int | None


class ReasoningBlock(BaseModel):
    type: Literal["reasoning"]
    text: list[str]
    summary: list[str]
    status: Literal["streaming", "completed", "interrupted"]
    startedAt: int | None
    finishedAt: int | None
    intervals: list[ThinkingInterval]
    updatedAt: int | None = None


class PublicMessage(BaseModel):
    id: str
    role: str
    content: str
    status: str
    blocks: list[NativeBlock | ReasoningBlock | dict[str, Any]] = Field(default_factory=list)
    createdAt: int


class ConversationSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    messages: list[PublicMessage]
    eventCursor: int


class MessageDelta(BaseModel):
    messageId: str
    role: str
    content: str
    status: str
    blocks: list[NativeBlock | ReasoningBlock | dict[str, Any]]
    createdAt: int
    offset: int


class ConversationEvent(BaseModel):
    id: int
    kind: str
    payload: MessageDelta | dict[str, Any]
