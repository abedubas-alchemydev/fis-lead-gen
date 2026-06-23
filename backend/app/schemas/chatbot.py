from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatbotRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class ChatTurnUsage:
    """Per-assistant-turn Gemini usage + telemetry.

    A plain frozen dataclass (not a Pydantic model) because it never
    crosses the API boundary — it flows from the chat service into the
    history layer, which copies the fields onto the ``chatbot_message``
    row. ``ChatbotResponse`` deliberately stays ``{reply}`` only.

    Token counts are ``int | None``: ``None`` means Gemini didn't report
    them (it omits ``usageMetadata`` on the terminal streaming chunk
    sometimes), persisted as a NULL column distinct from a real ``0``.
    ``tool_call_count`` and ``latency_ms`` are always known locally, so
    they're plain ``int``.
    """

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    tool_call_count: int
    latency_ms: int


class ChatbotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ChatbotRole
    content: str = Field(min_length=1, max_length=8000)


class ChatbotPageContext(BaseModel):
    """Optional client-supplied snapshot of where the user is in the app.

    The FE sends ``path`` (always) and ``title`` (when available). The BE
    folds these into the system prompt so Doxie can reason about the
    current page without the FE having to pre-fetch any per-route summary.

    ``entity_type`` / ``entity_id`` are sent only from firm detail routes
    (numeric-id ``/master-list/{id}`` → ``broker_dealer``, numeric-id
    ``/advisor-list/{id}`` → ``investment_advisor``) so the BE can ground
    "this firm" to the exact row being viewed. Both optional — old clients
    omit them — and never persisted (only path/title are stored on the
    ``chatbot_message`` row). Unknown values fall back silently to the
    path/title behaviour rather than erroring.
    """
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, max_length=512)
    title: str | None = Field(default=None, max_length=256)
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: int | None = Field(default=None)


class ChatbotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatbotMessage] = Field(min_length=1, max_length=50)
    page_context: ChatbotPageContext | None = None


class ChatbotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str


class ChatbotHistoryMessage(BaseModel):
    """One persisted message as returned by GET /chatbot/messages."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: ChatbotRole
    content: str
    created_at: datetime


class ChatbotHistoryResponse(BaseModel):
    """Persisted history for the user's active conversation."""
    model_config = ConfigDict(extra="forbid")

    conversation_id: int
    messages: list[ChatbotHistoryMessage]


class ChatbotNewConversationResponse(BaseModel):
    """Result of POST /chatbot/conversations/new — caller drops local
    history and starts a fresh thread keyed on this id."""
    model_config = ConfigDict(extra="forbid")

    conversation_id: int


class ChatbotConversationSummary(BaseModel):
    """One row in GET /chatbot/conversations (and the body returned by
    POST /chatbot/conversations/{id}/reopen).

    ``preview`` is the conversation's first user message truncated for
    list display ("New conversation" when no user turn exists yet), so
    the history browser is scannable without loading any transcript.
    """
    model_config = ConfigDict(extra="forbid")

    id: int
    started_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    is_active: bool
    message_count: int
    preview: str


class ChatbotConversationListResponse(BaseModel):
    """Newest-first listing of the current user's conversations."""
    model_config = ConfigDict(extra="forbid")

    conversations: list[ChatbotConversationSummary]


class ChatbotEmbeddingBackfillEntityCounts(BaseModel):
    """Per-entity-type slice of a backfill run."""
    model_config = ConfigDict(extra="forbid")

    embedded: int
    skipped: int
    failed: int


class ChatbotEmbeddingBackfillResponse(BaseModel):
    """Counts returned by POST /chatbot/embeddings/backfill.

    Top-level counts stay the all-entities totals (the original contract);
    the optional per-entity breakdowns were added when the backfill grew
    to cover investment advisors alongside broker-dealers.
    """
    model_config = ConfigDict(extra="forbid")

    embedded: int
    skipped: int
    failed: int
    broker_dealers: ChatbotEmbeddingBackfillEntityCounts | None = None
    investment_advisors: ChatbotEmbeddingBackfillEntityCounts | None = None
