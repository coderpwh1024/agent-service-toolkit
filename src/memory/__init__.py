from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from memory.postgres import get_postgres_saver, get_postgres_store


def initialize_database() -> AbstractAsyncContextManager[AsyncPostgresSaver]:
    """Persist thread-scoped conversation state in PostgreSQL in every deployment."""
    return get_postgres_saver()


def initialize_store() -> AbstractAsyncContextManager[AsyncPostgresStore]:
    """Persist cross-thread memory in the same PostgreSQL database as checkpoints."""
    return get_postgres_store()


__all__ = ["initialize_database", "initialize_store"]
