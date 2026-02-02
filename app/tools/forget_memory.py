"""Tool for deleting (forgetting) long-term memory records.

This provides a safe mechanism to remove incorrect/outdated facts or
preferences stored in Bedrock AgentCore Memory.

Safety defaults:
- dry_run=True by default (lists matches, does not delete)
"""

from __future__ import annotations

from typing import Literal, Protocol

from app.models.agent import ForgetMemoryResult

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


class _MemoryServiceProtocol(Protocol):
    def delete_similar_records(
        self,
        *,
        user_id: str,
        query: str,
        memory_type: str = "all",
        similarity_threshold: float = 0.7,
        dry_run: bool = True,
        max_matches: int = 25,
    ) -> ForgetMemoryResult: ...


_memory_service: _MemoryServiceProtocol | None = None
_current_user_id: str | None = None


def set_memory_context(memory_service: _MemoryServiceProtocol, user_id: str) -> None:
    """Set the memory service and user ID for the tool.

    Called by agent_service before creating the agent.
    """

    global _memory_service, _current_user_id
    _memory_service = memory_service
    _current_user_id = user_id


@tool(
    name="forget_memory",
    description=(
        "Forget (delete) incorrect/outdated long-term memories. "
        "Use this when you discover a stored fact/preference is wrong and should be removed. "
        "By default this is a dry-run: it will list matches without deleting. "
        "Set dry_run=false to actually delete matching records."
    ),
)
def forget_memory(
    query: str,
    memory_type: Literal["all", "facts", "preferences"] = "all",
    similarity_threshold: float = 0.7,
    dry_run: bool = True,
    max_matches: int = 10,
) -> str:
    """Find and optionally delete memory records similar to a query."""

    q = (query or "").strip()
    if not q:
        return "No query provided."

    if _memory_service is None:
        return "Memory service not available."

    if _current_user_id is None:
        return "User context not available."

    try:
        res = _memory_service.delete_similar_records(
            user_id=_current_user_id,
            query=q,
            memory_type=memory_type,
            similarity_threshold=similarity_threshold,
            dry_run=dry_run,
            max_matches=max_matches,
        )

        if res.matched == 0:
            return f"No matching memories found for '{q}'. Nothing to delete."

        lines: list[str] = []
        if res.dry_run:
            lines.append(f"Dry-run: found {res.matched} matching memories. NO DELETIONS PERFORMED.")
        else:
            lines.append(f"DELETE RUN: matched {res.matched} memories. Deleted: {res.deleted}.")
        lines.append("")
        for m in res.matches:
            lines.append(
                f"- [{m.namespace}] {m.text_preview} (score={m.score:.2f}, id={m.memory_record_id})"
            )

        if res.dry_run:
            lines.append("")
            lines.append("If you want me to delete these, rerun with dry_run=false (same query).")
        else:
            lines.append("")
            lines.append(
                "To verify, ask me to `search_memory` for the same topic (memory retrieval may be eventually consistent)."
            )

        return "\n".join(lines)
    except Exception as e:
        return f"Error forgetting memory: {e}"
