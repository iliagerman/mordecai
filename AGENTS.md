# AI Agent Coding Rules

This document defines the coding standards and best practices for the Mordecai AI agent platform. These rules must be followed when implementing or modifying agent-related code.

## Core Principles

1. **Type Safety First**: Use Pydantic models for all data structures
2. **Separation of Concerns**: Services → DAOs → Database (no skipping layers)
3. **No `Any` Types**: Use proper interfaces, protocols, or concrete types
4. **Explicit Over Implicit**: Prefer explicit types over `dict` and `TypedDict`
5. **Keep Files Small**: Split large files into focused modules under 500 lines
6. **No Direct `os.environ` in Services**: Centralize runtime env mutation behind a dedicated service/helper

---

## Rule 1: Use Pydantic Models for Data Structures

### ✅ Correct

```python
from app.models.base import JsonModel

class AttachmentInfo(JsonModel):
    file_id: str | None = None
    file_name: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    is_image: bool = False
```

### ❌ Incorrect

```python
from typing import TypedDict

class AttachmentInfo(TypedDict, total=False):
    file_id: str
    file_name: str
    # ...
```

### ❌ Also Incorrect

```python
# Using plain dict
def get_attachment(file_id: str) -> dict:
    return {"file_id": file_id, "file_name": "..."}
```

**Rationale**: Pydantic models provide:
- Runtime validation
- Automatic serialization/deserialization
- Better IDE support (autocomplete, type hints)
- Clearer error messages
- JSON schema generation

---

## Rule 2: Services Must Never Access Database Directly

### ✅ Correct

```python
class AgentService:
    def __init__(self, memory_dao: MemoryDAO, user_dao: UserDAO):
        self._memory_dao = memory_dao
        self._user_dao = user_dao

    async def get_user_memory(self, user_id: str) -> MemoryContext:
        memories = await self._memory_dao.get_all_for_user(user_id)
        return self._build_context(memories)
```

### ❌ Incorrect

```python
from sqlalchemy import select

class AgentService:
    def __init__(self, db: Database):
        self._db = db

    async def get_user_memory(self, user_id: str) -> MemoryContext:
        async with self._db.session() as session:
            result = await session.execute(select(Memory).where(...))  # NO!
```

**Rationale**:
- DAOs encapsulate all database logic
- Services should focus on business logic
- Easier to test (mock DAOs)
- Database schema changes don't affect services

---

## Rule 3: Avoid `dict` Type Annotations

### ✅ Correct

```python
def process_skills(skills: list[SkillInfo]) -> SkillRequirements:
    ...

def get_config() -> AgentConfig:
    ...
```

### ❌ Incorrect

```python
def process_skills(skills: list[dict]) -> dict[str, list[dict]]:
    ...

def get_config() -> dict:
    ...
```

**Exception**: Internal implementation details where the dict is truly ephemeral and never crosses boundaries.

---

## Rule 4: Avoid `Any` Types - Use Protocols or Interfaces

### ✅ Correct

```python
from typing import Protocol

class SkillServiceProtocol(Protocol):
    async def download_skill(self, url: str) -> SkillMetadata: ...
    async def list_skills(self) -> list[SkillMetadata]: ...

class AgentService:
    def __init__(self, skill_service: SkillServiceProtocol):
        self._skill_service = skill_service
```

### ❌ Incorrect

```python
class AgentService:
    def __init__(self, skill_service: Any):
        self._skill_service = skill_service
```

**Rationale**: `Any` disables type checking entirely, defeating the purpose of using a type checker.

---

## Rule 5: Internal State Should Be Encapsulated

### ✅ Correct

```python
class MessageCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, user_id: str, amount: int = 1) -> int:
        self._counts[user_id] = self._counts.get(user_id, 0) + amount
        return self._counts[user_id]

    def get(self, user_id: str) -> int:
        return self._counts.get(user_id, 0)

    def reset(self, user_id: str) -> None:
        self._counts[user_id] = 0
```

### ❌ Incorrect

```python
class AgentService:
    def __init__(self):
        self._user_message_counts: dict[str, int] = {}

    def increment_message_count(self, user_id: str, count: int = 1) -> int:
        current = self._user_message_counts.get(user_id, 0)
        self._user_message_counts[user_id] = current + count
        return self._user_message_counts[user_id]
```

**Rationale**: Encapsulating state in dedicated classes makes code:
- Easier to test
- More reusable
- Clearer in intent
- Less error-prone

