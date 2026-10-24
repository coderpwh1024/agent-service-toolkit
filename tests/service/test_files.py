import asyncio
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psycopg
import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.datastructures import Headers

from core import settings
from core.settings import Settings
from schema.files import UploadedFile
from service import file_uploads, files
from service.access import create_access_token
from service.file_uploads import (
    FileRepository,
    FileService,
    FileTooLargeError,
    FileUploadError,
    QiniuFileStorage,
)
from service.service import app


def metadata() -> UploadedFile:
    return UploadedFile(
        id=1,
        url="https://cdn.example.com/users/7/files/sample",
        original_name="测试.txt",
        content_type="text/plain",
        size_bytes=4,
        uploaded_at=datetime.now(UTC),
    )


def upload(data: bytes = b"test", name: str = "测试.txt", mime: str = "text/plain") -> UploadFile:
    return UploadFile(BytesIO(data), filename=name, headers=Headers({"content-type": mime}))


def service() -> FileService:
    repo = AsyncMock(spec=FileRepository)
    repo.create.return_value = metadata()
    storage = SimpleNamespace(
        bucket_name="test-bucket",
        upload=AsyncMock(return_value="https://cdn.example.com/object"),
        delete=AsyncMock(),
    )
    return FileService(repo, storage, max_bytes=4)


@pytest.mark.asyncio
async def test_upload_saves_confirmed_file_with_current_user_and_safe_name() -> None:
    svc = service()
    result = await svc.upload(7, upload(name="C:\\fakepath\\测试.txt"))
    assert result.id == 1
    key, data, mime = svc.storage.upload.await_args.args
    assert key.startswith("users/7/files/") and "测试" not in key
    assert (data, mime) == (b"test", "text/plain")
    svc.repo.create.assert_awaited_once_with(
        7, "https://cdn.example.com/object", "test-bucket", key, "测试.txt", "text/plain", 4
    )
    svc.storage.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,name,mime,error",
    [
        (b"", "empty.txt", "text/plain", ValueError),
        (b"12345", "large.txt", "text/plain", FileTooLargeError),
        (b"x", " ", "text/plain", ValueError),
        (b"x", "a" * 256, "text/plain", ValueError),
        (b"x", "bad\x00.txt", "text/plain", ValueError),
        (b"x", "file.txt", "bad\r\nheader", ValueError),
    ],
)
async def test_invalid_files_are_rejected_before_external_io(data, name, mime, error) -> None:
    svc = service()
    with pytest.raises(error):
        await svc.upload(7, upload(data, name, mime))
    svc.storage.upload.assert_not_awaited()
    svc.repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_size_metadata_does_not_bypass_actual_size_check() -> None:
    svc = service()
    file = upload(b"12345")
    file.size = 1
    with pytest.raises(FileTooLargeError):
        await svc.upload(7, file)
    svc.storage.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["upload", "database"])
