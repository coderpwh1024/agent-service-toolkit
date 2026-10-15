from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest

from memory.mem0_history import PostgresHistory

MEMORY_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def history_store():
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None
    return PostgresHistory(pool, "mem0_test", 7), conn


def inserted_payload(conn):
    inserts = [
        call
        for call in conn.execute.call_args_list
        if isinstance(call.args[0], str) and "INSERT INTO mem0_records" in call.args[0]
    ]
    return inserts[-1].args[1][3].obj


def test_new_history_contains_verified_live_owner(history_store):
    history, conn = history_store
    history.resolve_owner = Mock(return_value={"user_id": "alice", "agent_id": "mem0-chatbot"})
    history.add_history(MEMORY_ID, None, "tea", "ADD")
    payload = inserted_payload(conn)
    assert payload["user_id"] == "alice"
    assert payload["agent_id"] == "mem0-chatbot"
    assert payload["new_memory"] == "tea"


def test_deleted_history_can_inherit_owner_from_previous_events(history_store):
    history, conn = history_store
    history.resolve_owner = Mock(return_value=None)
    conn.execute.return_value.fetchone.return_value = ("alice", "mem0-chatbot")
    history.add_history(MEMORY_ID, "tea", None, "DELETE")
    payload = inserted_payload(conn)
    assert payload["user_id"] == "alice"
    assert payload["old_memory"] == "tea"
    assert payload["new_memory"] is None


def test_deleted_history_has_owner_even_without_previous_history(history_store):
    history, conn = history_store
    with history.history_scope(MEMORY_ID, "alice", "mem0-chatbot"):
        history.add_history(MEMORY_ID, "tea", None, "DELETE")
    assert inserted_payload(conn)["user_id"] == "alice"
    history.add_history(MEMORY_ID, None, "coffee", "ADD")
    assert "user_id" not in inserted_payload(conn)


def test_history_scope_does_not_authorize_other_memories_and_resets_on_error(history_store):
    history, conn = history_store
    with pytest.raises(RuntimeError):
        with history.history_scope("different-id", "alice", "mem0-chatbot"):
            history.add_history(MEMORY_ID, "tea", None, "DELETE")
            assert "user_id" not in inserted_payload(conn)
            raise RuntimeError("failed")
    history.add_history("different-id", None, "coffee", "ADD")
    assert "user_id" not in inserted_payload(conn)


@pytest.mark.parametrize("failure", [False, True])
def test_user_lock_commits_before_model_io_and_releases_on_error(history_store, failure):
    history, conn = history_store

    def run():
        with history.user_lock("alice&=中文", "mem0-chatbot"):
            assert conn.commit.call_count == 1
            assert "pg_advisory_lock" in conn.execute.call_args.args[0]
            if failure:
                raise ConnectionError("provider failed")

    if failure:
        with pytest.raises(ConnectionError):
            run()
    else:
        run()
    assert "pg_advisory_unlock" in conn.execute.call_args.args[0]
    assert conn.execute.call_args_list[0].args[1] == conn.execute.call_args_list[1].args[1]
    assert conn.commit.call_count == 2


def test_clear_context_uses_escaped_sdk_scope_and_logical_deletion(history_store):
    history, conn = history_store
    from mem0.memory.main import _build_session_scope

    history.clear_context("alice&=中文", "mem0-chatbot")
    query, args = conn.execute.call_args.args
    assert args == (
        7,
        "mem0_test",
        _build_session_scope({"user_id": "alice&=中文", "agent_id": "mem0-chatbot"}),
        _build_session_scope({"user_id": "alice&=中文", "agent_id": "mem0-chatbot"})
        + "&source_thread_id=",
    )
    assert "is_delete = 1" in query
    assert "update_time = CURRENT_TIMESTAMP" in query
    assert "kind = 'context'" in query
    assert "is_delete = 0" in query
    assert "starts_with(scope, %s)" in query


def test_extraction_context_uses_thread_scope_and_does_not_fall_back_to_legacy(history_store):
    history, conn = history_store
    scope = "agent_id=mem0-chatbot&user_id=alice"
    with history.extraction_scope("thread&=one", []):
        assert history.get_last_messages(scope) == []
        conn.execute.assert_not_called()
        history.save_messages([{"role": "user", "content": "tea"}], scope)
        insert = next(c for c in conn.execute.call_args_list if "INSERT INTO" in c.args[0])
        assert insert.args[1][2] == scope + "&source_thread_id=thread%26%3Done"
    conn.reset_mock()
    with history.extraction_scope("thread-two", None):
        history.get_last_messages(scope)
        assert conn.execute.call_args.args[1][1] == scope + "&source_thread_id=thread-two"


def test_extraction_reference_contains_assistant_context_and_resets_on_failure(history_store):
    history, conn = history_store
    context = [{"role": "assistant", "content": "Which city?"}]
    with pytest.raises(RuntimeError):
        with history.extraction_scope("thread", context):
            assert history.get_last_messages("scope") == context
            raise RuntimeError("cancelled")
    conn.execute.return_value.fetchall.return_value = []
    assert history.get_last_messages("scope") == []
    assert conn.execute.call_args.args[1][1] == "scope"


def test_list_is_scoped_and_uses_bounded_uuid_cursor(history_store):
    history, conn = history_store
    conn.execute.return_value.fetchall.return_value = [(UUID(MEMORY_ID), {"data": "tea"})]
    result = history.list_memories("alice", "mem0-chatbot", 51, UUID(MEMORY_ID))
    assert result == [{"id": MEMORY_ID, "data": "tea"}]
    assert conn.execute.call_args.args[1] == (
        "alice",
        "mem0-chatbot",
        UUID(MEMORY_ID),
        UUID(MEMORY_ID),
        51,
    )


def test_history_page_and_legacy_backfill_are_collection_scoped(history_store):
    history, conn = history_store
    conn.execute.return_value.fetchall.return_value = [
        (8, {"memory_id": MEMORY_ID, "event": "ADD"})
    ]
    assert history.history_page(MEMORY_ID, 51, 7) == [
        {"id": 8, "memory_id": MEMORY_ID, "event": "ADD"}
    ]
    assert conn.execute.call_args.args[1] == ("mem0_test", MEMORY_ID, 7, 7, 51)
    history.bind_history_owner(MEMORY_ID, "alice", "mem0-chatbot")
    query, args = conn.execute.call_args.args
    assert args[0].obj == {"user_id": "alice", "agent_id": "mem0-chatbot"}
    assert args[1:] == (7, "mem0_test", MEMORY_ID)
    assert "update_time = CURRENT_TIMESTAMP" in query
    assert "NOT (payload ? 'user_id' AND payload ? 'agent_id')" in query
