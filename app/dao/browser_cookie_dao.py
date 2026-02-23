"""Browser cookie data access operations."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.dao.base import BaseDAO
from app.models.domain import BrowserCookie
from app.models.orm import BrowserCookieModel


class BrowserCookieDAO(BaseDAO[BrowserCookie]):
    """Data access object for browser cookie operations.

    Manages persistent cookie storage for browser automation sessions.
    All methods return Pydantic BrowserCookie models, never SQLAlchemy objects.
    """

    async def upsert_cookies(self, user_id: str, cookies: list[BrowserCookie]) -> int:
        """Bulk upsert cookies by (user_id, domain, name, path).

        Args:
            user_id: User identifier.
            cookies: List of cookies to upsert.

        Returns:
            Number of cookies upserted.
        """
        if not cookies:
            return 0

        now = datetime.utcnow()
        count = 0

        async with self._db.session() as session:
            for cookie in cookies:
                stmt = sqlite_insert(BrowserCookieModel).values(
                    user_id=user_id,
                    domain=cookie.domain,
                    name=cookie.name,
                    value=cookie.value,
                    path=cookie.path,
                    expires=cookie.expires,
                    http_only=cookie.http_only,
                    secure=cookie.secure,
                    same_site=cookie.same_site,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id", "domain", "name", "path"],
                    set_={
                        "value": stmt.excluded.value,
                        "expires": stmt.excluded.expires,
                        "http_only": stmt.excluded.http_only,
                        "secure": stmt.excluded.secure,
                        "same_site": stmt.excluded.same_site,
                        "updated_at": now,
                    },
                )
                await session.execute(stmt)
                count += 1

        return count

    async def get_cookies(
        self, user_id: str, domain: str | None = None
    ) -> list[BrowserCookie]:
        """Retrieve cookies for a user, optionally filtered by domain.

        Args:
            user_id: User identifier.
            domain: Optional domain filter.

        Returns:
            List of BrowserCookie domain models.
        """
        async with self._db.session() as session:
            stmt = select(BrowserCookieModel).where(BrowserCookieModel.user_id == user_id)
            if domain is not None:
                stmt = stmt.where(BrowserCookieModel.domain == domain)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [
                BrowserCookie(
                    id=row.id,
                    user_id=row.user_id,
                    domain=row.domain,
                    name=row.name,
                    value=row.value,
                    path=row.path,
                    expires=row.expires,
                    http_only=row.http_only,
                    secure=row.secure,
                    same_site=row.same_site,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    async def delete_cookies(self, user_id: str, domain: str | None = None) -> int:
        """Delete cookies for a user, optionally filtered by domain.

        Args:
            user_id: User identifier.
            domain: Optional domain filter. If None, deletes all cookies for the user.

        Returns:
            Number of cookies deleted.
        """
        async with self._db.session() as session:
            stmt = delete(BrowserCookieModel).where(BrowserCookieModel.user_id == user_id)
            if domain is not None:
                stmt = stmt.where(BrowserCookieModel.domain == domain)

            result = await session.execute(stmt)
            return result.rowcount

    async def delete_expired(self, user_id: str) -> int:
        """Delete expired cookies for a user.

        Args:
            user_id: User identifier.

        Returns:
            Number of expired cookies deleted.
        """
        now = datetime.utcnow()
        async with self._db.session() as session:
            stmt = (
                delete(BrowserCookieModel)
                .where(BrowserCookieModel.user_id == user_id)
                .where(BrowserCookieModel.expires.isnot(None))
                .where(BrowserCookieModel.expires < now)
            )
            result = await session.execute(stmt)
            return result.rowcount
