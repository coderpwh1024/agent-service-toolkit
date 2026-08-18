import json
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import httpx

from schema import (
    ChatHistory,
    ChatHistoryInput,
    ChatMessage,
    Feedback,
    ServiceMetadata,
    StreamInput,
    UserInput,
    UserThreads,
    UserThreadsInput,
)


class AgentClientError(Exception):
    pass


class AgentClient:
    """用于与智能体服务交互的客户端"""

    def __init__(self,
                 base_url: str = "http://0.0.0.0",
                 agent: str | None = None,
                 timeout: float | None = None,
                 get_info: bool = True,
                 ) -> None:
        """
        初始化客户端

        参数:
          base_url (str): 智能体服务的基础 URL。
          agent (str): 默认使用的智能体名称。
          timeout (float, optional): 请求超时时间。
          get_info (bool, optional): 初始化时是否获取智能体信息。
              默认值：True。
        """
        self.base_url = base_url
        self.auth_secret = os.getenv("AUTH_SECRET")
        self.timeout = timeout
        self.info: ServiceMetadata | None = None
        self.agent: str | None = None
        if get_info:
            self.retrieve_info()
        if agent:
            self.update_agent(agent)

        @property
        def _headers(self) -> dict[str, str]:
            headers = {}
            if self.auth_secret:
                headers["Authorization"] = f"Bearer {self.auth_secret}"
            return headers

        def retrieve_info(self) -> None:
            try:
                response = httpx.get(
                    f"{self.base_url}/info",
                    headers=self._headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                raise AgentClientError(f"Error getting service info: {e}")

        # 更新智能体
        def update_agent(self, agent: str, verify: bool = True) -> None:
            if verify:
                if not self.info:
                    self.retrieve_info()
                agent_keys = [a.key for a in self.info.agents]
                if agent not in agent_keys:
                    raise AgentClientError(
                        f"Agent {agent} not found in available agents: {', '.join(agent_keys)}"
                    )
            self.agent = agent

        async def ainvoke(
                self,
                messages: str,
                model: str | None = None,
                thread_id: str | None = None,
                user_id: str | None = None,
                agent_config: dict[str, Any] | None = None,
        ) -> ChatMessage:
            """
            异步调用智能体，仅返回最终消息。

            参数:
                message (str): 发送给智能体的消息
                model (str, optional): 智能体使用的 LLM 模型
                thread_id (str, optional): 用于继续对话的线程 ID
                user_id (str, optional): 用于跨多个线程继续对话的用户 ID
                agent_config (dict[str, Any], optional): 传递给智能体的其他配置

            返回:
               AnyMessage: 智能体返回的响应
            """
            if not self.anget:
                raise AgentClientError("No agent selected. Use update_agent() to select an agent.")
            request = UserInput(messages=messages)
            if thread_id:
                request.thread_id = thread_id
            if model:
                request.model = model
            if agent_config:
                request.agent_config = agent_config
            if user_id:
                request.user_id = user_id

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/{self.agent}/invoke",
                        json=request.model_dump(),
                        headers=self._headers,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                except httpx.HTTPError as e:
                    raise AgentClientError(response.json())
            return ChatMessage.model_validate(response.json())

        def invoke(
                self,
                messages: str,
                model: str | None = None,
                thread_id: str | None = None,
                user_id: str | None = None,
                agent_config: dict[str, Any] | None = None,

        ) -> ChatMessage:
            """
            同步调用智能体。仅返回最终消息。

            参数：
               message (str)：发送给智能体的消息
               model (str，可选)：智能体使用的 LLM 模型
               thread_id (str，可选)：用于继续对话的线程 ID
               user_id (str，可选)：用于跨多个线程继续对话的用户 ID
               agent_config (dict[str, Any]，可选)：传递给智能体的其他配置
            返回：
               ChatMessage：智能体的响应
            """
            if not self.agent:
                raise AgentClientError("No agent selected. Use update_agent() to select an agent.")
            request = UserInput(message=messages)
            if thread_id:
                request.thread_id = thread_id
            if model:
                request.model = model
            if agent_config:
                request.agent_config = agent_config
            if user_id:
                request.user_id = user_id
            try:
                response = httpx.post(
                    f"{self.base_url}/{self.agent}/invoke",
                    json=request.model_dump(),
                    headers=self._headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                raise AgentClientError(f"Error:{e}")
            return ChatMessage.model_validate(response.json())

        def _parse_stream_line(self, line: str) -> ChatMessage | str | None:
            line = line.strip()
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    return None
                try:
                    parsed = json.loads(data)
                except Exception as e:
                    raise Exception(f"Server returned invalid message:{e}")
                match parsed["type"]:
                    case "message":
                        try:
                            return ChatMessage.model_validate(parsed["content"])
                        except Exception as e:
                            raise Exception(f"Server returned invalid message: {e}")
                    case "token":
                        return parsed["content"]
                    case "error":
                        error_msg = "Error:" + parsed["content"]
                        return ChatMessage(type="ai", content=error_msg)

            return None

        def stream(self
                   , message: str,
                   model: str, thread_id: str | None = None,
                   user_id: str | None = None,
                   agent_config: dict[str, Any] | None = None,
                   stream_tokens: bool = True
                   ) -> Generator[ChatMessage | str, None, None]:
                    """
                    同步地以流式方式返回智能体的响应。

                    智能体处理过程中的每一条中间消息，都会以 ChatMessage 的形式返回。
                    如果 stream_tokens 为 True（默认值），还会在流式模型生成内容时，
                    逐个返回生成的内容片段（token）。

                    参数：
                    message (str)：发送给智能体的消息。
                    model (str，可选)：智能体使用的大语言模型。
                    thread_id (str，可选)：用于继续同一段对话的线程 ID。
                    user_id (str，可选)：用户 ID，用于跨多个线程延续同一用户的对话。
                    agent_config (dict[str, Any]，可选)：传递给智能体的额外配置。
                    stream_tokens (bool，可选)：是否在内容生成过程中流式返回 token。
                    默认值：True。

                    返回：
                    Generator[ChatMessage | str, None, None]：
                    一个生成器，依次生成 ChatMessage 或字符串。
                    """