import asyncio

from client import AgentClient
from core import settings
from schema import ChatMessage


async def amain() -> None:
    #### ASYNC ####

    client = AgentClient(settings.BASE_URL)

    print("Agent info:")
    print(client.info)
    print("\n")

    print("Chat example:")
    print("\n")

    response = await client.ainvoke("给我讲一个笑话", model="qwen-plus")
    response.pretty_print()

    print("流式示例:")
    print("\n")

    async for message in client.astream("分享一个简短的趣闻？"):
        if isinstance(message, str):
            print(message, flush=True, end="")
        elif isinstance(message, ChatMessage):
            print("\n", flush=True)
            message.pretty_print()
        else:
            print(f"ERROR: Unknown type - {type(message)}")


def main() -> None:
    #### SYNC  ####
    client = AgentClient(settings.BASE_URL)

    print("Agent info:")
    print("\n")
    print(client.info)

    print("聊天示例:\n")
    response = client.invoke("讲个简短的笑话?", model="qwen-plus")
    response.pretty_print()

    print("流式示例:")
    print("\n")

    for message in client.stream("分享一个简短的趣闻？"):
        if isinstance(message, str):
            print(message, flush=True, end="")
        elif isinstance(message, ChatMessage):
            print("\n", flush=True)
            message.pretty_print()
        else:
            print(f"ERROR: Unknown type - {type(message)}")


if __name__ == "__main__":
    print("同步模式:")
    main()
    print("\n\n\n\n\n")
    print("异步模式:")
    print("\n")
    asyncio.run(amain())
