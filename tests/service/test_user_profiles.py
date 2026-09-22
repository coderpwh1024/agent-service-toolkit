from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.datastructures import Headers

from schema.auth import UserProfile
from service import user_profiles
from service.access import Principal
from service.email_auth import UserRepository
from service.user_profiles import (
    AvatarTooLargeError,
    QiniuAvatarStorage,
    UserProfileService,
    router,
)

PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"avatar-data"


class FakeUsers:
    def __init__(self):
        self.updated: tuple[int, str | None, str | None] | None = None
        self.error: Exception | None = None

    async def update_profile(
        self,
        user_id: int,
        nickname: str | None,
        image_url: str | None,
    ) -> UserProfile | None:
        if self.error:
            raise self.error
        self.updated = (user_id, nickname, image_url)
        return UserProfile(
            id=user_id,
            nickname=nickname or "existing",
            email="member@example.com",
            image_url=image_url,
        )


class FakeAvatars:
    def __init__(self):
        self.uploads: list[tuple[int, bytes, str, str]] = []
        self.deleted: list[str] = []

    async def upload(
        self,
        user_id: int,
        data: bytes,
        mime_type: str,
        extension: str,
    ) -> tuple[str, str]:
        self.uploads.append((user_id, data, mime_type, extension))
        object_key = f"users/{user_id}/avatars/avatar.{extension}"
        return object_key, f"https://cdn.example.com/{object_key}"

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)


@pytest.mark.asyncio
async def test_user_repository_update_does_not_modify_email() -> None:
    row = {
        "id": 7,
        "nickname": "新昵称",
        "email": "member@example.com",
        "image_url": "https://cdn.example.com/avatar.png",
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        def __init__(self):
            self.query = ""
            self.params = ()

        async def execute(self, query, params):
            self.query = query
            self.params = params
            return Cursor()

    class ConnectionContext:
        def __init__(self, connection):
            self.connection_value = connection

        async def __aenter__(self):
            return self.connection_value

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def __init__(self):
            self.connection_value = Connection()

        def connection(self):
            return ConnectionContext(self.connection_value)

    repository = object.__new__(UserRepository)
    repository.pool = Pool()

    user = await repository.update_profile(
        7,
        "新昵称",
        "https://cdn.example.com/avatar.png",
    )

    query = repository.pool.connection_value.query
    assert "UPDATE app_users" in query
    assert "update_by = %s" in query
    assert "update_time = CURRENT_TIMESTAMP" in query
    assert "email =" not in query
    assert repository.pool.connection_value.params == (
        "新昵称",
        "https://cdn.example.com/avatar.png",
        7,
        7,
    )
    assert user == UserProfile.model_validate(row)


def upload_file(data: bytes = PNG_DATA, content_type: str = "image/png") -> UploadFile:
    return UploadFile(
        BytesIO(data),
        filename="avatar.png",
        headers=Headers({"content-type": content_type}),
    )


@pytest.mark.asyncio
async def test_profile_update_uploads_avatar_and_preserves_email() -> None:
    users = FakeUsers()
    avatars = FakeAvatars()
    service = UserProfileService(users, avatars, max_avatar_bytes=1024)

    user = await service.update(7, "  新昵称  ", upload_file())

    expected_url = "https://cdn.example.com/users/7/avatars/avatar.png"
    assert users.updated == (7, "新昵称", expected_url)
    assert user.email == "member@example.com"
    assert str(user.image_url) == expected_url
    assert avatars.uploads == [(7, PNG_DATA, "image/png", "png")]


@pytest.mark.asyncio
async def test_profile_update_cleans_up_new_object_when_database_update_fails() -> None:
    users = FakeUsers()
    users.error = RuntimeError("database unavailable")
    avatars = FakeAvatars()
    service = UserProfileService(users, avatars, max_avatar_bytes=1024)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.update(7, None, upload_file())

    assert avatars.deleted == ["users/7/avatars/avatar.png"]


@pytest.mark.asyncio
async def test_profile_update_rejects_oversized_avatar_before_upload() -> None:
    avatars = FakeAvatars()
    service = UserProfileService(FakeUsers(), avatars, max_avatar_bytes=8)

    with pytest.raises(AvatarTooLargeError):
        await service.update(7, None, upload_file(PNG_DATA + b"too-large"))

    assert avatars.uploads == []


@pytest.mark.asyncio
async def test_qiniu_storage_returns_complete_public_url(monkeypatch) -> None:
    uploaded: dict[str, object] = {}

    def fake_put_data(token, key, data, **kwargs):
        uploaded.update(token=token, key=key, data=data, kwargs=kwargs)
        return {"key": key, "hash": "etag"}, SimpleNamespace(status_code=200)

    monkeypatch.setattr(user_profiles, "put_data", fake_put_data)
    config = SimpleNamespace(
        QINIU_ACCESS_KEY=SecretStr("access-key"),
        QINIU_SECRET_KEY=SecretStr("secret-key"),
        QINIU_BUCKET_NAME="agent-service-toolkit-avatars",
        QINIU_PUBLIC_BASE_URL="https://cdn.example.com/base/",
        QINIU_UPLOAD_TOKEN_TTL_SECONDS=3600,
    )
    storage = QiniuAvatarStorage(config)

    object_key, url = await storage.upload(9, PNG_DATA, "image/png", "png")

    assert object_key.startswith("users/9/avatars/")
    assert object_key.endswith(".png")
    assert url == f"https://cdn.example.com/base/{object_key}"
    assert uploaded["data"] == PNG_DATA
    assert uploaded["kwargs"] == {"mime_type": "image/png", "check_crc": True}


def test_profile_route_updates_current_app_user_and_rejects_email(monkeypatch) -> None:
    profile = UserProfile(
        id=7,
        nickname="新昵称",
        email="member@example.com",
        image_url=None,
    )
    service = SimpleNamespace(update=AsyncMock(return_value=profile))
    app = FastAPI()
    app.state.user_profile_service = service
    app.include_router(router)
    monkeypatch.setattr(
        user_profiles,
        "principal",
        lambda _request: Principal(user_id="7"),
    )

    with TestClient(app) as client:
        updated = client.patch("/users/me", data={"nickname": "新昵称"})
        rejected = client.patch(
            "/users/me",
            data={"nickname": "新昵称", "email": "other@example.com"},
        )

    assert updated.status_code == 200
    assert updated.json()["data"]["email"] == "member@example.com"
    service.update.assert_awaited_once()
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "Email cannot be changed"


def test_profile_route_rejects_admin_identity(monkeypatch) -> None:
    app = FastAPI()
    app.state.user_profile_service = SimpleNamespace(update=AsyncMock())
    app.include_router(router)
    monkeypatch.setattr(user_profiles, "principal", lambda _request: Principal(admin=True))

    with TestClient(app) as client:
        response = client.patch("/users/me", data={"nickname": "nickname"})

    assert response.status_code == 403
