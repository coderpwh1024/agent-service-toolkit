import sqlite3
from unittest.mock import MagicMock, Mock

from pydantic import SecretStr

from core.settings import Settings
from memory.mem0_sdk import build_memory, close_memory


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
        memory.llm.generate_response = Mock(return_value='{"memory": []}')
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
    finally:
        close_memory(memory)
