import asyncio
from typing import Any, LiteralString, cast

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg_pool import AsyncConnectionPool

from app.models import SQL_DIRECTORY
from app.schemas.files import UploadedFile

_COLUMNS = SQL("id, url, original_name, content_type, size_bytes, uploaded_at")


class FileRepository:
    def __init__(self, conninfo: str) -> None:
        self.pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
            conninfo,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)
        ddl = await asyncio.to_thread(
            SQL_DIRECTORY.joinpath("create_file_uploads.sql").read_text,
            encoding="utf-8",
        )
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            await conn.execute("SELECT pg_advisory_xact_lock(824567122)")
            await conn.execute(SQL(cast(LiteralString, ddl)))

    async def close(self) -> None:
        await self.pool.close()

    async def create(
        self,
        user_id: int,
        url: str,
        bucket_name: str,
        object_key: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
    ) -> UploadedFile:
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    SQL("""
                        INSERT INTO file_uploads (
                            user_id, url, bucket_name, object_key, original_name,
                            content_type, size_bytes, create_by, update_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING {}
                    """).format(_COLUMNS),
                    (
                        user_id,
                        url,
                        bucket_name,
                        object_key,
                        original_name,
                        content_type,
                        size_bytes,
                        user_id,
                        user_id,
                    ),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("File metadata was not saved")
            return UploadedFile.model_validate(row)

    async def get(self, file_id: int, user_id: int) -> UploadedFile | None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    SQL("""SELECT {} FROM file_uploads
                        WHERE id = %s AND user_id = %s AND is_delete = 0""").format(_COLUMNS),
                    (file_id, user_id),
                )
            ).fetchone()
        return UploadedFile.model_validate(row) if row else None
