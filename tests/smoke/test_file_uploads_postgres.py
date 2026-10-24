"""Exercise file metadata constraints and ownership on an isolated PostgreSQL schema."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

from memory.postgres import get_postgres_connection_string
from service.file_uploads import FileRepository


@pytest.mark.docker
@pytest.mark.asyncio
async def test_file_upload_schema_constraints_and_owned_queries() -> None:
    conninfo = get_postgres_connection_string()
    schema = "file_smoke_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    repo = FileRepository(make_conninfo(conninfo, options=f"-csearch_path={schema}"))
    try:
        await repo.open()
        await repo.open()
        file = await repo.create(
            7, "https://cdn.example.com/key", "bucket", "key", "file.txt", "text/plain", 4
        )
        assert file.id > 0 and file.uploaded_at.tzinfo is not None
        assert await repo.get(file.id, 7) == file
        assert await repo.get(file.id, 8) is None
        async with repo.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT create_by, update_by, create_time, update_time, uploaded_at, is_delete FROM file_uploads WHERE id = %s",
                    (file.id,),
                )
            ).fetchone()
            assert row is not None
            assert row["create_by"] == row["update_by"] == 7
            assert row["create_time"] == row["update_time"] == row["uploaded_at"]
            assert row["is_delete"] == 0
        with pytest.raises(psycopg.errors.UniqueViolation):
            await repo.create(
                7, "https://cdn.example.com/key", "bucket", "key", "file.txt", "text/plain", 4
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await repo.create(
                7, "https://cdn.example.com/empty", "bucket", "empty", "empty.txt", "text/plain", 0
            )
        async with repo.pool.connection() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "UPDATE file_uploads SET is_delete = 2, update_by = 7, update_time = CURRENT_TIMESTAMP WHERE id = %s",
                    (file.id,),
                )
            await conn.execute(
                "UPDATE file_uploads SET is_delete = 1, update_by = 7, update_time = CURRENT_TIMESTAMP WHERE id = %s",
                (file.id,),
            )
        assert await repo.get(file.id, 7) is None
        with pytest.raises(psycopg.errors.UniqueViolation):
            await repo.create(
                7, "https://cdn.example.com/key", "bucket", "key", "file.txt", "text/plain", 4
            )
    finally:
        await repo.close()
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            await conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))