async def test_upload_failure_never_returns_success_and_cleans_its_object(failure) -> None:
    svc = service()
    if failure == "upload":
        svc.storage.upload.side_effect = FileUploadError("provider failure")
        error = FileUploadError
    else:
        svc.repo.create.side_effect = psycopg.errors.CheckViolation("invalid metadata")
        error = psycopg.errors.CheckViolation
    with pytest.raises(error):
        await svc.upload(7, upload())
    key = svc.storage.upload.await_args.args[0]
    svc.storage.delete.assert_awaited_once_with(key)
    if failure == "upload":
        svc.repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_database_commit_keeps_uploaded_object() -> None:
    svc = service()
    svc.repo.create.side_effect = psycopg.OperationalError("connection lost during commit")
    with pytest.raises(psycopg.OperationalError):
        await svc.upload(7, upload())
    svc.storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_waits_for_upload_and_metadata_to_finish() -> None:
    svc = service()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_upload(*_args):
        started.set()
        await release.wait()
        return "https://cdn.example.com/object"

    svc.storage.upload.side_effect = slow_upload
    task = asyncio.create_task(svc.upload(7, upload()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    svc.repo.create.assert_awaited_once()
    svc.storage.delete.assert_not_awaited()


def storage_config(**overrides) -> Settings:
    values = {
        "USE_FAKE_MODEL": True,
        "QINIU_ACCESS_KEY": SecretStr("test-access"),
        "QINIU_SECRET_KEY": SecretStr("test-secret"),
        "QINIU_BUCKET_NAME": "test-bucket",
        "QINIU_PUBLIC_BASE_URL": "https://cdn.example.com/base/",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_qiniu_upload_scopes_token_and_confirms_provider_response(monkeypatch) -> None:
    storage = QiniuFileStorage(storage_config())
    storage.auth = Mock()
    storage.auth.upload_token.return_value = "test-upload-token"
    put = Mock(
        return_value=(
            {"key": "users/7/files/abc", "hash": "etag"},
            SimpleNamespace(status_code=200),
        )
    )
    monkeypatch.setattr(file_uploads, "put_data", put)
    url = await storage.upload("users/7/files/abc", b"test", "text/plain")
    assert url == "https://cdn.example.com/base/users/7/files/abc"
    storage.auth.upload_token.assert_called_once_with(
        "test-bucket",
        "users/7/files/abc",
        3600,
        policy={"insertOnly": 1, "fsizeLimit": 20 * 1024 * 1024},
    )
    put.assert_called_once_with(
        "test-upload-token", "users/7/files/abc", b"test", mime_type="text/plain", check_crc=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,status", [(None, 500), ({"key": "wrong", "hash": "etag"}, 200), ({"key": "abc"}, 200)]
)
async def test_qiniu_rejects_unconfirmed_uploads(monkeypatch, result, status) -> None:
    monkeypatch.setattr(
        file_uploads, "put_data", Mock(return_value=(result, SimpleNamespace(status_code=status)))
    )
    with pytest.raises(FileUploadError):
        await QiniuFileStorage(storage_config()).upload("abc", b"test", "text/plain")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://cdn.example.com",
        "https://cdn.example.com?token=secret",
        "https://user:secret@cdn.example.com",
    ],
)
def test_qiniu_rejects_invalid_public_domains(url) -> None:
    with pytest.raises(ValueError):
        QiniuFileStorage(storage_config(QINIU_PUBLIC_BASE_URL=url))


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("test-files-secret-" * 3))
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("test-admin"))
    svc = service()
    svc.repo.get.return_value = metadata()
    monkeypatch.setattr(app.state, "file_service", svc, raising=False)
    return TestClient(app), svc


def auth(user_id: str = "7") -> dict[str, str]:
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def test_api_upload_and_query_use_authenticated_owner(api) -> None:
    client, svc = api
    response = client.post(
        "/files", files={"file": ("测试.txt", b"test", "text/plain")}, headers=auth()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["id"] == 1
    assert body["data"]["url"].startswith("https://cdn.example.com/")
    assert "object_key" not in body["data"]
    assert client.get("/files/1/url", headers=auth()).status_code == 200
    svc.repo.get.assert_awaited_once_with(1, 7)
    svc.repo.get.return_value = None
    assert client.get("/files/1/url", headers=auth("8")).status_code == 404
    svc.repo.get.assert_awaited_with(1, 8)


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, 401),
        ({"Authorization": "Bearer invalid"}, 401),
        ({"Authorization": "Bearer test-admin"}, 403),
    ],
)
def test_api_rejects_missing_invalid_and_admin_tokens(api, headers, expected) -> None:
    client, svc = api
    assert (
        client.post("/files", files={"file": ("a.txt", b"test")}, headers=headers).status_code
        == expected
    )
    assert client.get("/files/1/url", headers=headers).status_code == expected
    svc.storage.upload.assert_not_awaited()
    svc.repo.get.assert_not_awaited()


