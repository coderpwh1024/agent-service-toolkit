# 账号、凭据与用户身份梳理

本文按当前仓库代码整理账号用途、环境变量和认证边界；不记录真实密钥，也不代表已经验证各账号的开通状态、额度或访问权限。

## 默认运行需要什么

当前默认方案使用 **阿里云百炼 + PostgreSQL**：

| 配置 | 用途 | 使用位置 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 聊天、RAG 向量生成、语音识别和语音合成共用的百炼密钥 | 服务端；启用语音时也需要提供给 Streamlit 进程 |
| `POSTGRES_USER`、`POSTGRES_PASSWORD` | PostgreSQL 数据库身份 | 服务端连接数据库；Compose 用于初始化数据库 |
| `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB` | 数据库连接目标 | 本地默认 `127.0.0.1:5432/agent_service`；服务容器内使用 `postgres:5432` |
| `AUTH_SECRET` | 可信后台服务凭据，由部署者自行设置 | 服务端管理调用和现有 Streamlit/Python 客户端；不能内置到移动 App |
| `APP_TOKEN_SECRET` | App 短期访问令牌的 HS256 签名密钥，至少 32 字符 | 仅服务端；启用邮箱认证或实时语音时必需 |
| `REDIS_URL` | Redis 连接地址 | 邮箱验证码的 3 分钟有效期、失败次数和 24 小时发送上限 |
| `SMTP_HOST`、`SMTP_FROM_EMAIL` | 验证邮件发送配置 | 启用邮箱认证时必需；可选用户名、密码和 STARTTLS |

本地 PostgreSQL 默认用户名和密码均为 `postgres`，见 [配置示例](../.env.example)。这是一组数据库凭据，不是聊天界面的登录账号。数据库当前用于会话检查点、跨会话记忆及 RAG 数据。

百炼相关地址和模型配置见 [百炼说明](Alibaba_Bailian.md)，RAG 初始化见 [RAG 说明](RAG_Assistant.md)。只使用这套默认方案时，不需要额外配置 OpenAI、Deepgram 或 ElevenLabs 账号。研究助手的审核、天气等扩展仍有各自的可选凭据。

## 可选模型服务账号

以下为仓库保留的接入选项，不需要全部配置。是否将模型加入可用列表由 [Settings](../src/core/settings.py) 决定，实际创建客户端的逻辑见 [LLM 工厂](../src/core/llm.py)。配置存在不等于外部服务已经验证可用。

| 服务 | 凭据或启用配置 | 配套配置与说明 |
| --- | --- | --- |
| 阿里云百炼 | `DASHSCOPE_API_KEY` | `DASHSCOPE_BASE_URL`；RAG 使用 `DASHSCOPE_EMBEDDING_MODEL` |
| OpenAI | `OPENAI_API_KEY` | 可选聊天模型 |
| DeepSeek | `DEEPSEEK_API_KEY` | 可选聊天模型 |
| Anthropic | `ANTHROPIC_API_KEY` | 可选聊天模型 |
| Google Gemini | `GOOGLE_API_KEY` | 与 Vertex AI 的文件凭据配置分开 |
| Google Vertex AI | `GOOGLE_APPLICATION_CREDENTIALS` | 凭据文件路径；见 [Vertex AI 说明](VertexAI.md) |
| Groq | `GROQ_API_KEY` | 聊天模型及研究助手的 Safeguard 审核 |
| AWS Bedrock | `USE_AWS_BEDROCK=true` | 仓库将凭据解析交给底层 SDK；启用开关本身不提供 AWS 身份 |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | 还需 `AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_DEPLOYMENT_MAP`；可配置 `AZURE_OPENAI_API_VERSION` |
| OpenRouter | `OPENROUTER_API_KEY` | 独立密钥，代码显式传入 OpenRouter 客户端 |
| OpenAI 兼容接口 | `COMPATIBLE_API_KEY` | 以 `COMPATIBLE_BASE_URL` 和 `COMPATIBLE_MODEL` 启用；认证要求取决于目标服务 |
| Ollama | `OLLAMA_MODEL` | 可选 `OLLAMA_BASE_URL`；仓库未定义专用账号或密钥字段 |
| 假模型 | `USE_FAKE_MODEL=true` | 用于测试，无外部模型账号；服务启动仍依赖 PostgreSQL |

