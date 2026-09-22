# 🧰 AI 智能体服务工具包

[![构建状态](https://github.com/JoshuaC215/agent-service-toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/JoshuaC215/agent-service-toolkit/actions/workflows/test.yml) [![codecov](https://codecov.io/github/JoshuaC215/agent-service-toolkit/graph/badge.svg?token=5MTJSYWD05)](https://codecov.io/github/JoshuaC215/agent-service-toolkit) [![Python 版本](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FJoshuaC215%2Fagent-service-toolkit%2Frefs%2Fheads%2Fmain%2Fpyproject.toml)](https://github.com/JoshuaC215/agent-service-toolkit/blob/main/pyproject.toml)
[![GitHub 许可证](https://img.shields.io/github/license/JoshuaC215/agent-service-toolkit)](https://github.com/JoshuaC215/agent-service-toolkit/blob/main/LICENSE) [![Streamlit 应用](https://static.streamlit.io/badges/streamlit_badge_black_red.svg)](https://agent-service-toolkit.streamlit.app/)

一个用于运行 AI 智能体服务的完整工具包，基于 LangGraph、FastAPI 和 Streamlit 构建。

它包含一个 [LangGraph](https://langchain-ai.github.io/langgraph/) 智能体、一个用于提供智能体服务的 [FastAPI](https://fastapi.tiangolo.com/) 服务、一个与该服务交互的客户端，以及一个使用该客户端提供聊天界面的 [Streamlit](https://streamlit.io/) 应用。数据结构和设置均使用 [Pydantic](https://github.com/pydantic/pydantic) 构建。

本项目提供了一个模板，帮助你使用 LangGraph 框架轻松构建并运行自己的智能体。它展示了从智能体定义到用户界面的完整配置，并通过一套完善、可靠的工具包，帮助你更轻松地开始开发基于 LangGraph 的项目。

**[🎥 观看代码仓库和应用的视频演示](https://www.youtube.com/watch?v=pdYVHw_YCNY)**

## 概览

### [体验应用！](https://agent-service-toolkit.streamlit.app/)

<a href="https://agent-service-toolkit.streamlit.app/"><img src="media/app_screenshot.png" width="600" alt="应用截图"></a>

### 快速开始

直接使用 Python 运行

```sh
# 默认使用阿里云百炼千问 3.8 Max，并使用 PostgreSQL 持久化记忆与 RAG。
# .env 只配置 Nacos 连接；应用配置维护在 Nacos 配置中心。

# 推荐使用 uv 安装 agent-service-toolkit，但也可以使用 "pip install ."
# 有关 uv 的安装方式，请参阅：https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/0.11.32/install.sh | sh

# 安装依赖。"uv sync" 会自动创建 .venv
uv sync --frozen
source .venv/bin/activate
python src/run_service.py

# 在另一个终端中
source .venv/bin/activate
streamlit run src/streamlit_app.py
```

使用 Docker 运行

```sh
# 先维护 Nacos 中的 agent-service-toolkit.yaml，再配置 .env 中的 Nacos 连接。
docker compose watch
```

### 架构图

<img src="media/agent_architecture.png" width="600" alt="智能体架构图">

### 主要特性

1. **LangGraph 智能体及最新特性**：使用 LangGraph 框架构建的可自定义智能体。实现了 LangGraph v1.0 的最新特性，包括使用 `interrupt()` 实现人在回路、使用 `Command` 实现流程控制、使用 `Store` 实现长期记忆，以及 `langgraph-supervisor`。
1. **FastAPI 服务**：通过流式和非流式端点提供智能体服务。
1. **高级流式传输**：采用一种新颖的方法，同时支持基于 token 和基于消息的流式传输。
1. **AG-UI 协议支持**：每个智能体也会通过 [AG-UI 协议](https://docs.ag-ui.com) 提供服务，以连接 CopilotKit 等兼容 AG-UI 的前端——请参阅[文档](docs/AGUI.md)。
1. **移动端实时语音 API**：提供百炼实时 ASR、Agent、流式 TTS、插话取消、播放进度和业务确认协议，可由独立 Flutter App 连接——请参阅[接口文档](docs/Voice_API.md)和[整体规划](docs/Voice_App_Plan.md)。
1. **Streamlit 界面**：提供用户友好的聊天界面，并支持基于阿里云百炼的语音输入和输出；两者复用 `DASHSCOPE_API_KEY`。
1. **多智能体支持**：在服务中运行多个智能体，并通过 URL 路径调用。可用的智能体和模型在 `/info` 中说明。
1. **异步设计**：使用 async/await 高效处理并发请求。
1. **内容审核**：使用 Safeguard 实现内容审核（需要 Groq API 密钥）。
1. **RAG 智能体**：使用 PostgreSQL 和阿里云百炼 Embedding 实现的 RAG 智能体——请参阅[文档](docs/RAG_Assistant.md)。
1. **聊天记录与长期记忆**：使用 PostgreSQL 保存会话检查点和跨会话记忆，并通过 `/threads` 按智能体列出用户之前的对话。
1. **反馈机制**：包含一个与 LangSmith 集成的星级反馈系统。
1. **Docker 支持**：包含 Dockerfile 和 Docker Compose 文件，便于开发和部署。
1. **Nacos 3.x 集成**：支持启动配置加载、服务注册与发现，并在服务关停时自动注销实例。
1. **测试**：为整个代码仓库提供完善的单元测试和集成测试。

### 关键文件

代码仓库的结构如下：

- `src/agents/`：定义多个具有不同能力的智能体
- `src/schema/`：定义协议 schema
- `src/core/`：核心模块，包括 LLM 定义和设置
- `src/service/service.py`：用于提供智能体服务的 FastAPI 服务
- `src/client/client.py`：与智能体服务交互的客户端
- `src/streamlit_app.py`：提供聊天界面的 Streamlit 应用
- `tests/`：单元测试和集成测试

## 设置与使用

1. 克隆代码仓库：

   ```sh
   git clone https://github.com/JoshuaC215/agent-service-toolkit.git
   cd agent-service-toolkit
   ```

2. 配置 Nacos：
   在 Nacos 配置中心创建或维护 Data ID `agent-service-toolkit.yaml`（类型为 `YAML`）。`.env` 仅保存连接 Nacos 所需的引导配置，可从 [`.env.example`](./.env.example) 复制。其他服务账号及认证边界见[账号、凭据与用户身份梳理](docs/Accounts_and_Credentials.md)。

3. 现在，你可以使用 Docker 或仅使用 Python，在本地运行智能体服务和 Streamlit 应用。推荐使用 Docker，以简化环境配置，并在代码发生更改时立即重新加载服务。

### 特定 AI 提供商的额外设置

- [设置阿里云百炼千问](docs/Alibaba_Bailian.md)
- [移动端实时语音 API](docs/Voice_API.md)
- [设置 Ollama](docs/Ollama.md)
- [设置 VertexAI](docs/VertexAI.md)
- [使用 PostgreSQL 设置 RAG 与记忆库](docs/RAG_Assistant.md)

### 构建或自定义你自己的智能体

要根据自己的使用场景自定义智能体：

1. 将新智能体添加到 `src/agents` 目录。你可以复制 `research_assistant.py` 或 `chatbot.py`，然后修改其行为和工具。
1. 导入新智能体，并将其添加到 `src/agents/agents.py` 中的 `agents` 字典。可通过 `/<your_agent_name>/invoke` 或 `/<your_agent_name>/stream` 调用你的智能体。
1. 调整 `src/streamlit_app.py` 中的 Streamlit 界面，使其与智能体的能力相匹配。

### 处理私有凭据文件

如果你的智能体或选用的 LLM 需要基于文件的凭据或证书，项目提供了 `privatecredentials/` 目录，方便你进行开发。除 `.gitkeep` 文件外，其中的所有内容都会被 Git 和 Docker 构建过程忽略。有关建议用法，请参阅[使用基于文件的凭据](docs/File_Based_Credentials.md)。

### Docker 设置

本项目包含 Docker 配置，便于开发和部署。`compose.yaml` 文件定义了四个服务：`postgres`、`redis`、`agent_service` 和 `streamlit_app`。Redis 用于邮箱验证码有效期与发送频率限制；每个应用服务的 `Dockerfile` 位于对应目录中。

对于本地开发，建议使用 [docker compose watch](https://docs.docker.com/compose/file-watch/)。该功能会在检测到源代码更改时自动更新容器，从而提供更顺畅的开发体验。

1. 确保系统中已安装 Docker 和 Docker Compose（>= [v2.24.0](https://docs.docker.com/compose/release-notes/#2240)）。

2. 配置好 Nacos 中的 `agent-service-toolkit.yaml` 后，根据 `.env.example` 创建只包含 Nacos 引导配置的 `.env`：

   ```sh
   cp .env.example .env
   # 编辑 .env，填写 Nacos 地址和认证信息
   ```

3. 以监视模式构建并启动服务：

   ```sh
   docker compose watch
   ```

   这将自动执行以下操作：
   - 启动智能体服务所连接的 PostgreSQL 和 Redis 服务
   - 启动使用 FastAPI 的智能体服务
   - 启动提供用户界面的 Streamlit 应用

4. 现在，当你修改代码时，服务将自动更新：
   - 对相关 Python 文件和目录的修改会触发对应服务的更新。
   - 注意：如果修改了 `pyproject.toml` 或 `uv.lock` 文件，则需要运行 `docker compose up --build` 重新构建服务。

5. 在 Web 浏览器中访问 `http://localhost:8501`，即可打开 Streamlit 应用。

6. 智能体服务 API 可通过 `http://0.0.0.0:8080` 访问。也可以通过 `http://0.0.0.0:8080/redoc` 查看 OpenAPI 文档。

7. 使用 `docker compose down` 停止服务。

借助此配置，你可以实时开发和测试更改，无需手动重启服务。

### 基于 AgentClient 构建其他应用

此代码仓库包含一个通用的 `src/client/client.AgentClient`，可用于与智能体服务交互。该客户端设计灵活，可用于在智能体之上构建其他应用。它同时支持同步和异步调用，以及流式和非流式请求。

除流式和事件协议外，HTTP 业务接口统一返回 `code`、`message`、`data`：

```json
{
  "code": 200,
  "message": "success",
  "data": {}
}
```

HTTP 状态码与响应体中的 `code` 保持一致。成功默认使用 200；未处理异常默认使用 500；参数校验、认证、限流等已知异常保留各自的 HTTP 状态码。`/stream`、`/{agent_id}/stream` 使用 SSE，`/agui/*` 使用 AG-UI 协议，`/voice/sessions/{session_id}/ws` 使用 WebSocket 事件协议，因此不套用该 JSON 封装。FastAPI 自带的 `/docs`、`/redoc` 和 `/openapi.json` 也保持框架原有格式。

有关如何使用 `AgentClient` 的完整示例，请参阅 `src/run_client.py` 文件。下面是一个简短示例：

```python
from client import AgentClient
client = AgentClient()

response = client.invoke("Tell me a brief joke?")
response.pretty_print()
# ================================== Ai Message ==================================
#
# A man walked into a library and asked the librarian, "Do you have any books on Pavlov's dogs and Schrödinger's cat?"
# The librarian replied, "It rings a bell, but I'm not sure if it's here or not."

```

### 不使用 Docker 进行本地开发

你也可以使用 Python 虚拟环境，在本地运行智能体服务和 Streamlit 应用。所有服务启动方式都使用 PostgreSQL 保存短期记忆和长期记忆；不再支持 SQLite、MongoDB 或内存回退。直接运行 `langgraph dev` 使用独立的开发运行时，不经过本项目的存储初始化，因此不作为本项目支持的服务启动方式。

1. 创建虚拟环境并安装依赖：

   ```sh
   uv sync --frozen
   source .venv/bin/activate
   ```

2. 准备 PostgreSQL。可以连接已有数据库，也可以只启动项目的数据库容器：

   ```sh
   docker compose up -d postgres
   ```

   当前 Nacos 部署的 Redis/PostgreSQL 连接信息只在配置中心维护。切换数据库时应更新 Nacos YAML 配置并提前创建数据库，不要在 `.env` 中重复设置连接字段。只有显式禁用 Nacos 时，应用才会使用代码中的本地开发默认值。

3. 运行 FastAPI 服务器：

   ```sh
   python src/run_service.py
   ```

4. 在单独的终端中运行 Streamlit 应用：

   ```sh
   streamlit run src/streamlit_app.py
   ```

5. 打开浏览器，访问 Streamlit 提供的 URL（通常为 `http://localhost:8501`）。

服务会自动创建检查点和长期记忆所需的表；PG 不可用时启动失败，不会回退到本地文件或内存。已有 `checkpoints.db` 不会自动迁移或删除。长期记忆仍由智能体通过 Store 显式写入。

### Nacos 3.x

项目使用官方 `nacos-sdk-python` 连接 Nacos 3.x。Nacos 3 默认将控制台和客户端服务分开：控制台可位于 `http://127.0.0.1:8080/`，应用 SDK 应连接服务器端口 `127.0.0.1:8848`，并确保对应的 gRPC 端口 `9848` 可访问。

在 `.env` 中启用本地 Nacos。由于本项目也默认监听 `8080`，Nacos 控制台已经占用该端口时，需要同时为应用设置其他端口：

```dotenv
NACOS_ENABLED=true
NACOS_SERVER_ADDR=127.0.0.1:8848
NACOS_USERNAME=nacos
NACOS_PASSWORD=replace-with-nacos-password
NACOS_CONFIG_DATA_ID=agent-service-toolkit.yaml
NACOS_STORAGE_CONFIG_REQUIRED=true
NACOS_SERVICE_NAME=agent-service-toolkit
NACOS_SERVICE_IP=127.0.0.1
PORT=8000
```

使用 Docker Compose 运行应用、Nacos 运行在宿主机时，将 `NACOS_SERVER_ADDR` 改为 `host.docker.internal:8848`，并将 `NACOS_SERVICE_IP` 设置为调用方可以访问的地址。`NACOS_NAMESPACE_ID` 使用命名空间 ID（留空代表 public），`NACOS_GROUP_NAME` 默认为 `DEFAULT_GROUP`。

已创建的存储配置使用 `public` 命名空间、`DEFAULT_GROUP` 分组和 `agent-service-toolkit.yaml` Data ID，配置类型为 `YAML`。配置直接在 Nacos 配置中心维护，并采用按功能分类的多级结构，例如：

```yaml
models:
  dashscope:
    api_key: replace-with-bailian-api-key

storage:
  redis:
    url: redis://redis-host:6379/0
  postgres:
    user: database-user
    password: database-password
    host: postgres-host
    port: 5432
    database: agent_service
  qiniu:
    ak: xxx
    sk: xxx
    bucket_name: agent-service-toolkit-avatars
    public_base_url: https://cdn.example.com
    upload_token_ttl_seconds: 3600
    avatar_max_bytes: 5242880

voice:
  enabled: true
  realtime:
    stt_model: qwen3-asr-flash-realtime
    tts_model: qwen3-tts-flash-realtime
    voices:
      - Cherry
  session:
    max_sessions: 8
    duration_seconds: 1800
  vad:
    silence_ms: 500
    threshold: 0.2
```

也兼容已有 Java/Spring 风格的 `mail`（或 `spring.mail`）节点，例如：

```yaml
mail:
  host: smtp.example.com
  port: 465
  username: mailer@example.com
  password: replace-with-smtp-client-password
  default-encoding: UTF-8
  properties:
    mail:
      smtp:
        ssl:
          enable: true
        socketFactory:
          fallback: false
          class: com.example.MailSocketFactory
```

`host`、`port`、`username` 和 `password` 会映射为对应的 `SMTP_*` 设置；未单独提供
`SMTP_FROM_EMAIL` 时使用 `username`。`ssl.enable=true` 会启用 465 常用的隐式 SSL 并关闭
STARTTLS；也支持 `starttls.enable`/`starttls.required`。`socketFactory` 是 Java 专属设置，加载时
仅做兼容性校验，Python SMTP 不会使用其中的类名。当前邮件模板固定使用 UTF-8，因此其他
`default-encoding` 值会被拒绝。

`storage.qiniu` 用于用户头像上传。`ak`、`sk` 和 `public_base_url` 必须在部署前替换为
七牛账号的真实访问密钥和桶绑定域名；`bucket_name` 对应预先创建的对象存储空间。服务端只
接受 JPEG、PNG、GIF 和 WebP，默认最大 5 MiB。数据库保存由 `public_base_url` 和服务端
生成对象键组成的完整 URL，不保存本地临时路径或客户端文件名。

远程配置会在 PostgreSQL、Redis、邮箱认证、智能体和其他服务资源初始化前加载。分类式多级 YAML 会映射到现有 `Settings` 字段；为兼容已有部署，原有顶层大写字段以及 `mail`/`spring.mail` 格式仍可使用，但同一字段不能在扁平和多级结构中配置不同值。启用 Nacos 且配置 `NACOS_CONFIG_DATA_ID` 后，模型 API 密钥可以只保存在远程 YAML 中；远程配置应用完成后仍会校验至少有一个可用模型 Provider。当 `NACOS_STORAGE_CONFIG_REQUIRED=true` 时，Redis/PostgreSQL 六个存储字段任一缺失、内容为空、类型错误或 Nacos 不可用都会导致启动失败，不会回退到 `.env` 或代码默认值。启用邮箱认证时，`APP_TOKEN_SECRET` 以及完整的 SMTP 字段也必须存在且有效。远程值会覆盖本地环境中的同名设置，因此数据库连接池、RAG、邮件认证、语音、模型和检查点存储均优先使用 Nacos 配置。

`NACOS_*` 连接参数以及 `HOST`、`PORT`、`MODE`、`LOG_LEVEL`、`GRACEFUL_SHUTDOWN_TIMEOUT` 属于引导配置，只能通过环境变量设置。当前实现只在启动时加载配置，修改 Nacos 配置后需重启应用。若仅使用服务注册与发现，可显式设置 `NACOS_STORAGE_CONFIG_REQUIRED=false` 并不配置 `NACOS_CONFIG_DATA_ID`。若只使用配置中心而不注册当前服务，可设置 `NACOS_REGISTER_SERVICE=false`。运行期间可从 `app.state.nacos.list_instances(...)` 查询健康实例。

## 使用 agent-service-toolkit 构建或受其启发的项目

以下是部分借鉴了本代码仓库的代码或受其启发的公开项目。

- **[PolyRAG](https://github.com/QuentinFuxa/PolyRAG)** - 扩展了 agent-service-toolkit，增加了基于 PostgreSQL 数据库和 PDF 文档的 RAG 能力。
- **[alexrisch/agent-web-kit](https://github.com/alexrisch/agent-web-kit)** - agent-service-toolkit 的 Next.js 前端
- **[raushan-in/dapa](https://github.com/raushan-in/dapa)** - Digital Arrest Protection App（DAPA，数字逮捕防护应用）通过用户友好的平台，帮助用户高效举报金融诈骗和欺诈行为。

**如有新的项目需要添加，请提交一个编辑 README 的拉取请求或发起讨论！** 非常欢迎收录更多项目。

## 贡献

欢迎贡献！请随时提交拉取请求。

**关于本代码仓库维护方式的说明：** 这是一个由个人维护的项目，issue、PR 和讨论大约每两周集中处理一次，并由一个 AI 维护智能体提供协助。如果回复需要一到两周时间，敬请耐心等待——对于确实紧急的问题（漏洞报告等）或正在进行的 PR，我会尽力在几天内回复。如果你对具体运作方式感兴趣，可以在 [`docs/maintenance/`](docs/maintenance/) 中查看纳入版本控制的完整自动化操作手册。

目前，测试需要在不使用 Docker 的本地开发环境中运行。要运行智能体服务的测试：

1. 确保当前位于项目根目录，并且已经激活虚拟环境。

2. 安装开发依赖和 pre-commit 钩子：

   ```sh
   uv sync --frozen
   pre-commit install
   ```

3. 使用 pytest 运行测试：

   ```sh
   pytest
   ```

### 可选依赖项的冒烟测试

某些集成不会在单元测试套件或默认 CI 运行中进行测试，因为它们需要真实的基础设施：PostgreSQL 检查点和长期记忆存储、AG-UI 端点以及 LangFuse 追踪。`scripts/smoke_test.sh` 会在 Docker 中启动每项依赖，针对该依赖运行服务，端到端验证集成（包括直接验证 PostgreSQL 中的记录和重新连接后的记忆读取），然后将其关闭。

```sh
./scripts/smoke_test.sh                 # 默认：postgres、agui
./scripts/smoke_test.sh postgres        # 单个目标
./scripts/smoke_test.sh langfuse        # 重型：启动完整的 LangFuse 自托管技术栈
./scripts/smoke_test.sh all             # 全部，包括 langfuse
```

这些是供维护者或智能体选择性运行的可信度检查，并非 CI 的一部分。请运行与你的更改相匹配的目标，而非整套测试。冒烟测试使用独立的 Compose 项目和端口，避免清理日常开发的数据卷。

## 许可证

本项目采用 MIT 许可证授权——详情请参阅 LICENSE 文件。
