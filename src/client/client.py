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
    """用于与智能体服务交互的客户端。"""

    def __init__(
        self,
        base_url: str = "http://0.0.0.0",
        agent: str | None = None,
        timeout: float | None = None,
        get_info: bool = True,
    ) -> None:
        """
        初始化客户端。

        参数：
            base_url (str)：智能体服务的基础 URL。
            agent (str)：默认使用的智能体名称。
            timeout (float，可选)：请求超时时间。
            get_info (bool，可选)：初始化时是否获取智能体信息。
                默认值：True
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
            raise AgentClientError(f"获取服务信息时出错：{e}")

        self.info = ServiceMetadata.model_validate(response.json())
        if not self.agent or self.agent not in [a.key for a in self.info.agents]:
            self.agent = self.info.default_agent

    def update_agent(self, agent: str, verify: bool = True) -> None:
        if verify:
            if not self.info:
                self.retrieve_info()
            agent_keys = [a.key for a in self.info.agents]  # type: ignore[union-attr]
            if agent not in agent_keys:
                raise AgentClientError(f"未在可用智能体中找到 {agent}：{', '.join(agent_keys)}")
        self.agent = agent

    async def ainvoke(
        self,
        message: str,
        model: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        agent_config: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """
        异步调用智能体，仅返回最终消息。

        参数：
            message (str)：发送给智能体的消息
            model (str，可选)：智能体使用的 LLM 模型
            thread_id (str，可选)：用于继续对话的线程 ID
            user_id (str，可选)：用于跨多个线程继续对话的用户 ID
            agent_config (dict[str, Any]，可选)：传递给智能体的其他配置

        返回：
            AnyMessage：智能体返回的响应
        """
        if not self.agent:
            raise AgentClientError("未选择智能体。请使用 update_agent() 选择智能体。")
        request = UserInput(message=message)
        if thread_id:
            request.thread_id = thread_id
        if model:
            request.model = model  # type: ignore[assignment]
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
                raise AgentClientError(f"错误：{e}")

        return ChatMessage.model_validate(response.json())

    def invoke(
        self,
        message: str,
        model: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        agent_config: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """
        同步调用智能体，仅返回最终消息。

        参数：
            message (str)：发送给智能体的消息
            model (str，可选)：智能体使用的 LLM 模型
            thread_id (str，可选)：用于继续对话的线程 ID
            user_id (str，可选)：用于跨多个线程继续对话的用户 ID
            agent_config (dict[str, Any]，可选)：传递给智能体的其他配置

        返回：
            ChatMessage：智能体返回的响应
        """
        if not self.agent:
            raise AgentClientError("未选择智能体。请使用 update_agent() 选择智能体。")
        request = UserInput(message=message)
        if thread_id:
            request.thread_id = thread_id
        if model:
            request.model = model  # type: ignore[assignment]
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
            raise AgentClientError(f"错误：{e}")

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
                raise Exception(f"解析服务器消息的 JSON 时出错：{e}")
            match parsed["type"]:
                case "message":
                    # 将 JSON 格式的消息转换为 AnyMessage
                    try:
                        return ChatMessage.model_validate(parsed["content"])
                    except Exception as e:
                        raise Exception(f"服务器返回了无效消息：{e}")
                case "token":
                    # 直接生成字符串令牌
                    return parsed["content"]
                case "error":
                    error_msg = "错误：" + parsed["content"]
                    return ChatMessage(type="ai", content=error_msg)
        return None

    def stream(
        self,
        message: str,
        model: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        agent_config: dict[str, Any] | None = None,
        stream_tokens: bool = True,
    ) -> Generator[ChatMessage | str, None, None]:
        """
        同步流式返回智能体的响应。

        智能体处理过程中的每条中间消息都以 ChatMessage 形式生成。
        如果 stream_tokens 为 True（默认值），响应还会在流式模型生成内容令牌时
        逐个生成这些令牌。

        参数：
            message (str)：发送给智能体的消息
            model (str，可选)：智能体使用的 LLM 模型
            thread_id (str，可选)：用于继续对话的线程 ID
            user_id (str，可选)：用于跨多个线程继续对话的用户 ID
            agent_config (dict[str, Any]，可选)：传递给智能体的其他配置
            stream_tokens (bool，可选)：是否在令牌生成时进行流式传输
                默认值：True

        返回：
            Generator[ChatMessage | str, None, None]：智能体返回的响应
        """
        if not self.agent:
            raise AgentClientError("未选择智能体。请使用 update_agent() 选择智能体。")
        request = StreamInput(message=message, stream_tokens=stream_tokens)
        if thread_id:
            request.thread_id = thread_id
        if user_id:
            request.user_id = user_id
        if model:
            request.model = model  # type: ignore[assignment]
        if agent_config:
            request.agent_config = agent_config
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/{self.agent}/stream",
                json=request.model_dump(),
                headers=self._headers,
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.strip():
                        parsed = self._parse_stream_line(line)
                        if parsed is None:
                            break
                        yield parsed
        except httpx.HTTPError as e:
            raise AgentClientError(f"错误：{e}")

    async def astream(
        self,
        message: str,
        model: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        agent_config: dict[str, Any] | None = None,
        stream_tokens: bool = True,
    ) -> AsyncGenerator[ChatMessage | str, None]:
        """
        异步流式返回智能体的响应。

        智能体处理过程中的每条中间消息都以 AnyMessage 形式生成。
        如果 stream_tokens 为 True（默认值），响应还会在流式模型生成内容令牌时
        逐个生成这些令牌。

        参数：
            message (str)：发送给智能体的消息
            model (str，可选)：智能体使用的 LLM 模型
            thread_id (str，可选)：用于继续对话的线程 ID
            user_id (str，可选)：用于跨多个线程继续对话的用户 ID
            agent_config (dict[str, Any]，可选)：传递给智能体的其他配置
            stream_tokens (bool，可选)：是否在令牌生成时进行流式传输
                默认值：True

        返回：
            AsyncGenerator[ChatMessage | str, None]：智能体返回的响应
        """
        if not self.agent:
            raise AgentClientError("未选择智能体。请使用 update_agent() 选择智能体。")
        request = StreamInput(message=message, stream_tokens=stream_tokens)
        if thread_id:
            request.thread_id = thread_id
        if model:
            request.model = model  # type: ignore[assignment]
        if agent_config:
            request.agent_config = agent_config
        if user_id:
            request.user_id = user_id
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/{self.agent}/stream",
                    json=request.model_dump(),
                    headers=self._headers,
                    timeout=self.timeout,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            parsed = self._parse_stream_line(line)
                            if parsed is None:
                                break
                            # 不生成空字符串令牌，以免引发生成器问题
                            if parsed != "":
                                yield parsed
            except httpx.HTTPError as e:
                raise AgentClientError(f"错误：{e}")

    async def acreate_feedback(
        self, run_id: str, key: str, score: float, kwargs: dict[str, Any] = {}
    ) -> None:
        """
        为一次运行创建反馈记录。

        这是对 LangSmith create_feedback API 的简单封装，以便凭据存储并管理在
        服务端，而不是客户端。
        参见：https://api.smith.langchain.com/redoc#tag/feedback/operation/create_feedback_api_v1_feedback_post
        """
        request = Feedback(run_id=run_id, key=key, score=score, kwargs=kwargs)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/feedback",
                    json=request.model_dump(),
                    headers=self._headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response.json()
            except httpx.HTTPError as e:
                raise AgentClientError(f"错误：{e}")

    def get_history(self, thread_id: str, agent: str | None = None) -> ChatHistory:
        """
        获取聊天历史记录。

        参数：
            thread_id (str，可选)：用于标识对话的线程 ID
            agent (str，可选)：使用其图来解析该线程的智能体。
        """
        agent = agent or self.agent
        request = ChatHistoryInput(thread_id=thread_id)
        url = f"{self.base_url}/{agent}/history" if agent else f"{self.base_url}/history"
        try:
            response = httpx.post(
                url,
                json=request.model_dump(),
                headers=self._headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise AgentClientError(f"错误：{e}")

        return ChatHistory.model_validate(response.json())

    def _user_threads_request(
        self, user_id: str, agent: str | None, limit: int
    ) -> tuple[str, dict[str, Any]]:
        agent_id = agent or self.agent
        url = f"{self.base_url}/{agent_id}/threads" if agent_id else f"{self.base_url}/threads"
        return url, UserThreadsInput(user_id=user_id, limit=limit).model_dump()

    def get_user_threads(
        self, user_id: str, agent: str | None = None, limit: int = 20
    ) -> UserThreads:
        """
        列出用户的对话线程。

        参数：
            user_id (str)：要列出其线程的用户 ID。
            agent (str，可选)：要列出其线程的智能体。
            limit (int，可选)：返回的最大线程数。
        """
        url, params = self._user_threads_request(user_id, agent, limit)
        try:
            response = httpx.get(
                url,
                params=params,
                headers=self._headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise AgentClientError(f"错误：{e}")

        return UserThreads.model_validate(response.json())

    async def aget_user_threads(
        self, user_id: str, agent: str | None = None, limit: int = 20
    ) -> UserThreads:
        """
        异步列出用户的对话线程。

        参数：
            user_id (str)：要列出其线程的用户 ID。
            agent (str，可选)：要列出其线程的智能体。
            limit (int，可选)：返回的最大线程数。
        """
        url, params = self._user_threads_request(user_id, agent, limit)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                raise AgentClientError(f"错误：{e}")

        return UserThreads.model_validate(response.json())
