"""Agent-service helper components.

This package contains small, typed helpers extracted from `app.services.agent_service`.
They are intentionally dependency-light so they can be unit-tested in isolation.
"""

from app.services.agent.types import AttachmentInfo, ConversationMessage, MemoryContext, SkillInfo

__all__ = [
    "AttachmentInfo",
    "ConversationMessage",
    "MemoryContext",
    "SkillInfo",
]
