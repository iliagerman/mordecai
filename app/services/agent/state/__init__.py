"""State manager classes for AgentService.

These classes encapsulate internal state management, providing type-safe
operations and clear separation of concerns.
"""

from app.services.agent.state.agent_name_registry import AgentNameRegistry
from app.services.agent.state.conversation_history import ConversationHistory
from app.services.agent.state.extraction_lock import ExtractionLockRegistry
from app.services.agent.state.message_counter import MessageCounter
from app.services.agent.state.session_manager import SessionManager
from app.services.agent.state.stm_cache import StmCache

__all__ = [
    "AgentNameRegistry",
    "ConversationHistory",
    "ExtractionLockRegistry",
    "MessageCounter",
    "SessionManager",
    "StmCache",
]
