"""Real Mem0 extraction/search and PostgreSQL, with deterministic model responses."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool
from pydantic import SecretStr

from core.settings import Settings
from memory.mem0_sdk import build_memory, close_memory
from memory.mem0_service import Mem0Service, MemoryConflictError, MemoryNotFoundError
from memory.postgres import get_postgres_connection_string
from schema.memory_commands import MemoryMutation, MemoryTarget


@pytest.mark.docker
@pytest.mark.asyncio
async def test_mem0_facts_survive_reconnect_and_isolate_users(monkeypatch, tmp_path):
    collection = f"mem0_smoke_{uuid4().hex[:16]}"
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_DIR", str(tmp_path))
    config = Settings(
        _env_file=None,
        USE_FAKE_MODEL=True,
        MEM0_API_KEY=SecretStr("test"),
        MEM0_COLLECTION_NAME=collection,
        MEM0_EMBEDDING_DIMS=3,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Mem0 must not use SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden)

    def configure_models(memory):
        memory.llm.client.close()
        memory.embedding_model.client.close()
        memory.llm = SimpleNamespace(
            client=Mock(),
            generate_response=lambda **kwargs: json.dumps(
                {"memory": [{"text": "User prefers tea"}]}
            ),
        )
        memory.embedding_model = SimpleNamespace(
            client=Mock(),
            config=SimpleNamespace(embedding_dims=3),
            embed=lambda *args: [1.0, 0.0, 0.0],
            embed_batch=lambda texts, *args: [[1.0, 0.0, 0.0] for _ in texts],
        )

    def exercise():
        conninfo = get_postgres_connection_string()
        with ConnectionPool(conninfo, min_size=1, max_size=2) as pool:
            memory = build_memory(config, pool)
            configure_models(memory)
            try:
                result = memory.add(
                    [{"role": "user", "content": "I prefer tea"}],
                    user_id="alice",
                    agent_id="mem0-chatbot",
                )
                assert result["results"]
                memory_id = result["results"][0]["id"]
                assert memory.history(memory_id)[0]["new_memory"] == "User prefers tea"
                memory.add(
                    [{"role": "user", "content": "I prefer tea"}],
                    user_id="alice",
                    agent_id="mem0-chatbot",
                )
                for index in range(12):
                    memory.db.save_messages(
                        [{"role": "user", "content": str(index)}], "context-test"
                    )
                assert [m["content"] for m in memory.db.get_last_messages("context-test")] == [
                    str(i) for i in range(2, 12)
                ]
                with pool.connection() as conn:
                    count = conn.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(collection))
                    ).fetchone()
                    assert count == (1,)
                    audit = conn.execute(
                        """SELECT create_by, update_by, is_delete FROM mem0_records
                           WHERE collection_name = %s AND kind = 'history'""",
                        (collection,),
                    ).fetchone()
                    assert audit == (
                        config.AUTH_SERVICE_ACCOUNT_ID,
                        config.AUTH_SERVICE_ACCOUNT_ID,
                        0,
                    )
            finally:
                close_memory(memory)
        with ConnectionPool(conninfo, min_size=1, max_size=2) as pool:
            memory = build_memory(config, pool)
            configure_models(memory)
            try:
                results = memory.search(
                    "drink", filters={"user_id": "alice", "agent_id": "mem0-chatbot"}
                )
                assert any(m["memory"] == "User prefers tea" for m in results["results"])
                assert (
                    memory.search("drink", filters={"user_id": "bob", "agent_id": "mem0-chatbot"})[
                        "results"
                    ]
                    == []
                )
                assert (
                    memory.search("drink", filters={"user_id": "alice", "agent_id": "other"})[
                        "results"
                    ]
                    == []
                )
                asyncio.run(manage(memory, memory_id))
            finally:
                close_memory(memory)
                with pool.connection() as conn:
                    for table in (collection, collection + "_entities"):
                        conn.execute(
                            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
                        )
                    conn.execute(
                        "DELETE FROM mem0_records WHERE collection_name = %s", (collection,)
                    )

    async def manage(memory, memory_id):
        for user_id, agent_id in [("bob", "mem0-chatbot"), ("alice", "other")]:
            memory.add(
                [{"role": "user", "content": "I prefer tea"}],
                user_id=user_id,
                agent_id=agent_id,
            )
        with ThreadPoolExecutor(max_workers=1) as executor:
            service = Mem0Service(memory, executor)
            listing = await service.list_memories("alice", 1)
            assert [str(record.id) for record in listing.memories] == [memory_id]
            assert listing.next_cursor is None
            old_target = MemoryTarget.model_validate(listing.memories[0].model_dump())
            with pytest.raises(MemoryNotFoundError):
                await service.update_memory(memory_id, "bob", "tampered")
            updated = await service.update_memory(memory_id, "alice", "User prefers coffee")
            assert updated.memory == "User prefers coffee"
            with pytest.raises(MemoryConflictError):
                await service.apply_memory_mutation(
                    "alice", MemoryMutation(action="delete", target=old_target)
                )
            assert memory.db.get_last_messages("agent_id=mem0-chatbot&user_id=alice") == []
            history = await service.memory_history(memory_id, "alice", 1)
            assert history.history[0].event == "ADD"
            assert history.next_cursor is not None
            next_page = await service.memory_history(memory_id, "alice", 1, history.next_cursor)
            assert next_page.history[0].event == "UPDATE"
            assert next_page.history[0].old_memory == "User prefers tea"
            assert next_page.history[0].new_memory == "User prefers coffee"
            assert (await service.delete_memory(memory_id, "alice")).deleted_count == 1
            with pytest.raises(MemoryNotFoundError):
                await service.get_memory(memory_id, "alice")
            deleted_history = await service.memory_history(memory_id, "alice")
            assert [entry.event for entry in deleted_history.history] == ["ADD", "UPDATE", "DELETE"]
            with pytest.raises(MemoryNotFoundError):
                await service.memory_history(memory_id, "bob")
            memory.add(
                [{"role": "user", "content": f"fact {index}"} for index in range(101)],
                user_id="alice",
                agent_id="mem0-chatbot",
                infer=False,
            )
            first_page = await service.list_memories("alice", 100)
            assert len(first_page.memories) == 100
            assert first_page.next_cursor is not None
            last_page = await service.list_memories("alice", 100, first_page.next_cursor)
            assert len(last_page.memories) == 1
            snapshot = await service.memory_snapshot("alice")
            assert snapshot.count == 101
            cleared = await service.apply_memory_mutation(
                "alice", MemoryMutation(action="delete_all", revision=snapshot.revision)
            )
            assert cleared.deleted_count == 101
            assert (await service.delete_all_memories("alice")).deleted_count == 0
            assert (await service.list_memories("alice")).memories == []
            assert (await service.list_memories("bob")).memories
            assert memory.search("drink", filters={"user_id": "alice", "agent_id": "other"})[
                "results"
            ]
            assert (await service.memory_history(memory_id, "alice")).history[-1].event == "DELETE"

    await asyncio.to_thread(exercise)
