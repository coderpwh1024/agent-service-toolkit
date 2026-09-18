# AG-UI 协议支持

该服务通过 [AG-UI 协议](https://docs.ag-ui.com) 对外提供所有智能体。AG-UI 是一种开放的、
基于事件的标准，用于将智能体连接到面向用户的应用，[CopilotKit](https://docs.copilotkit.ai)
及越来越多的框架都在使用它。这样，你既可以为智能体构建生产级 React/Next.js 前端，
又可以保留 Streamlit 应用用于开发。

繁重的工作（将 LangGraph 执行过程转换为 AG-UI 事件）由官方
[`ag-ui-langgraph`](https://pypi.org/project/ag-ui-langgraph/) 包完成。该服务只增加了一个
轻量适配器（`src/service/agui.py`），将其接入智能体注册表、Bearer 身份验证和 Langfuse
追踪。

## 端点

| 端点 | 说明 |
| --- | --- |
| `POST /agui/{agent_id}/run` | 运行智能体，并通过 SSE 流式传输 AG-UI 事件 |
| `POST /agui/run` | 使用默认智能体执行相同操作 |

请求体采用标准 AG-UI `RunAgentInput`。该端点与 API 的其余部分使用相同的 Bearer
身份验证，可使用可信服务的 `AUTH_SECRET`，也可使用 `/auth/token` 签发的短期用户令牌。

## 连接前端

推荐的生产环境部署方式是标准 CopilotKit 架构：前端与
[CopilotKit 运行时](https://docs.copilotkit.ai)（例如 Next.js API 路由）通信，运行时再使用
AG-UI `HttpAgent` 从服务端连接此服务：

```ts
import { HttpAgent } from "@ag-ui/client";

const agent = new HttpAgent({
  url: "http://your-service:8080/agui/research-assistant/run",
  headers: { Authorization: `Bearer ${process.env.AUTH_SECRET}` },
});
```

运行时保存 Bearer 令牌，并充当浏览器与智能体服务之间的可信层，其角色与 Streamlit
应用之于原生 API 相同。实验时可以让浏览器直接访问该端点，但你需要自行向服务添加
CORS 中间件，而且 `AUTH_SECRET` 会暴露给浏览器，因此应优先采用运行时模式。

## 尝试运行

项目包含一个使用官方 SDK 的最小参考客户端：

```sh
# 在一个终端中启动服务（也可以使用 docker compose watch）
python src/run_service.py

# 在另一个终端中运行
cd scripts/agui-client
npm install
node client.mjs "给我讲个笑话！" chatbot
```

使用 `THREAD_ID` 继续对话，并按需设置 `AUTH_SECRET` / `AGENT_URL`：

```sh
THREAD_ID=my-thread node client.mjs "再讲一个" chatbot
```

## 行为说明

- **线程与原生 API 共享。** 两种协议使用同一个按线程 ID 索引的检查点存储器，因此在
  `/stream` 上开始的对话可以通过 AG-UI 继续，反之亦然。需要注意：消息会按 ID 去重，
  因此不要使用新生成的 ID 重放历史消息（规范实现的 AG-UI 客户端会保留 ID，因此没有问题）。
- **按请求配置**应放在 `forwardedProps.configurable` 中，这是 AG-UI 中与原生 API 的
  `model` / `user_id` / `agent_config` 字段对应的配置，例如
  `{"forwardedProps": {"configurable": {"model": "gpt-5.2"}}}`。协议管理的键
  （`thread_id`、`checkpoint_id`、`checkpoint_ns`）会被拒绝。`model` 会像原生
  API 一样根据 `AVAILABLE_MODELS` 进行检查（不允许时返回 400）。
- **中断**（人在回路）会以名为 `on_interrupt` 的 `CUSTOM` 事件呈现。要恢复执行，
  请使用同一线程再次运行，并传入
  `{"forwardedProps": {"command": {"resume": <answer>}}}`。
- **客户端可以看到图状态。** AG-UI 的共享状态功能会发送包含完整图状态的
  `STATE_SNAPSHOT` 事件，因此不要把密钥或仅限内部使用的数据放入智能体状态。
- 适配器会过滤掉 **`RAW` 透传事件**。标准 AG-UI 客户端会忽略这些事件，而且它们可能向
  调用方暴露服务端内部信息（包括完整渲染后的提示词）。如果需要在可信层后方使用完整事件流
  进行调试（例如 AG-UI Event Inspector），请移除 `src/service/agui.py` 中的过滤器。
- **`/feedback` 和 `/history` 未进行桥接。** AG-UI `runId` 由客户端生成，不会用作
  LangSmith 运行 ID，因此星级反馈不适用于 AG-UI 运行。AG-UI 客户端会根据事件流自行管理
  消息历史。
- `ag-ui-langgraph` 包尚未达到 1.0 版本，因此已固定到相应版本。如果未来 LangGraph
  的某个主版本与它发生冲突，应移除此集成或让它延后升级，而不应因此阻碍核心依赖升级。
