from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from core import settings
from schema.video import VideoListQuery, VideoMetadataUpdate, VideoTask, VideoTaskPage
from service.access import create_access_token
from service.video import router
from video.service import VideoBusyError, VideoConflictError


def test_wan3_uses_existing_create_detail_and_list_routes(client) -> None:
    test_client, service = client
    task = VideoTask(
        id=24,
        user_id=12,
        model="wan3.0-video-prime",
        first_frame_url="https://images.example/first.jpg",
        last_frame_url="https://images.example/last.jpg",
        task_id="wan-task",
        status="PENDING",
        storage_status="NOT_READY",
        aliyun_video_url=None,
        qiniu_video_url=None,
        qiniu_object_key=None,
        error_code=None,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
    )
    service.create.return_value = task
    service.query.return_value = task
    service.list_tasks.return_value = VideoTaskPage(
        items=[task], total=1, page=1, page_size=20, total_pages=1
    )
    response = test_client.post(
        "/videos",
        headers=headers(),
        json={
            "model": task.model,
            "prompt": "从日出过渡到日落",
            "duration": 30,
            "first_frame_url": task.first_frame_url,
            "last_frame_url": task.last_frame_url,
        },
    )
    assert response.status_code == 202 and response.json()["data"]["model"] == task.model
    submitted = service.create.await_args.args[2]
    assert submitted.duration == 30 and str(submitted.first_frame_url) == task.first_frame_url
    detail = test_client.get("/videos/24", headers=headers())
    assert detail.json()["data"]["last_frame_url"] == task.last_frame_url
    listing = test_client.get("/videos?model=wan3.0-video-prime", headers=headers())
    assert listing.status_code == 200 and listing.json()["data"]["total"] == 1
    service.list_tasks.assert_awaited_once_with(12, VideoListQuery(model=task.model))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "model": "wan3.0-video",
            "prompt": "海边",
            "last_frame_url": "https://images.example/end.jpg",
        },
        {"model": "wan3.0-video", "prompt": "海边", "duration": 1},
        {
            "model": "happyhorse-1.1-t2v",
            "prompt": "海边",
            "first_frame_url": "https://images.example/start.jpg",
        },
    ],
)
def test_invalid_wan3_request_never_submits(client, payload: dict[str, object]) -> None:
    test_client, service = client
    assert test_client.post("/videos", headers=headers(), json=payload).status_code == 422
    service.create.assert_not_awaited()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("trusted"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 32))
    app = FastAPI()
    app.include_router(router)
    app.state.video_service = SimpleNamespace(
        create=AsyncMock(),
        query=AsyncMock(),
        list_tasks=AsyncMock(),
        update_metadata=AsyncMock(),
        delete=AsyncMock(),
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
    assert schema["paths"]["/videos/{record_id}"]["patch"]["operationId"] == "update_video_metadata"
    assert schema["paths"]["/videos/{record_id}"]["delete"]["operationId"] == "delete_video"


def test_update_metadata_and_delete_bind_current_user(client):
    test_client, service = client
    task = VideoTask(
        id=123,
        user_id=12,
        title="新标题",
        cover_url="https://cdn.example/custom.jpg",
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
    service.update_metadata.return_value = task

    updated = test_client.patch(
        "/videos/123",
        headers=headers(),
        json={"title": " 新标题 ", "cover_url": "https://cdn.example/custom.jpg"},
    )
    deleted = test_client.delete("/videos/123", headers=headers())

    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "新标题"
    assert updated.json()["data"]["cover_url"] == "https://cdn.example/custom.jpg"
    service.update_metadata.assert_awaited_once_with(
        123,
        12,
        VideoMetadataUpdate(title="新标题", cover_url="https://cdn.example/custom.jpg"),
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}
    service.delete.assert_awaited_once_with(123, 12)


@pytest.mark.parametrize("path", ["/videos/123", "/videos/0"])
def test_metadata_mutations_reject_invalid_identity_or_id(client, path):
    test_client, service = client
    expected = 422 if path.endswith("/0") else 403
    assert (
        test_client.patch(
            path, headers={"Authorization": "Bearer trusted"}, json={"title": "x"}
        ).status_code
        == expected
    )
    assert (
        test_client.delete(path, headers={"Authorization": "Bearer trusted"}).status_code
        == expected
    )
    service.update_metadata.assert_not_awaited()
    service.delete.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": " "},
        {"title": "字" * 201},
        {"cover_url": "javascript:alert(1)"},
        {"cover_url": None},
        {"title": "标题", "prompt": "越权参数"},
    ],
)
def test_metadata_update_rejects_invalid_body(client, payload):
    test_client, service = client
    assert test_client.patch("/videos/123", headers=headers(), json=payload).status_code == 422
    service.update_metadata.assert_not_awaited()


