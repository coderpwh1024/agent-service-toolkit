import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from memory.mem0_service import AGENT_ID, Mem0Service, MemoryConflictError, MemoryNotFoundError
from schema.memory_commands import MemoryMutation, MemoryTarget

MEMORY_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def mutation_service():
    rows = {
        MEMORY_ID: {
            "id": MEMORY_ID,
            "memory": "tea",
            "user_id": "alice",
            "agent_id": AGENT_ID,
            "updated_at": "2026-01-01T00:00:00Z",
        }
    }
    sdk = MagicMock()
    sdk.get.side_effect = lambda memory_id: dict(rows[memory_id]) if memory_id in rows else None

    def update(memory_id, text):
        rows[memory_id].update(memory=text, updated_at="2026-01-02T00:00:00Z")

    def listing(user_id, agent_id, limit, cursor=None):
        return [
            dict(record, data=record["memory"])
            for memory_id, record in sorted(rows.items())
            if record["user_id"] == user_id
            and record["agent_id"] == agent_id
            and (cursor is None or UUID(memory_id) > cursor)
        ][:limit]

    sdk.update.side_effect = update
    sdk.delete.side_effect = lambda memory_id: rows.pop(memory_id)
    sdk.db.list_memories.side_effect = listing
    with ThreadPoolExecutor(max_workers=1) as executor:
        yield Mem0Service(sdk, executor), sdk, rows


