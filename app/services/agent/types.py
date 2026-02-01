from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class MemoryContext(TypedDict, total=False):
    agent_name: str
    facts: list[str]
    preferences: list[str]


class AttachmentInfo(TypedDict, total=False):
    file_id: str
    file_name: str
    file_path: str
    mime_type: str
    file_size: int
    is_image: bool


class SkillInfo(TypedDict):
    name: str
    description: str
    path: str


class ConversationMessage(TypedDict):
    # We keep this as `str` rather than a Literal union because upstream callers
    # may emit additional roles, and we don't want type strictness to break.
    role: str
    content: str


class WhenClause(TypedDict, total=False):
    config: str
    env: str
    equals: str


class RequirementSpec(TypedDict, total=False):
    name: str
    prompt: str
    example: str
    when: WhenClause


class MissingSkillRequirements(TypedDict, total=False):
    env: list[RequirementSpec]
    config: list[RequirementSpec]


Role = Literal["user", "assistant", "system"]