@pytest.mark.parametrize("user_id", ["abc", "-1", "0", "9223372036854775808", "１２"])
def test_api_rejects_invalid_numeric_user_identity(api, user_id) -> None:
    client, _ = api
    assert client.get("/files/1/url", headers=auth(user_id)).status_code == 401


@pytest.mark.parametrize("file_id", ["0", "-1", "abc", "9223372036854775808"])
def test_api_validates_file_id(api, file_id) -> None:
    client, _ = api
    assert client.get(f"/files/{file_id}/url", headers=auth()).status_code == 422


def test_api_maps_provider_database_and_validation_errors(api) -> None:
    client, svc = api
    assert (
        client.post("/files", files={"file": ("a.txt", b"12345")}, headers=auth()).status_code
        == 413
    )
    assert client.post("/files", files={"file": ("a.txt", b"")}, headers=auth()).status_code == 422
    assert client.post("/files", headers=auth()).status_code == 422
    svc.storage.upload.side_effect = FileUploadError("secret provider details")
    result = client.post("/files", files={"file": ("a.txt", b"test")}, headers=auth())
    assert result.status_code == 502 and "secret" not in result.text
    svc.repo.get.side_effect = psycopg.OperationalError("secret connection string")
    result = client.get("/files/1/url", headers=auth())
    assert result.status_code == 503 and "secret" not in result.text


def test_disabled_feature_returns_503(api, monkeypatch) -> None:
    client, _ = api
    monkeypatch.setattr(app.state, "file_service", None)
    assert client.get("/files/1/url", headers=auth()).status_code == 503


@pytest.mark.asyncio
async def test_file_lifespan_opens_and_closes_repository(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FILE_UPLOAD_ENABLED", True)
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("test-file-secret" * 3))
    repo = AsyncMock(spec=FileRepository)
    monkeypatch.setattr(files, "FileRepository", lambda _conninfo: repo)
    monkeypatch.setattr(files, "get_postgres_connection_string", lambda: "test-connection")
    monkeypatch.setattr(files, "QiniuFileStorage", lambda _config: Mock())
    local_app = FastAPI()
    async with files.file_lifespan(local_app):
        assert local_app.state.file_service is not None
        repo.open.assert_awaited_once()
    assert local_app.state.file_service is None
    repo.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_file_lifespan_fails_and_closes_pool_when_database_setup_fails(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FILE_UPLOAD_ENABLED", True)
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("test-file-secret" * 3))
    repo = AsyncMock(spec=FileRepository)
    repo.open.side_effect = psycopg.OperationalError("unavailable")
    monkeypatch.setattr(files, "FileRepository", lambda _conninfo: repo)
    monkeypatch.setattr(files, "get_postgres_connection_string", lambda: "test-connection")
    monkeypatch.setattr(files, "QiniuFileStorage", lambda _config: Mock())
    with pytest.raises(psycopg.OperationalError):
        async with files.file_lifespan(FastAPI()):
            pytest.fail("Must not start with unavailable PostgreSQL")
    repo.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, SecretStr("too-short")])
async def test_enabled_lifespan_requires_app_token_secret(monkeypatch, secret) -> None:
    monkeypatch.setattr(settings, "FILE_UPLOAD_ENABLED", True)
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", secret)
    with pytest.raises(ValueError, match="APP_TOKEN_SECRET"):
        async with files.file_lifespan(FastAPI()):
            pytest.fail("Must not enable uploads without app authentication")


@pytest.mark.asyncio
async def test_disabled_lifespan_does_not_open_connections(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FILE_UPLOAD_ENABLED", False)
    factory = Mock()
    monkeypatch.setattr(files, "FileRepository", factory)
    async with files.file_lifespan(FastAPI()):
        pass
    factory.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 100 * 1024 * 1024 + 1])
def test_file_upload_size_setting_rejects_invalid_limits(limit) -> None:
    with pytest.raises(ValidationError, match="FILE_UPLOAD_MAX_BYTES"):
        Settings(_env_file=None, FILE_UPLOAD_MAX_BYTES=limit)
