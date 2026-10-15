import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from core.settings import Settings
from memory.mem0_sdk import ValidatedExtractionLlm, build_memory, close_memory


def test_real_sdk_bridge_never_opens_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_DIR", str(tmp_path))
    from mem0 import Memory
    from mem0.utils.factory import VectorStoreFactory

    def forbidden(*args, **kwargs):
        raise AssertionError("SQLite is forbidden")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    vector_store = Mock()
    monkeypatch.setattr(VectorStoreFactory, "create", lambda *args: vector_store)
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value = Mock()
    connection.execute.return_value.fetchone.return_value = ("vector(1024)",)
    config = Settings(_env_file=None, USE_FAKE_MODEL=True, MEM0_API_KEY=SecretStr("test"))
    memory = build_memory(config, pool)
    try:
        assert isinstance(memory, Memory)
        assert memory.db.pool is pool
        assert memory.embedding_model.client.timeout == config.MEM0_TIMEOUT_SECONDS
        memory.llm.provider.generate_response = Mock(return_value='{"memory": []}')
        memory.embedding_model.embed = Mock(return_value=[1.0] * 1024)
        vector_store.search.return_value = []
        connection.execute.return_value.fetchall.return_value = []
        result = memory.add(
            [{"role": "user", "content": "hello"}], user_id="alice", agent_id="mem0-chatbot"
        )
        assert result == {"results": []}
        assert any(
            "INSERT INTO mem0_records" in call.args[0] for call in connection.execute.call_args_list
        )
        from mem0.exceptions import LLMError

        memory.llm.provider.generate_response.return_value = "invalid extraction"
        save_messages = Mock()
        monkeypatch.setattr(memory.db, "save_messages", save_messages)
        with pytest.raises(LLMError, match="Invalid Mem0 extraction response"):
            memory.add(
                [{"role": "user", "content": "I prefer coffee"}],
                user_id="alice",
                agent_id="mem0-chatbot",
            )
        save_messages.assert_not_called()
    finally:
        close_memory(memory)


@pytest.mark.parametrize(
    "response",
    [
        "",
        "invalid JSON",
        "{}",
        '{"memory":null}',
        '{"memory":[{"text":42}]}',
        '{"memory":[{"text":" "}]}',
        '{"memory":[{}]}',
        None,
    ],
)
def test_invalid_extraction_output_raises_without_exposing_response(response):
    provider = Mock(generate_response=Mock(return_value=response))
    model = ValidatedExtractionLlm(provider)
    with pytest.raises(ValueError, match="^Invalid Mem0 extraction response$"):
        model.generate_response(messages=[])


@pytest.mark.parametrize(
    "response",
    [
        '{"memory":[]}',
        '```json\n{"memory":[]}\n```',
    ],
)
def test_valid_empty_extraction_is_success(response):
    model = ValidatedExtractionLlm(Mock(generate_response=Mock(return_value=response)))
    assert model.generate_response(messages=[]) == '{"memory":[]}'


def test_valid_extraction_preserves_sdk_linking_fields():
    model = ValidatedExtractionLlm(
        Mock(
            generate_response=Mock(
                return_value='{"memory":[{"text":"喜欢茶","linked_memory_ids":["0"]}]}'
            )
        )
    )
    assert '"linked_memory_ids":["0"]' in model.generate_response(messages=[])


def test_real_sdk_update_delete_and_history_preserve_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_DIR", str(tmp_path))
    from mem0.utils.factory import VectorStoreFactory

    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda _text: [])
    vector_store = Mock()
    monkeypatch.setattr(VectorStoreFactory, "create", lambda *args: vector_store)
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = ("vector(1024)",)
    config = Settings(_env_file=None, USE_FAKE_MODEL=True, MEM0_API_KEY=SecretStr("test"))
    memory = build_memory(config, pool)
    memory_id = str(uuid4())
    rows = {
        memory_id: SimpleNamespace(
            id=memory_id,
            payload={
                "data": "tea",
                "user_id": "alice",
                "agent_id": "mem0-chatbot",
                "created_at": "2026-01-01T00:00:00+00:00",
                "source_thread_id": "thread",
            },
        )
    }
    vector_store.get.side_effect = lambda vector_id: rows.get(vector_id)

    def update(vector_id, vector, payload):
        rows[vector_id].payload = payload

    vector_store.update.side_effect = update
    vector_store.delete.side_effect = lambda vector_id: rows.pop(vector_id)
    memory.embedding_model.embed = Mock(return_value=[1.0] * 1024)
    conn.execute.return_value.fetchone.return_value = None
    try:
        memory.update(memory_id=memory_id, text="coffee")
        record = memory.get(memory_id=memory_id)
        assert record["memory"] == "coffee"
        assert record["metadata"]["source_thread_id"] == "thread"
        assert record["created_at"] == "2026-01-01T00:00:00+00:00"
        assert record["updated_at"] != record["created_at"]
        memory.embedding_model.embed.assert_called_once_with("coffee", "update")
        with memory.db.history_scope(memory_id, "alice", "mem0-chatbot"):
            memory.delete(memory_id=memory_id)
        assert memory.get(memory_id=memory_id) is None
        events = [
            call.args[1][3].obj
            for call in conn.execute.call_args_list
            if isinstance(call.args[0], str) and "INSERT INTO mem0_records" in call.args[0]
        ]
        assert [event["event"] for event in events] == ["UPDATE", "DELETE"]
        assert [(event["old_memory"], event["new_memory"]) for event in events] == [
            ("tea", "coffee"),
            ("coffee", None),
        ]
        assert all(
            event["user_id"] == "alice" and event["agent_id"] == "mem0-chatbot" for event in events
        )
    finally:
        close_memory(memory)
