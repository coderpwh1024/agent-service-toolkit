import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from core import settings
from service import video as video_routes
from video.runtime import get_video_service


@pytest.mark.asyncio
async def test_disabled_video_does_not_open_database_or_start_worker(monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_ENABLED", False)
    repo = AsyncMock()
    monkeypatch.setattr(video_routes, "VideoRepository", repo)
    app = FastAPI()
    async with video_routes.video_lifespan(app):
        assert app.state.video_service is None
        assert get_video_service() is None
    repo.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_video_cancels_worker_and_closes_database(monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    monkeypatch.setattr(type(settings), "require_video_configuration", lambda self: None)
    monkeypatch.setattr(video_routes, "get_postgres_connection_string", lambda: "unused")
    repo = AsyncMock()
    monkeypatch.setattr(video_routes, "VideoRepository", lambda _: repo)
    monkeypatch.setattr(video_routes, "HappyHorseClient", lambda *_: object())
    monkeypatch.setattr(video_routes, "QiniuVideoStorage", lambda *_: object())
    started = asyncio.Event()
    stopped = asyncio.Event()
    service = AsyncMock()

    async def run():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    service.run.side_effect = run
    monkeypatch.setattr(video_routes, "VideoService", lambda *_: service)
    app = FastAPI()
    async with video_routes.video_lifespan(app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert app.state.video_service is service
        assert get_video_service() is service
    assert stopped.is_set()
    assert app.state.video_service is None
    assert get_video_service() is None
    repo.open.assert_awaited_once()
    repo.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_failure_does_not_leave_video_enabled(monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_ENABLED", True)
    monkeypatch.setattr(type(settings), "require_video_configuration", lambda self: None)
    monkeypatch.setattr(video_routes, "get_postgres_connection_string", lambda: "unused")
    repo = AsyncMock()
    repo.open.side_effect = ConnectionError("数据库不可用")
    monkeypatch.setattr(video_routes, "VideoRepository", lambda _: repo)
    app = FastAPI()
    with pytest.raises(ConnectionError):
        async with video_routes.video_lifespan(app):
            pytest.fail("不应在数据库失败后启动视频服务")
    assert app.state.video_service is None
    assert get_video_service() is None
    repo.close.assert_awaited_once()
