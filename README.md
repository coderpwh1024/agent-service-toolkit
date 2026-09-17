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
# 配置方式参见 .env.example。

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
# compose.yaml 会将宿主机中的 DASHSCOPE_API_KEY 传入服务容器；.env 可选。
docker compose watch
```

### 架构图

<img src="media/agent_architecture.png" width="600" alt="智能体架构图">

### 主要特性

1. **LangGraph 智能体及最新特性**：使用 LangGraph 框架构建的可自定义智能体。实现了 LangGraph v1.0 的最新特性，包括使用 `interrupt()` 实现人在回路、使用 `Command` 实现流程控制、使用 `Store` 实现长期记忆，以及 `langgraph-supervisor`。
1. **FastAPI 服务**：通过流式和非流式端点提供智能体服务。
1. **高级流式传输**：采用一种新颖的方法，同时支持基于 token 和基于消息的流式传输。
1. **AG-UI 协议支持**：每个智能体也会通过 [AG-UI 协议](https://docs.ag-ui.com) 提供服务，以连接 CopilotKit 等兼容 AG-UI 的前端——请参阅[文档](docs/AGUI.md)。
1. **Streamlit 界面**：提供用户友好的聊天界面，并支持基于阿里云百炼的语音输入和输出；两者复用 `DASHSCOPE_API_KEY`。
1. **多智能体支持**：在服务中运行多个智能体，并通过 URL 路径调用。可用的智能体和模型在 `/info` 中说明。
1. **异步设计**：使用 async/await 高效处理并发请求。
1. **内容审核**：使用 Safeguard 实现内容审核（需要 Groq API 密钥）。
1. **RAG 智能体**：使用 PostgreSQL 和阿里云百炼 Embedding 实现的 RAG 智能体——请参阅[文档](docs/RAG_Assistant.md)。
1. **聊天记录与长期记忆**：使用 PostgreSQL 保存会话检查点和跨会话记忆，并通过 `/threads` 按智能体列出用户之前的对话。
1. **反馈机制**：包含一个与 LangSmith 集成的星级反馈系统。
1. **Docker 支持**：包含 Dockerfile 和 Docker Compose 文件，便于开发和部署。
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

2. 设置环境变量：
   至少需要一个 LLM API 密钥或相关配置。推荐在当前 shell 中配置 `DASHSCOPE_API_KEY`，并在根目录的 `.env` 中配置 PostgreSQL。最小配置参考 [`.env.example`](./.env.example)，其他服务账号及认证边界见[账号、凭据与用户身份梳理](docs/Accounts_and_Credentials.md)。

3. 现在，你可以使用 Docker 或仅使用 Python，在本地运行智能体服务和 Streamlit 应用。推荐使用 Docker，以简化环境配置，并在代码发生更改时立即重新加载服务。

### 特定 AI 提供商的额外设置

- [设置阿里云百炼千问](docs/Alibaba_Bailian.md)
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

本项目包含 Docker 配置，便于开发和部署。`compose.yaml` 文件定义了三个服务：`postgres`、`agent_service` 和 `streamlit_app`。每个服务的 `Dockerfile` 位于各自对应的目录中。

对于本地开发，建议使用 [docker compose watch](https://docs.docker.com/compose/file-watch/)。该功能会在检测到源代码更改时自动更新容器，从而提供更顺畅的开发体验。

1. 确保系统中已安装 Docker 和 Docker Compose（>= [v2.24.0](https://docs.docker.com/compose/release-notes/#2240)）。

2. 默认使用阿里云百炼的千问 3.8 Max。如果宿主机当前环境已经配置 `DASHSCOPE_API_KEY`，`compose.yaml` 会自动将它传入服务容器，无需创建 `.env`。否则，可根据 `.env.example` 创建 `.env`：

   ```sh
   cp .env.example .env
   # 编辑 .env，添加你的 API 密钥
   ```

3. 以监视模式构建并启动服务：

   ```sh
   docker compose watch
   ```

   这将自动执行以下操作：
   - 启动智能体服务所连接的 PostgreSQL 数据库服务
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

   本地默认连接 `127.0.0.1:5432/agent_service`，用户名和密码均为 `postgres`，与 Compose 默认配置一致。使用其他数据库时，在 `.env` 中设置 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB`、`POSTGRES_USER` 和 `POSTGRES_PASSWORD`，并提前创建数据库。

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
