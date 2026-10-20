import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, Mock

import pytest

from core import settings
from memory.mem0_history import PostgresHistory
from memory.mem0_service import AGENT_ID, Mem0Service


@pytest.mark.asyncio
async def test_sdk_scope_and_bounded_context(monkeypatch):
    monkeypatch.setattr(settings, "MEM0_MAX_CONTEXT_CHARS", 5)
    memory = Mock()
    memory.db = PostgresHistory(MagicMock(), "mem0_test", 7)
    memory.search.return_value = {"results": [{"memory": "abcdef"}, {"memory": "other"}]}
    memory.add.return_value = {"results": []}
    with ThreadPoolExecutor(max_workers=1) as executor:
        service = Mem0Service(memory, executor)
        assert await service.search("hello", "alice") == ["abcde"]
        memory.search.assert_called_once_with(
            query="hello",
            filters={"user_id": "alice", "agent_id": AGENT_ID},
            top_k=settings.MEM0_TOP_K,
            threshold=settings.MEM0_THRESHOLD,
        )
        await service.remember([{"role": "user", "content": "tea"}], "alice", "t")
        assert memory.add.call_args.kwargs["user_id"] == "alice"
        assert memory.add.call_args.kwargs["metadata"] == {"source_thread_id": "t"}
        assert "run_id" not in memory.add.call_args.kwargs


@pytest.mark.asyncio
async def test_rerank_uses_larger_scoped_candidate_pool_and_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "MEM0_TOP_K", 2)
    monkeypatch.setattr(settings, "MEM0_RERANK_CANDIDATES", 8)
    memory = Mock()
    memory.search.return_value = {
        "results": [{"memory": "tea"}, {"memory": "coffee"}, {"memory": "water"}]
    }
    reranker = Mock()
    reranker.rerank.return_value = [{"memory": "water"}, {"memory": "coffee"}]
    with ThreadPoolExecutor(max_workers=1) as executor:
        service = Mem0Service(memory, executor, reranker=reranker)
        assert await service.search("drink", "alice") == ["water", "coffee"]
        memory.search.assert_called_with(
            query="drink",
            filters={"user_id": "alice", "agent_id": AGENT_ID},
            top_k=8,
            threshold=settings.MEM0_THRESHOLD,
        )
        reranker.rerank.side_effect = ConnectionError("unavailable")
        assert await service.search("drink", "alice") == ["tea", "coffee"]


@pytest.mark.asyncio
async def test_empty_candidates_skip_reranker():
    memory = Mock()
    memory.search.return_value = {"results": []}
    reranker = Mock()
    with ThreadPoolExecutor(max_workers=1) as executor:
        service = Mem0Service(memory, executor, reranker=reranker)
        assert await service.search("drink", "alice") == []
        reranker.rerank.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_retains_worker_capacity():
    started, release = Event(), Event()

    def slow_search(**kwargs):
        started.set()
        release.wait(5)
        return {"results": []}

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = Mem0Service(Mock(search=slow_search), executor, max_pending=1)
        task = asyncio.create_task(service.search("query", "alice"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.pending
            with pytest.raises(RuntimeError, match="busy"):
                await service.search("other", "bob")
        finally:
            release.set()
            await asyncio.gather(*service.pending)
        assert not service.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "open", "build", "close"])
async def test_lifecycle_initialization_and_cleanup(monkeypatch, failure):
    from pydantic import SecretStr

    from memory import mem0_service

    monkeypatch.setattr(settings, "MEM0_ENABLED", True)
    monkeypatch.setattr(settings, "MEM0_API_KEY", SecretStr("test"))
    pool = Mock()
    memory = Mock()
    builder = Mock(return_value=memory)
    closer = Mock()
    if failure == "open":
        pool.open.side_effect = ConnectionError("unavailable")
    elif failure == "build":
        builder.side_effect = ValueError("extension unavailable")
    elif failure == "close":
        closer.side_effect = RuntimeError("close failed")
    monkeypatch.setattr(mem0_service, "ConnectionPool", lambda *args, **kwargs: pool)
    monkeypatch.setattr(mem0_service, "build_memory", builder)
    monkeypatch.setattr(mem0_service, "close_memory", closer)
    monkeypatch.setattr(mem0_service, "get_postgres_connection_string", lambda: "dbname=test")
    monkeypatch.setattr(mem0_service, "validate_postgres_config", lambda: None)

    async def run():
        async with mem0_service.mem0_lifespan():
            assert mem0_service.get_mem0_service().memory is memory

    if failure:
        with pytest.raises((ConnectionError, ValueError, RuntimeError)):
            await run()
    else:
        await run()
    pool.close.assert_called_once()
    assert mem0_service._active_service is None
    if failure in {None, "close"}:
        closer.assert_called_once_with(memory)


@pytest.mark.asyncio
async def test_lifecycle_closes_reranker_on_build_failure(monkeypatch):
    from pydantic import SecretStr

    from memory import mem0_service

    monkeypatch.setattr(settings, "MEM0_ENABLED", True)
    monkeypatch.setattr(settings, "MEM0_API_KEY", SecretStr("test"))
    monkeypatch.setattr(settings, "MEM0_RERANK_MODEL", "qwen3.7-text-rerank")
    monkeypatch.setattr(settings, "MEM0_RERANK_URL", "https://example.test/rerank")
    pool = Mock()
    reranker = Mock()
    builder = Mock(side_effect=ValueError("build failed"))
    monkeypatch.setattr(mem0_service, "ConnectionPool", lambda *args, **kwargs: pool)
    monkeypatch.setattr(mem0_service, "Mem0Reranker", Mock(return_value=reranker))
    monkeypatch.setattr(mem0_service, "build_memory", builder)
    monkeypatch.setattr(mem0_service, "get_postgres_connection_string", lambda: "dbname=test")
    monkeypatch.setattr(mem0_service, "validate_postgres_config", lambda: None)
    with pytest.raises(ValueError, match="build failed"):
        async with mem0_service.mem0_lifespan():
            pass
    reranker.close.assert_called_once()
    pool.close.assert_called_once()


def test_enabled_memory_requires_key(monkeypatch):
    from core.settings import Settings

    config = Settings(
        _env_file=None,
        USE_FAKE_MODEL=True,
        MEM0_ENABLED=True,
        MEM0_API_KEY=None,
        DASHSCOPE_API_KEY=None,
    )
    with pytest.raises(ValueError, match="MEM0_API_KEY"):
        config.require_mem0_configuration()