@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("error,status", [(LookupError(), 404), (VideoBusyError("同步中"), 409)])
def test_metadata_mutations_hide_foreign_records_and_report_busy(client, method, error, status):
    test_client, service = client
    getattr(service, "update_metadata" if method == "patch" else "delete").side_effect = error
    response = getattr(test_client, method)(
        "/videos/123",
        headers=headers(),
        **({"json": {"title": "新标题"}} if method == "patch" else {}),
    )
    assert response.status_code == status


def test_metadata_update_rejects_cover_before_archival(client):
    test_client, service = client
    service.update_metadata.side_effect = VideoConflictError("封面尚未就绪")
    response = test_client.patch(
        "/videos/123", headers=headers(), json={"cover_url": "https://cdn.example/custom.jpg"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "metadata", [{}, {"title": "海边日落", "cover_url": "https://cdn.example/cover.jpg"}]
)
def test_create_and_detail_return_metadata(client, metadata):
    test_client, service = client
    task = VideoTask(
        id=123,
        user_id=12,
        task_id="task-123",
        status="PENDING",
        storage_status="NOT_READY",
        aliyun_video_url=None,
        qiniu_video_url=None,
        qiniu_object_key=None,
        error_code=None,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
        **metadata,
    )
    service.create.return_value = task
    service.query.return_value = task
    created = test_client.post("/videos", json={"prompt": "海边", **metadata}, headers=headers())
    detail = test_client.get("/videos/123", headers=headers())
    assert created.status_code == 202 and detail.status_code == 200
    for response in (created, detail):
        assert response.json()["data"]["title"] == metadata.get("title")
        assert response.json()["data"]["cover_url"] == metadata.get("cover_url")
    body = service.create.await_args.args[2]
    assert body.title == metadata.get("title")
    assert (str(body.cover_url) if body.cover_url else None) == metadata.get("cover_url")


@pytest.mark.parametrize(
    "metadata", [{"title": " "}, {"title": "字" * 201}, {"cover_url": "javascript:alert(1)"}]
)
def test_create_rejects_invalid_metadata_before_submission(client, metadata):
    test_client, service = client
    response = test_client.post("/videos", json={"prompt": "海边", **metadata}, headers=headers())
    assert response.status_code == 422
    service.create.assert_not_awaited()


def test_list_videos_returns_page_and_binds_current_user(client):
    test_client, service = client
    task = VideoTask(
        id=123,
        title="海边日落",
        cover_url="https://cdn.example/cover.jpg",
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
            "q": "  海边\t日落  ",
        },
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["total"] == 3 and result["total_pages"] == 2
    assert result["page"] == 2 and result["page_size"] == 2
    assert result["items"][0]["id"] == 123
    assert result["items"][0]["title"] == "海边日落"
    assert result["items"][0]["cover_url"] == "https://cdn.example/cover.jpg"
    assert result["items"][0]["qiniu_video_url"] == "https://cdn.example/video.mp4"
    assert "request_payload" not in result["items"][0]
    service.list_tasks.assert_awaited_once_with(
        12,
        VideoListQuery(
            page=2, page_size=2, status="SUCCEEDED", storage_status="STORED", q="海边 日落"
        ),
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
        {"q": ""},
        {"q": " \t "},
        {"q": "字" * 201},
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
