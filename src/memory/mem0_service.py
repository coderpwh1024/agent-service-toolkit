"""Bounded async access to Mem0's synchronous SDK and PostgreSQL resources."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from time import monotonic
from typing import Any

from psycopg_pool import ConnectionPool

from core.settings import settings
from memory.mem0_sdk import build_memory, close_memory
from memory.postgres import get_postgres_connection_string, validate_postgres_config

logger = logging.getLogger(__name__)
AGENT_ID = "mem0-chatbot"


class Mem0Service:
    def __init__(self, memory: Any, executor: ThreadPoolExecutor, *, max_pending: int = 8) -> None:
        self.memory = memory
        self.executor = executor
        self.pending: set[asyncio.Future[Any]] = set()
        self.max_pending = max_pending

    async def _call(self, operation: str, function: Callable[..., Any], **kwargs: Any) -> Any:
        if len(self.pending) >= self.max_pending:
            raise RuntimeError("Mem0 is busy; retry on the next turn")
        started = monotonic()
        future = asyncio.get_running_loop().run_in_executor(
            self.executor, partial(function, **kwargs)
        )
        self.pending.add(future)
        future.add_done_callback(self.pending.discard)
        try:
            async with asyncio.timeout(settings.MEM0_TIMEOUT_SECONDS):
                result = await asyncio.shield(future)
            self.pending.discard(future)
            logger.info(
                "Mem0 operation completed operation=%s duration_ms=%d",
                operation,
                int((monotonic() - started) * 1000),
            )
            return result
        except (Exception, asyncio.CancelledError):
            # Cancellation cannot kill a running SDK worker. Observe its result and
            # keep capacity reserved until it finishes, including during shutdown.
            future.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
            raise

    async def search(self, query: str, user_id: str) -> list[str]:
        result = await self._call(
            "search",
            self.memory.search,
            query=query,
            filters={"user_id": user_id, "agent_id": AGENT_ID},
            top_k=settings.MEM0_TOP_K,
            threshold=settings.MEM0_THRESHOLD,
        )
        memories: list[str] = []
        remaining = settings.MEM0_MAX_CONTEXT_CHARS
        for item in result.get("results", []):
            text = item.get("memory")
            if isinstance(text, str) and text.strip() and remaining > 0:
                memories.append(text[:remaining])
                remaining -= len(memories[-1])
        logger.info("Mem0 search returned count=%d", len(memories))
        return memories

    async def remember(self, messages: list[dict[str, str]], user_id: str, thread_id: str) -> None:
        result = await self._call(
            "add",
            self.memory.add,
            messages=messages,
            user_id=user_id,
            agent_id=AGENT_ID,
            metadata={"source_thread_id": thread_id},
        )
        logger.info("Mem0 write returned count=%d", len(result.get("results", [])))


_active_service: Mem0Service | None = None


def get_mem0_service() -> Mem0Service | None:
    if settings.MEM0_ENABLED and _active_service is None:
        raise RuntimeError("Mem0 has not been initialized by the service lifecycle")
    return _active_service if settings.MEM0_ENABLED else None


@asynccontextmanager
async def mem0_lifespan() -> AsyncIterator[None]:
    global _active_service
    if not settings.MEM0_ENABLED:
        yield
        return
    validate_postgres_config()
    settings.require_mem0_configuration()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem0")
    pool = ConnectionPool(
        get_postgres_connection_string(),
        min_size=1,
        max_size=2,
        open=False,
        timeout=settings.MEM0_TIMEOUT_SECONDS,
        kwargs={
            "connect_timeout": int(settings.MEM0_TIMEOUT_SECONDS),
            "options": f"-c statement_timeout={int(settings.MEM0_TIMEOUT_SECONDS * 1000)}",
        },
    )
    memory = None
    try:
        await asyncio.to_thread(pool.open, wait=True, timeout=settings.MEM0_TIMEOUT_SECONDS)
        initialization = asyncio.get_running_loop().run_in_executor(
            executor, build_memory, settings, pool
        )
        try:
            memory = await asyncio.shield(initialization)
        except asyncio.CancelledError:
            memory = await initialization
            raise
        _active_service = Mem0Service(memory, executor)
        logger.info(
            "Mem0 initialized backend=pgvector collection=%s", settings.MEM0_COLLECTION_NAME
        )
        yield
    finally:
        service = _active_service
        _active_service = None
        if service and service.pending:
            await asyncio.gather(*service.pending, return_exceptions=True)
        try:
            if memory is not None:
                await asyncio.to_thread(close_memory, memory)
        finally:
            try:
                await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
            finally:
                await asyncio.to_thread(pool.close)
        logger.info("Mem0 resources closed")
