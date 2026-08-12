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
        def update_agent(self, agent: str, verify: bool = True)->None:
            if verify:
                if not self.info:
                    self.retrieve_info()
                agent_keys=[a.key for a in self.info.agents]
                if agent not in agent_keys:
                    raise AgentClientError(
                        f"Agent {agent} not found in available agents: {', '.join(agent_keys)}"
                    )
            self.agent = agent
