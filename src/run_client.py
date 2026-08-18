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

    response = await  client.ainvoke("给我讲一个笑话", model="qwen-plus")
    response.pretty_print()

    print("流式示例:")
    print("\n")

    async  for message in client.astream("分享一个简短的趣闻？"):
        if isinstance(message,str):
            print(message,flush=True,end="")
        elif isinstance(message,ChatMessage):
            print("\n",flush=True)
            message.pretty_print()
        else:
            print(f"ERROR: Unknown type - {type(message)}")



