from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from core import settings
from memory.mem0_service import MemoryBusyError, MemoryNotFoundError
from schema.memory import MemoryDeleteResult, MemoryHistory, MemoryList, MemoryRecord
from service import memories
from service.access import create_access_token

MEMORY_ID = str(uuid4())


@pytest.fixture
def memory_api(monkeypatch, test_client):
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("service-secret"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("app-secret"))
    record = MemoryRecord(id=MEMORY_ID, memory="喜欢茶", user_id="alice", agent_id="mem0-chatbot")
    service = AsyncMock()
    service.list_memories.return_value = MemoryList(memories=[record])
    service.get_memory.return_value = record
    service.update_memory.return_value = record.model_copy(update={"memory": "喜欢咖啡"})
    service.delete_memory.return_value = MemoryDeleteResult(deleted_count=1)
    service.delete_all_memories.return_value = MemoryDeleteResult(deleted_count=3)
    service.memory_history.return_value = MemoryHistory(history=[])
    monkeypatch.setattr(memories, "get_mem0_service", lambda: service)
    token, _ = create_access_token("alice")
    return test_client, service, {"Authorization": f"Bearer {token}"}


OPERATIONS = [
    ("GET", "/memories", "list_memories", ("alice", 50, None)),
    ("GET", f"/memories/{MEMORY_ID}", "get_memory", (MEMORY_ID, "alice")),
    ("PUT", f"/memories/{MEMORY_ID}", "update_memory", (MEMORY_ID, "alice", "喜欢咖啡")),
    ("DELETE", f"/memories/{MEMORY_ID}", "delete_memory", (MEMORY_ID, "alice")),
    ("DELETE", "/memories", "delete_all_memories", ("alice",)),
    ("GET", f"/memories/{MEMORY_ID}/history", "memory_history", (MEMORY_ID, "alice", 50, None)),
]


@pytest.mark.parametrize("method,path,operation,args", OPERATIONS)
def test_authenticated_management(memory_api, method, path, operation, args):
    client, service, headers = memory_api
    kwargs = {"json": {"text": " 喜欢咖啡 "}} if method == "PUT" else {}
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"] == getattr(service, operation).return_value.model_dump(mode="json")
    getattr(service, operation).assert_awaited_once_with(*args)


@pytest.mark.parametrize("method,path,operation,args", OPERATIONS)
def test_cannot_impersonate_other_user(memory_api, method, path, operation, args):
    client, service, headers = memory_api
    kwargs = {"json": {"text": "coffee"}} if method == "PUT" else {}
    response = client.request(method, path, headers=headers, params={"user_id": "bob"}, **kwargs)
    assert response.status_code == 403
    getattr(service, operation).assert_not_awaited()


@pytest.mark.parametrize("method,path,operation,args", OPERATIONS)
def test_bearer_auth_is_required(memory_api, method, path, operation, args):
    client, service, _ = memory_api
    kwargs = {"json": {"text": "coffee"}} if method == "PUT" else {}
    response = client.request(method, path, params={"user_id": "alice"}, **kwargs)
    assert response.status_code == 401
    getattr(service, operation).assert_not_awaited()


@pytest.mark.parametrize("method,path,operation,args", OPERATIONS)
def test_service_credentials_require_explicit_user(memory_api, method, path, operation, args):
    client, service, _ = memory_api
    headers = {"Authorization": "Bearer service-secret"}
    kwargs = {"json": {"text": "喜欢咖啡"}} if method == "PUT" else {}
    assert client.request(method, path, headers=headers, **kwargs).status_code == 422
    getattr(service, operation).assert_not_awaited()
    response = client.request(method, path, headers=headers, params={"user_id": "alice"}, **kwargs)
    assert response.status_code == 200
    getattr(service, operation).assert_awaited_once_with(*args)


@pytest.mark.parametrize("text", ["", "   ", "x" * 6001])
def test_update_rejects_invalid_text(memory_api, text):
    client, service, headers = memory_api
    response = client.put(f"/memories/{MEMORY_ID}", headers=headers, json={"text": text})
    assert response.status_code == 422
    service.update_memory.assert_not_awaited()


def test_update_rejects_metadata_and_owner_changes(memory_api):
    client, service, headers = memory_api
    response = client.put(
        f"/memories/{MEMORY_ID}",
        headers=headers,
        json={"text": "coffee", "user_id": "bob", "metadata": {"agent_id": "other"}},
    )
    assert response.status_code == 422
    service.update_memory.assert_not_awaited()


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"cursor": "invalid"}])
def test_list_validation(memory_api, query):
    client, service, headers = memory_api
    assert client.get("/memories", headers=headers, params=query).status_code == 422
    service.list_memories.assert_not_awaited()


def test_pagination_and_invalid_id(memory_api):
    client, service, headers = memory_api
    assert client.get("/memories/not-a-uuid", headers=headers).status_code == 422
    assert (
        client.get(
            f"/memories/{MEMORY_ID}/history", headers=headers, params={"cursor": 0}
        ).status_code
        == 422
    )
    response = client.get(
        f"/memories/{MEMORY_ID}/history", headers=headers, params={"limit": 2, "cursor": 7}
    )
    assert response.status_code == 200
    service.memory_history.assert_awaited_once_with(MEMORY_ID, "alice", 2, 7)


@pytest.mark.parametrize(
    "error,status",
    [
        (MemoryNotFoundError(), 404),
        (MemoryBusyError(), 503),
        (TimeoutError(), 504),
        (ConnectionError("private-text-and-key"), 503),
    ],
)
def test_errors_do_not_leak_provider_details(memory_api, caplog, error, status):
    client, service, headers = memory_api
    service.get_memory.side_effect = error
    response = client.get(f"/memories/{MEMORY_ID}", headers=headers)
    assert response.status_code == status
    assert response.json()["code"] == status
    assert "private-text-and-key" not in response.text + caplog.text


def test_disabled_memory_is_unavailable(memory_api, monkeypatch):
    client, service, headers = memory_api
    monkeypatch.setattr(memories, "get_mem0_service", lambda: None)
    assert client.delete("/memories", headers=headers).status_code == 503
    service.delete_all_memories.assert_not_awaited()


def test_openapi_has_unique_operation_ids(memory_api):
    client, _, _ = memory_api
    paths = client.get("/openapi.json").json()["paths"]
    ids = [
        operation["operationId"]
        for path, methods in paths.items()
        if path.startswith("/memories")
        for operation in methods.values()
    ]
    assert len(ids) == len(set(ids)) == 6