---

## Rule 6: Return Typed Models, Not `list[dict]`

### ✅ Correct

```python
def discover_skills(self, user_id: str) -> list[SkillInfo]:
    skills_dir = self._get_skills_dir(user_id)
    return [SkillInfo.from_skill_dir(d) for d in skills_dir.iterdir()]
```

### ❌ Incorrect

```python
def discover_skills(self, user_id: str) -> list[dict]:
    skills_dir = self._get_skills_dir(user_id)
    return [{"name": d.name, "path": str(d)} for d in skills_dir.iterdir()]
```

---

## Rule 7: Keep Files Small - Split Large Services

### ✅ Correct

```
app/services/agent/
├── __init__.py                 # Exports AgentService
├── agent_service.py            # <500 lines, orchestration only
├── session_management.py        # new_session, _trigger_extraction
├── message_processing.py        # process_message, process_image_message
├── attachment_handler.py        # process_message_with_attachments
├── agent_creation.py            # _create_agent, get_or_create_agent
└── state/                       # State managers (already split)
```

### ❌ Incorrect

```python
# agent_service.py: 1740+ lines - TOO LARGE
class AgentService:
    # 20+ methods doing unrelated things
    # Hard to navigate, test, and maintain
```

**Guidelines**:
- **Target**: Keep service files under 500 lines
- **Split by responsibility**: Each module should have one clear purpose
- **Orchestration only**: Service files should compose, not implement
- **Extract helper modules**: Move complex logic to dedicated files

**When to Split**:
- File exceeds 500 lines
- File contains multiple distinct responsibilities (session mgmt, message processing, etc.)
- Testing becomes difficult due to tight coupling
- Navigation becomes cumbersome

**How to Split**:
1. Identify groups of related methods (e.g., all session-related)
2. Create a new module in `app/services/{service}/`
3. Move methods and related private helpers
4. Use composition: main service imports/uses helper modules
5. Update imports in tests and other callers

**Example Split**:
- `session_management.py` - `new_session()`, `_trigger_extraction_and_clear()`
- `message_processing.py` - `process_message()`, `_maybe_run_simple_skill_echo_for_tests()`
- `attachment_handler.py` - `process_image_message()`, `process_message_with_attachments()`
- `agent_creation.py` - `_create_agent()`, `get_or_create_agent()`

---

## Rule 8: Do Not Read/Write `os.environ` in Services

Services should not access `os.environ` directly. This avoids hidden global side-effects and makes
configuration behavior easier to reason about and test.

**Approved approach**: use the runtime env wrapper service (see `app/services/runtime_env_service.py`).

### ✅ Correct

```python
from app.services.runtime_env_service import RuntimeEnvService


class SomeService:
    def __init__(self, env: RuntimeEnvService) -> None:
        self._env = env

    def configure(self) -> None:
        self._env.set("AWS_REGION", "us-east-1")
        self._env.unset("AWS_SESSION_TOKEN")
```

### ❌ Incorrect

```python
import os


class SomeService:
    def configure(self) -> None:
        os.environ["AWS_REGION"] = "us-east-1"  # NO
```

**Exception**: low-level configuration/bootstrap modules whose job is explicitly to materialize
env for subprocesses (e.g., config hot-reload helpers) may touch `os.environ`.

---

## File Organization

### Where Models Live

| Type                             | Location                               |
| -------------------------------- | -------------------------------------- |
| Domain models (returned by DAOs) | `app/models/domain.py`                 |
| Agent-specific models            | `app/models/agent.py`                  |
| Request/Response models          | Router files or `app/models/api.py`    |
| Configuration                    | `app/config.py` (extends BaseSettings) |

### Where State Managers Live

Internal state managers live in `app/services/{service}/state/`:
```
app/services/agent/state/
├── __init__.py
├── session_manager.py
├── message_counter.py
└── ...
```

---

## Migration Checklist

When migrating existing code to follow these rules:

1. [ ] Replace `TypedDict` with `JsonModel` subclasses
2. [ ] Replace `dict[K, V]` return types with specific models
3. [ ] Create state manager classes for internal dict state
4. [ ] Replace `Any` with protocols or concrete types
5. [ ] Update all imports
6. [ ] Update tests to use new models
7. [ ] Run `just typecheck` - must pass
8. [ ] Run `just test-unit` - all tests must pass
