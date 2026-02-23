"""Skill secrets data access operations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.domain import UserSkillSecret
from app.models.orm import UserSkillSecretModel


class SkillSecretDAO(BaseDAO[UserSkillSecret]):
    """Data access object for per-user skill secrets.

    Secrets are stored as a JSON blob keyed by ``user_id``.
    All methods return Pydantic ``UserSkillSecret`` models, never SQLAlchemy
    objects.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _to_domain(model: UserSkillSecretModel) -> UserSkillSecret:
        return UserSkillSecret(
            user_id=model.user_id,
            secrets_data=SkillSecretDAO._parse_json(model.secrets_data),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, user_id: str) -> UserSkillSecret | None:
        """Return the full secrets record for *user_id*, or ``None``."""

        async with self._db.session() as session:
            result = await session.execute(
                select(UserSkillSecretModel).where(
                    UserSkillSecretModel.user_id == user_id
                )
            )
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return self._to_domain(model)

    async def get_secrets_data(self, user_id: str) -> dict[str, Any]:
        """Return the parsed ``secrets_data`` dict for *user_id*.

        Returns an empty dict if no record exists.
        """

        rec = await self.get(user_id)
        return rec.secrets_data if rec else {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert(self, user_id: str, secrets_data: dict[str, Any]) -> UserSkillSecret:
        """Insert or replace the entire secrets blob for *user_id*."""

        now = datetime.utcnow()
        blob = json.dumps(secrets_data, ensure_ascii=False)

        async with self._db.session() as session:
            result = await session.execute(
                select(UserSkillSecretModel).where(
                    UserSkillSecretModel.user_id == user_id
                )
            )
            model = result.scalar_one_or_none()

            if model is not None:
                model.secrets_data = blob
                model.updated_at = now
            else:
                model = UserSkillSecretModel(
                    user_id=user_id,
                    secrets_data=blob,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)

            await session.flush()
            return self._to_domain(model)

    async def upsert_key(
        self,
        user_id: str,
        key: str,
        value: Any,
        skill_name: str | None = None,
    ) -> UserSkillSecret:
        """Set a single key inside the secrets blob.

        If *skill_name* is provided the key is placed under a nested dict
        named after the skill, e.g. ``{"himalaya": {"OUTLOOK_EMAIL": "..."}}``.
        Otherwise it is placed at the top level.
        """

        data = await self.get_secrets_data(user_id)

        if skill_name:
            block = data.setdefault(skill_name, {})
            if not isinstance(block, dict):
                block = {}
                data[skill_name] = block
            block[key] = value
        else:
            data[key] = value

        return await self.upsert(user_id, data)

    async def delete_key(
        self,
        user_id: str,
        key: str,
        skill_name: str | None = None,
    ) -> UserSkillSecret | None:
        """Remove a single key from the secrets blob.

        Returns the updated record, or ``None`` if no record exists.
        """

        data = await self.get_secrets_data(user_id)
        if not data:
            return None

        if skill_name:
            block = data.get(skill_name)
            if isinstance(block, dict):
                block.pop(key, None)
        else:
            data.pop(key, None)

        return await self.upsert(user_id, data)