服务至少需要一种模型配置。设置多个提供商后，可通过请求的 `model` 字段选择模型；未显式指定 `DEFAULT_MODEL` 时，配置百炼会优先选择百炼，假模型测试模式优先级更高。

## 工具、追踪与反馈账号

| 功能 | 账号或凭据 | 未配置时的行为 | 代码入口 |
| --- | --- | --- | --- |
| GitHub MCP | `GITHUB_PAT`；可选 `MCP_GITHUB_SERVER_URL` | GitHub 智能体不加载 MCP 工具 | [GitHub 智能体](../src/agents/github_mcp_agent/github_mcp_agent.py) |
| 天气查询 | `OPENWEATHERMAP_API_KEY` | 研究助手不添加天气工具 | [研究助手](../src/agents/research_assistant.py) |
| 内容审核 | `GROQ_API_KEY` | 跳过 Safeguard 模型审核 | [Safeguard](../src/agents/safeguard.py) |
| LangSmith 追踪 | `LANGCHAIN_API_KEY`、`LANGCHAIN_TRACING_V2` | 追踪开关默认关闭 | [Settings](../src/core/settings.py) |
| LangSmith 反馈 | `LANGCHAIN_API_KEY` | `/feedback` 仍会调用 LangSmith；未配置可用凭据时不能假定反馈可用 | [反馈接口](../src/service/service.py) |
| Langfuse 追踪 | `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_TRACING` | 追踪开关默认关闭 | [服务](../src/service/service.py)、[AG-UI](../src/service/agui.py) |

LangSmith 可配置 `LANGCHAIN_PROJECT`、`LANGCHAIN_ENDPOINT`；Langfuse 可配置 `LANGFUSE_HOST`。这些地址、项目名及开关不属于登录凭据。

GitHub 工具实际使用服务端配置的 PAT 身份，不随聊天中的 `user_id` 切换。DuckDuckGo 搜索和本地计算器在当前代码中没有单独的账号配置。

## 应用内用户身份与认证

后端支持无密码邮箱登录与注册。`EMAIL_AUTH_ENABLED=true` 时，新邮箱验证成功后写入 `app_users`，已存在邮箱验证成功后直接登录；两种路径都签发相同的短期 App Token。登出、刷新令牌和账号资料管理仍未实现。

`app_users` 使用自增 `BIGINT` 主键，包含必填昵称、规范化邮箱、可选图片 URL，以及 `create_by`、`update_by`、`create_time`、`update_time`、`is_delete`。有效记录的邮箱由部分唯一索引约束。自助注册的审计操作者使用 `AUTH_SERVICE_ACCOUNT_ID`，默认保留值为 `0`。

完整且幂等的 PostgreSQL DDL 位于 [`src/service/sql/create_app_users.sql`](../src/service/sql/create_app_users.sql)。服务启动时读取并执行同一文件，也可以手工执行 `psql -f src/service/sql/create_app_users.sql`，因此运行时初始化与部署脚本不会形成两套 schema 定义。

| 标识 | 当前含义 | 是否完成用户认证 |
| --- | --- | --- |
| `AUTH_SECRET` | 可信服务共享的管理凭据 | 是管理身份，可跨用户操作 |
| App access token | 邮箱验证或 `/auth/token` 签发的短期 JWT，`sub` 为用户 ID | 是普通用户身份 |
| `user_id` | 关联用户的线程、记忆和语音会话 | App Token 调用时由服务端绑定 |
| `thread_id` | 标识单次会话及其历史 | 由 `app_thread_owners` 校验归属，不是访问凭证 |
| `run_id` | 标识一次运行及其反馈 | 由 `app_runs` 校验归属 |

[Streamlit](../src/streamlit_app.py) 优先读取 session state 中的 `user_id`，其次读取 URL 参数，否则生成 UUID 并写回 URL。虽然常量名为 `USER_ID_COOKIE`，这段实现使用的是 session state 和 URL 参数，没有登录 Cookie 校验。

[服务端](../src/service/service.py) 的实际边界如下：

