from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatbotRole = Literal["user", "assistant"]


class ChatbotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ChatbotRole
    content: str = Field(min_length=1, max_length=8000)


class ChatbotPageContext(BaseModel):
    """Optional client-supplied snapshot of where the user is in the app.

    The FE sends ``path`` (always) and ``title`` (when available). The BE
    folds these into the system prompt so Doxie can reason about the
    current page without the FE having to pre-fetch any per-route summary.
    """
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, max_length=512)
    title: str | None = Field(default=None, max_length=256)


class ChatbotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatbotMessage] = Field(min_length=1, max_length=50)
    page_context: ChatbotPageContext | None = None


class ChatbotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
