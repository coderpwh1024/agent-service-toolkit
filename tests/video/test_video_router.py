from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from core import settings
from schema.video import VideoListQuery, VideoTask, VideoTaskPage
from service.access import create_access_token
from service.video import router
from video.service import VideoBusyError, VideoConflictError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("trusted"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 32))
    app = FastAPI()
    app.include_router(router)
    app.state.video_service = SimpleNamespace(
        create=AsyncMock(), query=AsyncMock(), list_tasks=AsyncMock()
    )
    with TestClient(app) as client:
        yield client, app.state.video_service


def headers(user_id="12"):
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": "video-test"}


@pytest.mark.parametrize(
    "authorization, status", [(None, 401), ("Bearer trusted", 403), ("Bearer invalid", 401)]
)
def test_requires_valid_user_token(client, authorization, status):
    test_client, service = client
    response = test_client.post(
        "/videos",
        json={"prompt": "horse"},
        headers={
            "Idempotency-Key": "video-test",
            **({"Authorization": authorization} if authorization else {}),
        },
    )
    assert response.status_code == status
    service.create.assert_not_called()


@pytest.mark.parametrize("user_id", ["abc", "0", "-1", "9223372036854775808", "１２"])
def test_rejects_non_bigint_user_ids(client, user_id):
    test_client, service = client
    assert test_client.get("/videos/1", headers=headers(user_id)).status_code == 401
    service.query.assert_not_called()


def test_foreign_record_returns_not_found_without_provider_calls(client):
    test_client, service = client
    service.query.side_effect = LookupError()
    assert test_client.get("/videos/1", headers=headers()).status_code == 404
    service.query.assert_awaited_once_with(1, 12)


@pytest.mark.parametrize("key", [None, "", "has space", "x" * 129])
def test_requires_valid_idempotency_header(client, key):
    test_client, service = client
    request_headers = headers()
    request_headers.pop("Idempotency-Key")
    if key is not None:
        request_headers["Idempotency-Key"] = key
    response = test_client.post("/videos", json={"prompt": "horse"}, headers=request_headers)
    assert response.status_code == 422
    service.create.assert_not_called()


def test_conflict_and_disabled_feature(client):
    test_client, service = client
    service.create.side_effect = VideoConflictError("幂等键冲突")
    response = test_client.post("/videos", json={"prompt": "horse"}, headers=headers())
    assert response.status_code == 409
    service.query.side_effect = VideoBusyError("同步中")
    assert test_client.get("/videos/1", headers=headers()).status_code == 409
    test_client.app.state.video_service = None
    assert test_client.get("/videos/1", headers=headers()).status_code == 503


def test_video_routes_are_registered_in_service():
    from service import app

    schema = app.openapi()
    assert schema["paths"]["/videos"]["post"]["operationId"] == "create_video"
    assert schema["paths"]["/videos"]["get"]["operationId"] == "list_videos"
    assert schema["paths"]["/videos/{record_id}"]["get"]["operationId"] == "query_video"


def test_list_videos_returns_page_and_binds_current_user(client):
    test_client, service = client
    task = VideoTask(
        id=123,
        user_id=12,
        task_id="task-123",
        status="SUCCEEDED",
        storage_status="STORED",
        aliyun_video_url=None,
        qiniu_video_url="https://cdn.example/video.mp4",
        qiniu_object_key="videos/happyhorse/task-123.mp4",
        error_code=None,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
    )
    service.list_tasks.return_value = VideoTaskPage(
        items=[task],
        total=3,
        page=2,
        page_size=2,
        total_pages=2,
    )
    response = test_client.get(
        "/videos",
        headers=headers(),
        params={
            "page": 2,
            "page_size": 2,
            "status": "SUCCEEDED",
            "storage_status": "STORED",
        },
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["total"] == 3 and result["total_pages"] == 2
    assert result["page"] == 2 and result["page_size"] == 2
    assert result["items"][0]["id"] == 123
    assert result["items"][0]["qiniu_video_url"] == "https://cdn.example/video.mp4"
    assert "request_payload" not in result["items"][0]
    service.list_tasks.assert_awaited_once_with(
        12,
        VideoListQuery(page=2, page_size=2, status="SUCCEEDED", storage_status="STORED"),
    )


def test_list_videos_defaults_and_empty_result(client):
    test_client, service = client
    service.list_tasks.return_value = VideoTaskPage(
        items=[],
        total=0,
        page=1,
        page_size=20,
        total_pages=0,
    )
    response = test_client.get("/videos", headers=headers())
    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "total_pages": 0,
    }
    service.list_tasks.assert_awaited_once_with(12, VideoListQuery())


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page": "abc"},
        {"page": 2147483648},
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": 1.5},
        {"status": "INVALID"},
        {"storage_status": "INVALID"},
        {"user_id": 99},
        {"sort": "update_time"},
    ],
)
def test_list_videos_rejects_invalid_query(client, params):
    test_client, service = client
    assert test_client.get("/videos", headers=headers(), params=params).status_code == 422
    service.list_tasks.assert_not_awaited()


@pytest.mark.parametrize(
    "authorization, status",
    [
        (None, 401),
        ("Bearer trusted", 403),
        ("Bearer invalid", 401),
    ],
)
def test_list_videos_requires_user_token(client, authorization, status):
    test_client, service = client
    response = test_client.get(
        "/videos",
        headers={"Authorization": authorization} if authorization else {},
    )
    assert response.status_code == status
    service.list_tasks.assert_not_awaited()


def test_list_videos_when_disabled(client):
    test_client, service = client
    test_client.app.state.video_service = None
    assert test_client.get("/videos", headers=headers()).status_code == 503
    service.list_tasks.assert_not_awaited()