- 同时未设置 `AUTH_SECRET` 和 `APP_TOKEN_SECRET` 时，保留原开发模式，业务接口不要求 Bearer 令牌并按管理身份处理。该模式不适合多用户暴露。
- 设置后，`/info`、调用、流式输出、历史、线程列表、反馈及 AG-UI 路由接受可信 `AUTH_SECRET` 或 App Token；`/health` 独立于认证路由，默认文档路由也没有接入这一校验。
- `POST /auth/email/code` 和 `POST /auth/email/verify` 是公开认证入口。前者只接收 `email` 并向规范化后的邮箱发送 6 位数字验证码；新用户的默认昵称由邮箱 `@` 前的部分生成，头像默认为空。验证码 3 分钟有效且只能成功使用一次，连续错误 5 次后作废。
- 同一邮箱在滚动 24 小时内最多请求 6 封验证码邮件。Redis 使用邮箱 SHA-256 作为键的一部分，仅保存验证码 HMAC 摘要及待注册资料；明文验证码不会写入 Redis 或日志。
- 验证成功时会再次查询用户表。存在的有效邮箱直接登录；不存在时依靠数据库唯一索引原子注册，避免并发创建重复账号。
- 可信服务携带 `AUTH_SECRET` 调用 `POST /auth/token`，为已完成登录校验的 `user_id` 签发短期令牌。普通 App Token 不能再次签发令牌。
- App Token 的 `user_id` 取自 JWT `sub`。服务端对 HTTP、SSE、AG-UI 和语音入口执行线程归属校验，对反馈执行运行归属校验；跨用户访问返回 403。
- [Python 客户端](../src/client/client.py) 从自己的进程环境读取 `AUTH_SECRET` 并添加 `Authorization: Bearer ...`；它不会自动建立用户登录会话。

邮箱认证请求示例：

```http
POST /auth/email/code
Content-Type: application/json

{"email":"user@example.com"}
```

收到邮件后提交：

```http
POST /auth/email/verify
Content-Type: application/json

{"email":"user@example.com","code":"012345"}
```

响应中的 `access_token` 用于后续 `Authorization: Bearer <access_token>`。认证邮件完全使用固定模板生成，不调用 LLM，也不会把邮箱、默认昵称或验证码送入智能体提示词。

Compose 中的 Redis 配置面向本地开发，默认没有密码并映射宿主机端口。生产部署应将 Redis 放在受限私网，使用带认证信息的 `REDIS_URL`；跨不可信网络连接时还应使用 `rediss://`。

## 配置放在哪里

1. **本地 Python 运行**：配置可放在进程环境或项目 `.env`。Settings 使用 `find_dotenv()`；服务启动入口和 Streamlit 入口还调用 `load_dotenv()`。环境中已存在的值通常优先于 `.env`。
2. **Docker Compose**：服务端和 Streamlit 都通过 `env_file` 读取可选的 `.env`。[Compose](../compose.yaml) 会启动 PostgreSQL 和启用 AOF 的 Redis，并显式传入邮箱认证、SMTP 和百炼等变量。
3. **文件凭据**：开发用文件可放在 `privatecredentials/`，Compose 挂载到 `/privatecredentials`。本地路径和容器路径不同，`GOOGLE_APPLICATION_CREDENTIALS` 应指向运行进程能读取的路径。
4. **服务与 App 令牌**：Streamlit/Python 服务客户端可读取 `AUTH_SECRET`。移动 App 只保存短期 App Token，不保存 `AUTH_SECRET` 或 `APP_TOKEN_SECRET`。实时语音流程见 [Voice API](Voice_API.md)。浏览器直连 AG-UI 的认证处理见 [AG-UI 说明](AGUI.md)。

## 本次梳理发现的待处理项

- `.env.example` 列出了百炼、PostgreSQL、Redis、SMTP 和实时语音所需配置，其他可选账号配置以本文及代码为准。
- Compose 的服务端健康检查访问公开的 `/health`，启用认证后仍可正常探活。
- 现有 README 和文件凭据文档声称私有文件被 Git 和 Docker 构建忽略，但当前工作树缺少根目录 `.gitignore` 和 `.dockerignore`。现有文档的这一保证不能直接视为已落实；本机 Git 排除配置也不能替代随仓库分发的规则。
- 邮箱注册和登录已经实现；登出、刷新令牌及账号资料管理尚未实现。

以上是代码与配置层面的盘点。本次没有读取真实密钥内容、验证外部账号，也没有调整运行中的服务配置。
