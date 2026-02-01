"""Agent-specific Pydantic models.

This module contains all data structures used by the agent service,
replacing the previous TypedDict definitions with proper Pydantic models
for validation, serialization, and type safety.
"""

from typing import Any

from app.models.base import JsonModel


class WhenClause(JsonModel):
    """Condition clause for skill requirements."""

    config: str | None = None
    env: str | None = None
    equals: str | None = None


class RequirementSpec(JsonModel):
    """A single requirement specification for a skill."""

    name: str | None = None
    prompt: str | None = None
    example: str | None = None
    when: WhenClause | None = None


class MissingSkillRequirements(JsonModel):
    """Missing skill requirements grouped by category."""

    env: list[RequirementSpec] = []
    config: list[RequirementSpec] = []


class SkillInfo(JsonModel):
    """Information about a discovered skill."""

    name: str
    description: str
    path: str


class ConversationMessage(JsonModel):
    """A single message in a conversation.

    Note: We keep role as str rather than a Literal union because upstream
    callers may emit additional roles, and we don't want type strictness to break.
    """

    role: str
    content: str


class MemoryContext(JsonModel):
    """Memory context retrieved for a user.

    Contains facts, preferences, and agent name that should be injected
    into the agent's system prompt.
    """

    agent_name: str | None = None
    facts: list[str] = []
    preferences: list[str] = []


class AttachmentInfo(JsonModel):
    """Metadata about a file attachment from a user."""

    file_id: str | None = None
    file_name: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    is_image: bool = False


class ImageContentBlock(JsonModel):
    """A content block for vision model input."""

    type: str
    source: dict[str, Any] | None = None
    text: str | None = None


# Type alias for role literals where strictness is desired
Role = str  # Literal["user", "assistant", "system"] - kept as str for flexibility
