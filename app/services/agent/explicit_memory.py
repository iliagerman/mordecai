from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def extract_explicit_memory_text(message: str) -> tuple[str, str] | None:
    """Extract (kind, text) from explicit memory requests like 'remember ...'.

    Returns:
        ("fact"|"preference", extracted_text) or None.
    """

    raw = (message or "").strip()
    if not raw:
        return None

    lower = raw.lower().strip()

    # Avoid treating retrieval questions as storage requests.
    retrieval_prefixes = (
        "do you remember",
        "did you remember",
        "do u remember",
        "did u remember",
        "remember when ",
        "remeber when ",
    )
    if lower.startswith(retrieval_prefixes):
        return None

    prefixes = [
        "remember that ",
        "remember ",
        "please remember that ",
        "please remember ",
        "note that ",
        "note ",
        "save that ",
        "save this ",
    ]

    extracted: str | None = None
    for p in prefixes:
        if lower.startswith(p):
            extracted = raw[len(p) :].strip()
            break

    # Support punctuation patterns like "remember: ..."
    if extracted is None and (lower.startswith("remember") or lower.startswith("remeber")):
        lead_len = len("remember") if lower.startswith("remember") else len("remeber")
        extracted = raw[lead_len:].lstrip(" ,:-\t").strip()

    # Support "I need you to remember ..." forms
    if extracted is None:
        m = re.search(
            r"\b(?:i\s+(?:need|meed|ned)\s+you\s+to|i\s+want\s+you\s+to|can\s+you|could\s+you|please|pls)\s+(?:to\s+)?(?P<verb>remember|remeber)\b\s*(?:that\s+)?(?P<text>.+)$",
            raw,
            flags=re.IGNORECASE,
        )
        if m:
            extracted = (m.group("text") or "").strip()

    if not extracted:
        return None

    # Heuristic: preferences vs facts.
    pref_leads = (
        "i prefer ",
        "i like ",
        "i dislike ",
        "my preference ",
        "my preferences ",
        "prefer ",
    )

    extracted_lower = extracted.lower()
    kind = "preference" if extracted_lower.startswith(pref_leads) else "fact"
    if "favorite" in extracted_lower or "favourite" in extracted_lower:
        kind = "preference"

    return kind, extracted


def contains_sensitive_memory_text(text: str) -> bool:
    """Reject likely secrets/PII from being stored via explicit remember."""

    text_lower = (text or "").lower()
    sensitive_keywords = [
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "token",
        "bearer",
        "private_key",
        "private-key",
        "access_key",
        "access-key",
        "credential",
        "auth_token",
    ]
    if any(k in text_lower for k in sensitive_keywords):
        return True

    patterns = [
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        r"\b(?:password|passwd|pwd)\s*[:=]\s*\S+",
        r"\b(?:api[_-]?key|apikey)\s*[:=]\s*\S+",
        r"\b(?:token|bearer)\s*[:=]\s*\S+",
        r"\b(?:secret|private[_-]?key)\s*[:=]\s*\S+",
        r"\b(?:sk-|pk-)[A-Za-z0-9]{20,}",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",
        r"\b[0-9]{16}\b",
    ]
    for pattern in patterns:
        if re.search(pattern, text or "", re.IGNORECASE):
            return True

    return False


@dataclass(slots=True)
class ExplicitMemoryWriter:
    """Best-effort immediate memory write for explicit 'remember' requests."""

    config: Any
    memory_service: Any
    get_session_id: Any
    logger: Any

    def maybe_store(self, *, user_id: str, message: str) -> None:
        if not message:
            return
        if not getattr(self.config, "memory_enabled", False) or self.memory_service is None:
            return

        extracted = extract_explicit_memory_text(message)
        if not extracted:
            return

        kind, text = extracted
        if not text:
            return
        if contains_sensitive_memory_text(text):
            self.logger.info(
                "Skipping explicit memory store for user %s: looks sensitive",
                user_id,
            )
            return

        session_id = self.get_session_id(user_id)
        try:
            if kind == "preference" and hasattr(self.memory_service, "store_preference"):
                self.memory_service.store_preference(
                    user_id=user_id,
                    preference=text,
                    session_id=session_id,
                    write_to_short_term=True,
                )
            else:
                self.memory_service.store_fact(
                    user_id=user_id,
                    fact=text,
                    session_id=session_id,
                    replace_similar=True,
                    similarity_query=text,
                    write_to_short_term=True,
                    short_term_kind=kind,
                )
        except Exception as e:
            self.logger.warning(
                "Failed to store explicit memory for user %s: %s",
                user_id,
                e,
            )
