"""Tests for daily chat-record endpoints."""

from datetime import UTC, datetime, time, timedelta

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr

from agents import DEFAULT_AGENT
from service.access import create_access_token


class FakeCheckpointTuple:
    def __init__(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint_at: datetime,
        messages: list,
        user_id: str,
        agent_id: str,
    ) -> None:
        self.config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        self.checkpoint = {
            "ts": checkpoint_at.isoformat(),
            "channel_values": {"messages": messages},
        }
        self.metadata = {"user_id": user_id, "agent_id": agent_id}


class FakeCheckpointer:
    def __init__(self, *, ignore_filter: bool = False) -> None:
        self.rows: list[FakeCheckpointTuple] = []
        self.ignore_filter = ignore_filter
        self._next_id = 0

    def add(
        self,
        thread_id: str,
        checkpoint_at: datetime,
        messages: list,
        user_id: str = "user-123",
        agent_id: str = DEFAULT_AGENT,
    ) -> None:
        self._next_id += 1
        self.rows.append(
            FakeCheckpointTuple(
                thread_id,
                f"cp-{self._next_id:06d}",
                checkpoint_at,
                messages,
                user_id,
                agent_id,
            )
        )

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = sorted(
            self.rows,
            key=lambda row: row.config["configurable"]["checkpoint_id"],
            reverse=True,
        )
        yielded = 0
        for row in rows:
            configurable = row.config["configurable"]
            if config:
                requested = config["configurable"]
                if requested.get("thread_id") != configurable["thread_id"]:
                    continue
                if requested.get("checkpoint_ns", "") != configurable["checkpoint_ns"]:
                    continue
            if before and configurable["checkpoint_id"] >= before["configurable"]["checkpoint_id"]:
                continue
            if not self.ignore_filter and filter:
                if any(row.metadata.get(key) != value for key, value in filter.items()):
                    continue
            yield row
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def test_chat_records_groups_latest_30_days_and_orders_descending(test_client, mock_agent) -> None:
    today = datetime.now(UTC).date()
    checkpointer = FakeCheckpointer(ignore_filter=True)
    checkpointer.add(
        "thread-expired",
        datetime.combine(today - timedelta(days=30), time(12), tzinfo=UTC),
        [HumanMessage(content="Expired")],
    )
    checkpointer.add(
        "thread-yesterday",
        datetime.combine(today - timedelta(days=1), time(9), tzinfo=UTC),
        [HumanMessage(content="Yesterday")],
    )
    checkpointer.add(
        "thread-today-a",
        datetime.combine(today, time(8), tzinfo=UTC),
        [HumanMessage(content="Today A")],
    )
    checkpointer.add(
        "thread-today-b",
        datetime.combine(today, time(10), tzinfo=UTC),
        [HumanMessage(content="Today B")],
    )
    checkpointer.add(
        "thread-other-user",
        datetime.combine(today, time(11), tzinfo=UTC),
        [HumanMessage(content="Private")],
        user_id="other-user",
    )
    mock_agent.checkpointer = checkpointer

    response = test_client.get("/chat-records", params={"user_id": "user-123"})

    assert response.status_code == 200
    records = response.json()["data"]["records"]
    assert [record["record_date"] for record in records] == [
        today.isoformat(),
        (today - timedelta(days=1)).isoformat(),
    ]
    assert records[0]["conversation_count"] == 2
    assert records[0]["latest_at"].startswith(f"{today.isoformat()}T10:00:00")


def test_chat_record_detail_returns_only_messages_added_that_day(test_client, mock_agent) -> None:
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    old_messages = [HumanMessage(content="Old question"), AIMessage(content="Old answer")]
    current_messages = old_messages + [
        HumanMessage(content="Today question"),
        AIMessage(content="Today answer"),
    ]
    checkpointer = FakeCheckpointer()
    checkpointer.add(
        "thread-1",
        datetime.combine(yesterday, time(12), tzinfo=UTC),
        old_messages,
    )
    checkpointer.add(
        "thread-1",
        datetime.combine(today, time(9), tzinfo=UTC),
        current_messages,
    )
    mock_agent.checkpointer = checkpointer

    response = test_client.get(f"/chat-records/{today.isoformat()}", params={"user_id": "user-123"})

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["record_date"] == today.isoformat()
    assert len(detail["conversations"]) == 1
    conversation = detail["conversations"][0]
    assert conversation["thread_id"] == "thread-1"
    assert conversation["title"] == "Old question"
    assert [(message["type"], message["content"]) for message in conversation["messages"]] == [
        ("human", "Today question"),
        ("ai", "Today answer"),
    ]


def test_chat_record_detail_rejects_date_outside_latest_30_days(test_client, mock_agent) -> None:
    old_date = datetime.now(UTC).date() - timedelta(days=30)

    response = test_client.get(
        f"/chat-records/{old_date.isoformat()}", params={"user_id": "user-123"}
    )

    assert response.status_code == 422
    assert response.json()["message"] == "record_date must be within the latest 30 days"


def test_chat_records_without_checkpointer_returns_empty(test_client, mock_agent) -> None:
    mock_agent.checkpointer = None

    response = test_client.get("/chat-records", params={"user_id": "user-123"})

    assert response.status_code == 200
    assert response.json()["data"] == {"records": []}


def test_chat_records_rejects_another_user_for_app_token(
    test_client, mock_agent, mock_settings
) -> None:
    mock_settings.AUTH_SECRET = SecretStr("admin-secret")
    mock_settings.APP_TOKEN_SECRET = SecretStr("x" * 32)
    mock_settings.APP_TOKEN_TTL_SECONDS = 900
    token, _ = create_access_token("user-123", mock_settings)

    response = test_client.get(
        "/chat-records",
        params={"user_id": "other-user"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "user_id does not match the authenticated user"


def test_chat_records_checkpointer_error_returns_500(test_client, mock_agent) -> None:
    async def broken_alist(*args, **kwargs):
        raise RuntimeError("db unavailable")
        yield

    mock_agent.checkpointer = type("Checkpointer", (), {"alist": broken_alist})()

    response = test_client.get("/chat-records", params={"user_id": "user-123"})

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "message": "Unexpected error",
        "data": None,
    }
