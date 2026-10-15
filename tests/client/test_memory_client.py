from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from client import AgentClient, AgentClientError
from schema.memory import MemoryDeleteResult, MemoryHistory, MemoryList, MemoryRecord

MEMORY_ID = "00000000-0000-0000-0000-000000000001"
RECORD = {"id": MEMORY_ID, "memory": "coffee", "user_id": "alice", "agent_id": "mem0-chatbot"}
OPERATIONS = [
    (
        "get_memories",
        (),
        {"limit": 2, "cursor": UUID(MEMORY_ID)},
        "GET",
        "",
        {"memories": [RECORD], "next_cursor": MEMORY_ID},
        MemoryList,
    ),
    ("get_memory", (MEMORY_ID,), {}, "GET", f"/{MEMORY_ID}", RECORD, MemoryRecord),
    ("update_memory", (MEMORY_ID, " coffee "), {}, "PUT", f"/{MEMORY_ID}", RECORD, MemoryRecord),
    (
        "delete_memory",
        (MEMORY_ID,),
        {},
        "DELETE",
        f"/{MEMORY_ID}",
        {"deleted_count": 1},
        MemoryDeleteResult,
    ),
    ("delete_all_memories", (), {}, "DELETE", "", {"deleted_count": 3}, MemoryDeleteResult),
    (
        "get_memory_history",
        (MEMORY_ID,),
        {"limit": 2, "cursor": 7},
        "GET",
        f"/{MEMORY_ID}/history",
        {"history": [], "next_cursor": None},
        MemoryHistory,
    ),
]


@pytest.mark.parametrize("name,args,kwargs,method,path,data,model", OPERATIONS)
@pytest.mark.parametrize("async_call", [False, True])
@pytest.mark.asyncio
async def test_memory_methods_use_envelopes_auth_and_user_scope(
    name, args, kwargs, method, path, data, model, async_call
):
    client = AgentClient(base_url="http://test", get_info=False, timeout=12)
    client.auth_secret = "test-access-token"
    client.agent = "other-chat-agent"
    response = httpx.Response(
        200,
        json={"code": 200, "message": "success", "data": data},
        request=httpx.Request(method, "http://test/memories" + path),
    )
    target = "httpx.AsyncClient.request" if async_call else "httpx.request"
    with patch(target, return_value=response) as request:
        function = getattr(client, "a" + name if async_call else name)
        result = function(*args, user_id="alice", **kwargs)
        if async_call:
            result = await result
        assert isinstance(result, model)
        positional, sent = request.call_args
        assert positional == (method, "http://test/memories" + path)
        assert sent["headers"] == {"Authorization": "Bearer test-access-token"}
        assert sent["params"]["user_id"] == "alice"
        assert sent["timeout"] == 12
        if "limit" in kwargs:
            assert sent["params"]["limit"] == 2
            expected_cursor = str(kwargs["cursor"]) if name == "get_memories" else 7
            assert sent["params"]["cursor"] == expected_cursor
        if name == "update_memory":
            assert sent["json"] == {"text": "coffee"}


@pytest.mark.asyncio
@pytest.mark.parametrize("async_call", [False, True])
async def test_bound_user_token_can_omit_user_id(async_call):
    client = AgentClient(base_url="http://test", get_info=False)
    response = httpx.Response(
        200,
        json={"code": 200, "message": "success", "data": {"deleted_count": 0}},
        request=httpx.Request("DELETE", "http://test/memories"),
    )
    target = "httpx.AsyncClient.request" if async_call else "httpx.request"
    with patch(target, return_value=response) as request:
        result = await client.adelete_all_memories() if async_call else client.delete_all_memories()
        assert result.deleted_count == 0
        assert request.call_args.kwargs["params"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("async_call", [False, True])
@pytest.mark.parametrize("status", [403, 404, 503, 504])
async def test_memory_http_failures_raise_client_error(async_call, status):
    client = AgentClient(base_url="http://test", get_info=False)
    response = httpx.Response(
        status,
        json={"code": status, "message": "operation failed", "data": None},
        request=httpx.Request("DELETE", "http://test/memories"),
    )
    target = "httpx.AsyncClient.request" if async_call else "httpx.request"
    with patch(target, return_value=response):
        with pytest.raises(AgentClientError, match=str(status)):
            if async_call:
                await client.adelete_all_memories("alice")
            else:
                client.delete_all_memories("alice")


def test_invalid_update_is_rejected_before_sending():
    client = AgentClient(get_info=False)
    with patch("httpx.request") as request:
        with pytest.raises(ValidationError):
            client.update_memory(MEMORY_ID, " ", "alice")
        with pytest.raises(ValueError):
            client.delete_memory("../../other", "alice")
        request.assert_not_called()


@pytest.mark.asyncio
async def test_async_request_is_not_sent_for_invalid_update():
    client = AgentClient(get_info=False)
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        with pytest.raises(ValidationError):
            await client.aupdate_memory(MEMORY_ID, " ", "alice")
        request.assert_not_awaited()
