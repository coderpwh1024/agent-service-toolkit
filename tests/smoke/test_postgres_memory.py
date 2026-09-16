"""Exercise both production memory factories against a real PostgreSQL database."""

from uuid import uuid4

import psycopg
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.base import BaseStore
from langgraph.store.postgres import AsyncPostgresStore

from memory import initialize_database, initialize_store
from memory.postgres import get_postgres_connection_string


@pytest.mark.docker
@pytest.mark.asyncio
async def test_both_memories_survive_reconnecting():
    """A new thread reads user memory after all original pools have been closed."""
    user_id = f"memory-smoke-{uuid4()}"
    first_thread = f"{user_id}-first"
    second_thread = f"{user_id}-second"

    async def remember(state: MessagesState, config: RunnableConfig, *, store: BaseStore):
        namespace = (config["configurable"]["user_id"],)
        saved = await store.aget(namespace, "preference")
        if saved is None:
            value = {"drink": state["messages"][-1].content}
            await store.aput(namespace, "preference", value)
        else:
            value = saved.value
        return {"messages": [AIMessage(content=value["drink"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("remember", remember)
    builder.add_edge(START, "remember")
    builder.add_edge("remember", END)
    config = RunnableConfig(configurable={"thread_id": first_thread, "user_id": user_id})

    try:
        async with initialize_database() as saver, initialize_store() as store:
            assert isinstance(saver, AsyncPostgresSaver)
            assert isinstance(store, AsyncPostgresStore)
            graph = builder.compile(checkpointer=saver, store=store)
            await graph.ainvoke({"messages": [HumanMessage(content="tea")]}, config)

        # Direct SQL proves that both kinds of memory reached the same PG database.
        async with await psycopg.AsyncConnection.connect(get_postgres_connection_string()) as conn:
            cursor = await conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (first_thread,)
            )
            row = await cursor.fetchone()
            assert row is not None and row[0] > 0
            cursor = await conn.execute(
                "SELECT value FROM store WHERE prefix = %s AND key = %s",
                (user_id, "preference"),
            )
            assert await cursor.fetchone() == ({"drink": "tea"},)

        async with initialize_database() as saver, initialize_store() as store:
            graph = builder.compile(checkpointer=saver, store=store)
            state = await graph.aget_state(config)
            assert [message.content for message in state.values["messages"]] == ["tea", "tea"]
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="What do I drink?")]},
                RunnableConfig(configurable={"thread_id": second_thread, "user_id": user_id}),
            )
            assert result["messages"][-1].content == "tea"
    finally:
        async with initialize_database() as saver, initialize_store() as store:
            await saver.adelete_thread(first_thread)
            await saver.adelete_thread(second_thread)
            await store.adelete((user_id,), "preference")