def target(rows):
    record = rows[MEMORY_ID]
    return MemoryTarget(id=MEMORY_ID, memory=record["memory"], updated_at=record["updated_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "delete"])
async def test_spoken_preview_must_still_match_before_mutating(mutation_service, action):
    service, sdk, rows = mutation_service
    preview = target(rows)
    rows[MEMORY_ID]["memory"] = "changed through HTTP API"
    mutation = MemoryMutation(
        action=action, target=preview, text="coffee" if action == "update" else None
    )
    with pytest.raises(MemoryConflictError):
        await service.apply_memory_mutation("alice", mutation)
    sdk.update.assert_not_called()
    sdk.delete.assert_not_called()
    sdk.db.clear_context.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_update_confirmation_does_not_duplicate_history(mutation_service):
    service, sdk, rows = mutation_service
    mutation = MemoryMutation(action="update", target=target(rows), text="coffee")
    assert (await service.apply_memory_mutation("alice", mutation)).status == "updated"
    assert (await service.apply_memory_mutation("alice", mutation)).status == "unchanged"
    sdk.update.assert_called_once_with(memory_id=MEMORY_ID, text="coffee")
    sdk.db.clear_context.assert_called_once_with("alice", AGENT_ID)


@pytest.mark.asyncio
async def test_repeated_delete_cannot_delete_any_other_memory(mutation_service):
    service, sdk, rows = mutation_service
    mutation = MemoryMutation(action="delete", target=target(rows))
    assert (await service.apply_memory_mutation("alice", mutation)).deleted_count == 1
    with pytest.raises(MemoryNotFoundError):
        await service.apply_memory_mutation("alice", mutation)
    sdk.delete.assert_called_once_with(memory_id=MEMORY_ID)


@pytest.mark.asyncio
async def test_spoken_mutation_cannot_cross_user_boundaries(mutation_service):
    service, sdk, rows = mutation_service
    with pytest.raises(MemoryNotFoundError):
        await service.apply_memory_mutation(
            "bob", MemoryMutation(action="delete", target=target(rows))
        )
    sdk.delete.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_detects_new_memories_and_prevents_stale_clear(mutation_service):
    service, sdk, rows = mutation_service
    snapshot = await service.memory_snapshot("alice")
    assert snapshot.count == 1
    new_id = "00000000-0000-0000-0000-000000000002"
    rows[new_id] = dict(rows[MEMORY_ID], id=new_id, memory="new fact")
    with pytest.raises(MemoryConflictError):
        await service.apply_memory_mutation(
            "alice", MemoryMutation(action="delete_all", revision=snapshot.revision)
        )
    sdk.delete.assert_not_called()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_snapshot_pages_all_facts_and_clear_retains_other_scopes(mutation_service):
    service, sdk, rows = mutation_service
    for index in range(2, 104):
        memory_id = str(UUID(int=index))
        rows[memory_id] = dict(rows[MEMORY_ID], id=memory_id, memory=f"fact {index}")
    rows[str(UUID(int=200))] = dict(rows[MEMORY_ID], id=str(UUID(int=200)), user_id="bob")
    rows[str(UUID(int=201))] = dict(rows[MEMORY_ID], id=str(UUID(int=201)), agent_id="other")
    snapshot = await service.memory_snapshot("alice")
    assert snapshot.count == 103
    mutation = MemoryMutation(action="delete_all", revision=snapshot.revision)
    result = await service.apply_memory_mutation("alice", mutation)
    assert result.deleted_count == 103
    assert set(rows) == {str(UUID(int=200)), str(UUID(int=201))}
    assert (await service.apply_memory_mutation("alice", mutation)).deleted_count == 0
    rows[MEMORY_ID] = {
        "id": MEMORY_ID,
        "memory": "later fact",
        "user_id": "alice",
        "agent_id": AGENT_ID,
    }
    with pytest.raises(MemoryConflictError):
        await service.apply_memory_mutation("alice", mutation)
    assert rows[MEMORY_ID]["memory"] == "later fact"


@pytest.mark.asyncio
async def test_record_search_keeps_ids_and_checks_sdk_scope(mutation_service):
    service, sdk, _ = mutation_service
    sdk.search.return_value = {"results": [{"id": MEMORY_ID, "memory": "tea"}]}
    result = await service.search_memory_records("tea", "alice")
    assert str(result.memories[0].id) == MEMORY_ID
    assert sdk.search.call_args.kwargs["filters"] == {"user_id": "alice", "agent_id": AGENT_ID}
    with pytest.raises(MemoryNotFoundError):
        await service.search_memory_records("tea", "bob")


@pytest.mark.asyncio
async def test_cancelled_voice_update_can_finish_without_duplicate_retry(mutation_service):
    service, sdk, rows = mutation_service
    started, release = Event(), Event()
    original_update = sdk.update.side_effect

    def slow_update(memory_id, text):
        started.set()
        release.wait(5)
        original_update(memory_id, text)

    sdk.update.side_effect = slow_update
    mutation = MemoryMutation(action="update", target=target(rows), text="coffee")
    task = asyncio.create_task(service.apply_memory_mutation("alice", mutation))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.pending
    finally:
        release.set()
        await asyncio.gather(*service.pending)
    assert (await service.apply_memory_mutation("alice", mutation)).status == "unchanged"
    sdk.update.assert_called_once_with(memory_id=MEMORY_ID, text="coffee")


@pytest.mark.asyncio
async def test_empty_voice_clear_also_clears_extraction_context(mutation_service):
    service, sdk, rows = mutation_service
    rows.clear()
    snapshot = await service.memory_snapshot("alice")
    result = await service.apply_memory_mutation(
        "alice", MemoryMutation(action="delete_all", revision=snapshot.revision)
    )
    assert result.deleted_count == 0
    sdk.db.clear_context.assert_called_once_with("alice", AGENT_ID)
    sdk.delete.assert_not_called()


@pytest.mark.asyncio
async def test_batch_deletes_only_selected_memories_and_retains_other_scopes(mutation_service):
    service, sdk, rows = mutation_service
    selected = [target(rows)]
    for index, owner, agent in [
        (2, "alice", AGENT_ID),
        (3, "alice", AGENT_ID),
        (4, "bob", AGENT_ID),
        (5, "alice", "other"),
    ]:
        memory_id = str(UUID(int=index))
        rows[memory_id] = dict(rows[MEMORY_ID], id=memory_id, user_id=owner, agent_id=agent)
        if index == 2:
            selected.append(MemoryTarget.model_validate(rows[memory_id]))
    result = await service.apply_memory_mutation(
        "alice", MemoryMutation(action="delete_many", targets=selected)
    )
    assert result.status == "deleted" and result.deleted_count == 2
    assert set(rows) == {str(UUID(int=index)) for index in [3, 4, 5]}
    assert sdk.db.bind_history_owner.call_count == 2
    assert sdk.db.history_scope.call_count == 2
    assert (await service.memory_snapshot("alice")).count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["changed", "missing", "foreign_user", "foreign_agent"])
async def test_batch_validates_every_target_before_any_deletion(mutation_service, failure):
    service, sdk, rows = mutation_service
    first = target(rows)
    second_id = str(UUID(int=2))
    rows[second_id] = dict(rows[MEMORY_ID], id=second_id)
    second = MemoryTarget.model_validate(rows[second_id])
    if failure == "changed":
        rows[second_id]["updated_at"] = "changed"
    elif failure == "missing":
        del rows[second_id]
    elif failure == "foreign_user":
        rows[second_id]["user_id"] = "bob"
    else:
        rows[second_id]["agent_id"] = "other"
    error = MemoryConflictError if failure == "changed" else MemoryNotFoundError
    with pytest.raises(error):
        await service.apply_memory_mutation(
            "alice", MemoryMutation(action="delete_many", targets=[first, second])
        )
    sdk.delete.assert_not_called()
    sdk.db.clear_context.assert_not_called()
    assert MEMORY_ID in rows


def test_batch_mutation_rejects_empty_duplicate_or_mixed_targets():
    from pydantic import ValidationError

    selected = MemoryTarget(id=MEMORY_ID, memory="tea")
    for fields in [
        {"targets": []},
        {"targets": [selected, selected]},
        {"targets": [selected], "target": selected},
        {"targets": [selected], "revision": "all"},
        {"targets": [selected], "text": "replacement"},
    ]:
        with pytest.raises(ValidationError):
            MemoryMutation(action="delete_many", **fields)
