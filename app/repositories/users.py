import asyncio
from typing import Any, LiteralString, cast

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg_pool import AsyncConnectionPool

from app.core import settings
from app.models import SQL_DIRECTORY
from app.schemas.auth import UserProfile

USER_SCHEMA_PATH = SQL_DIRECTORY / "create_app_users.sql"


class UserRepository:
    def __init__(self, conninfo: str, service_account_id: int):
        self.service_account_id = service_account_id
        self.pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
            conninfo,
            min_size=settings.POSTGRES_MIN_CONNECTIONS_PER_POOL,
            max_size=settings.POSTGRES_MAX_CONNECTIONS_PER_POOL,
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "application_name": settings.POSTGRES_APPLICATION_NAME + "-auth",
            },
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)
        schema_sql = await asyncio.to_thread(USER_SCHEMA_PATH.read_text, encoding="utf-8")
        async with self.pool.connection() as conn:
            await conn.execute(SQL(cast(LiteralString, schema_sql)))

    async def close(self) -> None:
        await self.pool.close()

    async def get_by_email(self, email: str) -> UserProfile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, nickname, email, image_url
                    FROM app_users
                    WHERE email = %s AND is_delete = 0
                    """,
                    (email,),
                )
            ).fetchone()
        return UserProfile.model_validate(row) if row else None

    async def get_by_id(self, user_id: int) -> UserProfile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, nickname, email, image_url
                    FROM app_users
                    WHERE id = %s AND is_delete = 0
                    """,
                    (user_id,),
                )
            ).fetchone()
        return UserProfile.model_validate(row) if row else None

    async def update_profile(
        self,
        user_id: int,
        nickname: str | None,
        image_url: str | None,
    ) -> UserProfile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE app_users
                    SET nickname = COALESCE(%s, nickname),
                        image_url = COALESCE(%s, image_url),
                        update_by = %s,
                        update_time = CURRENT_TIMESTAMP
                    WHERE id = %s AND is_delete = 0
                    RETURNING id, nickname, email, image_url
                    """,
                    (nickname, image_url, user_id, user_id),
                )
            ).fetchone()
        return UserProfile.model_validate(row) if row else None

    async def create_or_get(
        self, email: str, nickname: str, image_url: str | None
    ) -> tuple[UserProfile, bool]:
        existing = await self.get_by_email(email)
        if existing is not None:
            return existing, False
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO app_users (
                        nickname, email, image_url, create_by, update_by
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id, nickname, email, image_url
                    """,
                    (
                        nickname,
                        email,
                        image_url,
                        self.service_account_id,
                        self.service_account_id,
                    ),
                )
            ).fetchone()
        if row:
            return UserProfile.model_validate(row), True
        existing = await self.get_by_email(email)
        if existing is None:
            raise RuntimeError("User registration conflict could not be resolved")
        return existing, False
