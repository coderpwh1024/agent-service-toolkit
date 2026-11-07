import logging
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

from app.agents import DEFAULT_AGENT


@pytest.mark.asyncio
async def test_lifespan(monkeypatch, caplog) -> None:
    """The main agent receives the production persistence components during startup."""
    import app.lifecycle as service

    fake_saver_setup = False
    fake_store_setup = False

    class FakeSaver:
        async def setup(self) -> None:
            nonlocal fake_saver_setup
            fake_saver_setup = True

    class FakeStore:
        async def setup(self) -> None:
            nonlocal fake_store_setup
            fake_store_setup = True

    fake_saver = FakeSaver()
    fake_store = FakeStore()

    @asynccontextmanager
    async def fake_initialize_database():
        yield fake_saver

    @asynccontextmanager
    async def fake_initialize_store():
        yield fake_store

    agent = type("Agent", (), {"checkpointer": None, "store": None})()
    monkeypatch.setattr(service, "initialize_database", fake_initialize_database)
    monkeypatch.setattr(service, "initialize_store", fake_initialize_store)
    monkeypatch.setattr(service, "get_agent", lambda agent_key: agent)

    caplog.set_level(logging.INFO, logger=service.logger.name)

    async with service.lifespan(FastAPI()):
        pass

    assert fake_saver_setup
    assert fake_store_setup
    assert agent.checkpointer is fake_saver
    assert agent.store is fake_store
    assert f"Agent initialized: {DEFAULT_AGENT}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_component", ["initialize_database", "initialize_store"])
async def test_lifespan_fails_when_postgres_is_unavailable(monkeypatch, failed_component):
    """Never start serving with transient memory when either PG component fails."""
    import app.lifecycle as service

    @asynccontextmanager
    async def available():
        yield object()

    @asynccontextmanager
    async def unavailable():
        raise ConnectionError("PostgreSQL is unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "initialize_database", available)
    monkeypatch.setattr(service, "initialize_store", available)
    monkeypatch.setattr(service, failed_component, unavailable)

    with pytest.raises(ConnectionError, match="PostgreSQL is unavailable"):
        async with service.lifespan(FastAPI()):
            pytest.fail("Service must not start without PostgreSQL persistence")
