"""支持异步初始化和动态图创建的智能体类型"""

from abc import ABC, abstractmethod

from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel


class LazyLoadingAgent(ABC):
    """“需要异步加载的智能体基类"""

    def __init__(self) -> None:
        """初始化 Agent"""
        self._loaded = False
        self._graph: CompiledStateGraph | Pregel | None = None

    @abstractmethod
    async def load(self) -> None:
        """
        为该智能体执行异步加载。

        此方法会在服务启动期间调用，并应负责：
        - 建立外部连接（MCP 客户端、数据库等）
        - 加载工具或资源
        - 执行其他必要的异步初始化操作
        - 创建该智能体的图
        """
        raise NotImplementedError  # pragma: no cover

    # 获取图
    def get_graph(self) -> CompiledStateGraph | Pregel:
        """
        获取智能体的图。

        返回在 load() 期间创建的图实例。

        返回：
        智能体的图（CompiledStateGraph 或 Pregel）
        """
        if not self._loaded:
            raise RuntimeError("智能体尚未加载。请先调用 load()")
        if self._graph is None:
            raise RuntimeError("图尚未创建。请检查 load() 是否正确执行")
        return self._graph
