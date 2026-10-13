"""Daily chat-record views derived from LangGraph checkpoints."""

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from schema import ChatRecordDay, DailyChatConversation
from service.utils import (
    convert_message_content_to_string,
    langchain_to_chat_message,
    messages_from_checkpoint,
)

logger = logging.getLogger(__name__)

CHAT_RECORD_DAYS = 30
CHECKPOINT_PAGE_SIZE = 500
TITLE_MAX_LENGTH = 60


def recent_record_dates(now: datetime | None = None) -> tuple[date, date]:
    current_date = (now or datetime.now(UTC)).astimezone(UTC).date()
    return current_date - timedelta(days=CHAT_RECORD_DAYS - 1), current_date


def _checkpoint_time(row: Any) -> datetime | None:
    value = row.checkpoint.get("ts")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _checkpoint_rows(
    checkpointer: Any,
    user_id: str,
    agent_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[tuple[Any, datetime]]:
    rows: list[tuple[Any, datetime]] = []
    before = None
    metadata_filter = {"user_id": user_id, "agent_id": agent_id}

    while True:
        page = [
            row
            async for row in checkpointer.alist(
                None,
                filter=metadata_filter,
                before=before,
                limit=CHECKPOINT_PAGE_SIZE,
            )
        ]
        if not page:
            break

        reached_start = False
        for row in page:
            checkpoint_at = _checkpoint_time(row)
            if checkpoint_at is None:
                continue
            if checkpoint_at < start_at:
                reached_start = True
                break
            if checkpoint_at >= end_at:
                continue

            configurable = row.config.get("configurable", {})
            if configurable.get("checkpoint_ns", ""):
                continue
            metadata = row.metadata or {}
            if metadata.get("user_id") != user_id or metadata.get("agent_id") != agent_id:
                logger.warning("Skipping checkpoint with mismatched chat-record ownership metadata")
                continue
            rows.append((row, checkpoint_at))

        if reached_start or len(page) < CHECKPOINT_PAGE_SIZE:
            break
        before = RunnableConfig(
            configurable={"checkpoint_id": page[-1].config["configurable"]["checkpoint_id"]}
        )

    return rows


async def list_chat_record_days(
    checkpointer: Any,
    user_id: str,
    agent_id: str,
    now: datetime | None = None,
) -> list[ChatRecordDay]:
    first_date, current_date = recent_record_dates(now)
    start_at = datetime.combine(first_date, time.min, tzinfo=UTC)
    end_at = datetime.combine(current_date + timedelta(days=1), time.min, tzinfo=UTC)
    rows = await _checkpoint_rows(checkpointer, user_id, agent_id, start_at, end_at)

    activity: dict[date, tuple[datetime, set[str]]] = {}
    for row, checkpoint_at in rows:
        record_date = checkpoint_at.date()
        thread_id = row.config["configurable"]["thread_id"]
        if record_date not in activity:
            activity[record_date] = (checkpoint_at, {thread_id})
            continue
        latest_at, thread_ids = activity[record_date]
        thread_ids.add(thread_id)
        activity[record_date] = (max(latest_at, checkpoint_at), thread_ids)

    return [
        ChatRecordDay(
            record_date=record_date,
            latest_at=latest_at,
            conversation_count=len(thread_ids),
        )
        for record_date, (latest_at, thread_ids) in sorted(activity.items(), reverse=True)
    ]


async def _messages_before(
    checkpointer: Any,
    row: Any,
    user_id: str,
    agent_id: str,
) -> list[BaseMessage]:
    configurable = row.config["configurable"]
    config = RunnableConfig(
        configurable={
            "thread_id": configurable["thread_id"],
            "checkpoint_ns": configurable.get("checkpoint_ns", ""),
        }
    )
    previous_rows = [
        previous
        async for previous in checkpointer.alist(
            config,
            filter={"user_id": user_id, "agent_id": agent_id},
            before=row.config,
            limit=1,
        )
    ]
    if not previous_rows:
        return []
    previous = previous_rows[0]
    metadata = previous.metadata or {}
    if metadata.get("user_id") != user_id or metadata.get("agent_id") != agent_id:
        return []
    return messages_from_checkpoint(previous.checkpoint)


def _messages_added(
    previous_messages: list[BaseMessage], latest_messages: list[BaseMessage]
) -> list[BaseMessage]:
    shared = 0
    for previous, latest in zip(previous_messages, latest_messages, strict=False):
        if previous != latest:
            break
        shared += 1
    return latest_messages[shared:]


async def get_chat_record_detail(
    checkpointer: Any,
    user_id: str,
    agent_id: str,
    record_date: date,
) -> list[DailyChatConversation]:
    start_at = datetime.combine(record_date, time.min, tzinfo=UTC)
    end_at = start_at + timedelta(days=1)
    rows = await _checkpoint_rows(checkpointer, user_id, agent_id, start_at, end_at)

    rows_by_thread: dict[str, list[tuple[Any, datetime]]] = defaultdict(list)
    for row, checkpoint_at in rows:
        rows_by_thread[row.config["configurable"]["thread_id"]].append((row, checkpoint_at))

    conversations: list[DailyChatConversation] = []
    for thread_id, thread_rows in rows_by_thread.items():
        latest_row, updated_at = thread_rows[0]
        earliest_row = thread_rows[-1][0]
        latest_messages = messages_from_checkpoint(latest_row.checkpoint)
        previous_messages = await _messages_before(checkpointer, earliest_row, user_id, agent_id)
        daily_messages = _messages_added(previous_messages, latest_messages)
        first_human = next(
            (message for message in latest_messages if isinstance(message, HumanMessage)),
            None,
        )
        conversations.append(
            DailyChatConversation(
                thread_id=thread_id,
                agent_id=agent_id,
                updated_at=updated_at,
                title=(
                    convert_message_content_to_string(first_human.content)[:TITLE_MAX_LENGTH]
                    if first_human
                    else None
                ),
                messages=[langchain_to_chat_message(message) for message in daily_messages],
            )
        )

    conversations.sort(key=lambda conversation: conversation.updated_at, reverse=True)
    return conversations
