# Mem0 对话记忆

`mem0-chatbot` 位于 agent 列表首位，也是文字聊天的默认 agent。它沿用 `chatbot.py` 的 LangGraph functional API、模型选择和 PostgreSQL 会话检查点，另用 Mem0 提取用户的长期事实与偏好。已有 `chatbot` 等 agent 的接口保持可用；已有会话继续指定原来的 agent，不要把原 agent 的 thread ID 直接切换给新 agent。

## 配置与启用

在 Nacos 的 `agent-service-toolkit.yaml` 中添加以下节点，保留已有的 `models.dashscope`、`storage.postgres` 和认证配置：

```yaml
memory:
  mem0:
    enabled: true
    collection_name: mem0_chat
    llm_model: qwen-plus
    embedding_model: qwen3.7-text-embedding-flash
    embedding_dims: 1024
    top_k: 5
    threshold: 0.3
    timeout_seconds: 20
    max_context_chars: 6000
    chat_context_tokens: 12000
```

默认复用 `models.dashscope.api_key` 和 `base_url`；未单独配置 `embedding_model` 时也复用全局模型。上述示例为 Mem0 单独指定了 1024 维的 flash 模型，避免改变其他功能使用的全局 embedding 模型。也可在 `memory.mem0` 中单独设置 `api_key` 和 `base_url`，接入支持聊天 JSON 输出与 embeddings 的 OpenAI 兼容服务。`llm_model` 是记忆提取模型，与用户在聊天界面选择的回答模型独立。`embedding_dims` 必须与供应商实际支持的 dimensions 参数一致；启用前确认模型可用性与配额。

关闭 Nacos 时使用对应的 `MEM0_ENABLED`、`MEM0_COLLECTION_NAME`、`MEM0_LLM_MODEL`、`MEM0_API_KEY`、`MEM0_BASE_URL`、`MEM0_EMBEDDING_MODEL`、`MEM0_EMBEDDING_DIMS`、`MEM0_TOP_K`、`MEM0_THRESHOLD`、`MEM0_TIMEOUT_SECONDS`、`MEM0_MAX_CONTEXT_CHARS` 和 `MEM0_CHAT_CONTEXT_TOKENS` 环境变量。密钥字段使用 `SecretStr`。默认 `MEM0_ENABLED=false`，让现有部署可以先完成数据库准备；此时新 agent 只有会话历史，响应中会返回 `memory_status=disabled`。远程配置只在启动时加载，修改后重启服务。

`chat_context_tokens` 限制回答模型的输入预算，默认 12000，范围 2048–131072。实现按消息正文的 UTF-8 字节数及消息开销保守估算，不依赖供应商分词器；部署时须按所选模型的实际窗口下调，并预留输出空间。系统规则和当前输入优先保留，然后放入能容纳的检索记忆和最近的完整历史消息。裁剪只影响模型输入，完整聊天检查点仍然保留；单条当前输入超过预算时提示拆分，返回 `memory_status=input_too_long`，不调用回答模型或提取记忆。`max_context_chars` 仍限制检索记忆正文总长度，也限制传给提取模型的近期对话正文总长度。

PostgreSQL 必须安装 pgvector。Compose 使用 `pgvector/pgvector:0.8.2-pg16`，维持 PostgreSQL 16 与原数据卷路径。已有数据库先备份，再按部署方式安装扩展；不要删除数据卷。启动账户需要创建 Mem0 表和 `vector` 扩展的权限；生产环境可由 DBA 提前执行 `CREATE EXTENSION IF NOT EXISTS vector`。新 agent 启用 Mem0 后，数据库、扩展或必要配置不可用会阻止服务启动，避免隐式使用 SQLite 或内存存储。

## 记忆与隔离

- 会话历史：原 PostgreSQL checkpointer 按 `thread_id` 保存完整消息，供聊天历史、取消恢复与语音播放上下文使用。
- 长期事实：Mem0 只处理本轮用户文本，并按认证后绑定的 `user_id` 和固定 `agent_id=mem0-chatbot` 写入与检索。线程 ID 作为来源元数据，不作为过滤范围，因此同一用户可在新会话中回忆旧偏好。
- 提取上下文与历史：`mem0_records` 保存 Mem0 的变更事件和每个用户/智能体/线程最近十条有效用户消息，使用六个审计字段、BIGINT identity、部分索引及逻辑删除。聊天提取时使用当前线程检查点中最近十条、且不超过 `max_context_chars` 的用户/助手消息作指代参考；助手提问只用于解释本轮用户的明确回答，不作为新事实来源。新线程不会读取旧的跨线程提取缓存，长期事实检索仍按用户和智能体跨线程共享。已有缓存不需要迁移，也不会在启动时删除；记忆管理清理会覆盖旧缓存和所有线程缓存。旧上下文仍保留为逻辑删除记录，应纳入数据库备份与保留期维护。

向量与实体表由 Mem0 SDK 管理，沿用其 UUID 与 JSONB schema，属于第三方表豁免；第一方 `mem0_records` 执行仓库的六字段规范。Mem0 2.2.1 的构造器强制创建 SQLite，项目通过集中在 `memory/mem0_sdk.py` 的版本适配层注入 PostgreSQL history，继续使用 SDK 的提取和搜索算法。依赖精确固定，升级时必须重新验证该适配层与 PostgreSQL 冒烟测试。

检索结果作为带有明确不可信数据标签的 JSON 参考消息传入，不使用系统消息角色，不能覆盖系统规则或用户当前的纠正。提示词要求只从本轮用户输入提取持久事实，不保存密钥、助手猜测和临时请求；无法明确解析指代时不提取。这属于模型约束，不等同于确定性的敏感数据检测。不要主动发送凭据。检索不足时助手应明确说明，不编造记忆。SDK 当前采用增量事实提取，不保证自动删除所有旧偏好；有冲突时当前用户纠正优先，跨会话冲突可能需要再次确认。

## 普通聊天策略

`mem0-chatbot` 的系统提示词要求结合最近几轮推进对话：重复提问时重新解释或澄清具体问题，发现明确错误时纠正；面对玩笑或挑衅保持平静，不推断用户情绪、不说教、不反复使用通用客服问句。用户换话题后直接回答新问题；明确要求复述、确认事实或没听清语音时允许重复。提示词中的虚构多轮示例用于展示回应方式，不作为固定话术，也不会进入真实聊天检查点或记忆提取上下文。文字和语音共用这些策略。

这些是模型行为指导，不保证每次生成都符合要求。验证提示词时应使用同一模型和参数，对下列多轮场景分别运行多次，检查是否根据上下文调整、事实是否一致，以及语音首句延迟：

| 场景 | 观察目标 |
| --- | --- |
| 连续三次相同或近似的挑衅，再转问普通问题 | 回应方式随上下文调整，不还击、不猜测情绪，切换后正常回答 |
| 连续表示没理解一个概念，再指出解释中的错误 | 换解释方式或聚焦澄清，并纠正有依据的错误 |
| 回答后要求“没听清，再说一遍”或确认数字 | 允许准确复述，不为避免重复而改变事实 |
| 询问历史中没有的信息，或记忆检索失败 | 明确证据不足，不编造经历、记忆或操作成功结果 |

单元测试使用替身模型，只验证策略与历史被传入、示例隔离、预算及流式链路，不验证真实模型的自然程度。真实模型的效果仍需以上述多轮联调确认。

## 记忆管理 API

服务提供以下接口，统一返回 `{"code": 200, "message": "success", "data": ...}`。接口只管理当前配置集合内的 `mem0-chatbot` 长期事实，不受客户端当前选中的其他聊天 agent 影响。需要开启 Mem0；关闭或不可用时返回 HTTP 503。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/memories` | 分页列出用户记忆及其 UUID |
| `GET` | `/memories/{memory_id}` | 查询指定记忆 |
| `PUT` | `/memories/{memory_id}` | 修改指定记忆文本，重新向量化 |
| `DELETE` | `/memories/{memory_id}` | 删除指定记忆，返回 `deleted_count: 1` |
| `DELETE` | `/memories` | 分批删除该用户所有 Mem0 聊天记忆，返回实际删除数量 |
| `GET` | `/memories/{memory_id}/history` | 分页查询 `ADD`、`UPDATE`、`DELETE` 历史，包含变更前后文本 |

所有接口继承已有 bearer 认证。App token 默认绑定 token 中的用户，可省略 `user_id`；传入其他用户 ID 返回 403。使用可信 `AUTH_SECRET`（或现有无认证开发模式）时，必须通过查询参数明确指定 `user_id`，省略返回 422，避免误操作随机或全局范围。用户 ID 不可为空，最长 128 字符。单条查询、修改、删除和历史查询都检查用户及智能体归属；不存在或不属于此用户的 UUID 统一返回 404，不暴露其他用户数据。

列表的 `data` 包含 `memories` 和 `next_cursor`，每条记忆包含 `id`、`memory`、`user_id`、`agent_id`、时间和来源 `metadata`。按 UUID 升序分页；`limit` 默认 50，范围 1–100，下一页传入 `cursor=<next_cursor>`。历史的 `data` 包含 `history` 和 `next_cursor`，按历史记录 ID 升序分页，`cursor` 为正整数。最后一页的 `next_cursor` 为 `null`。分页不是数据库快照，并发新增时可重新遍历列表。

修改请求只接受 `{"text": "新的长期事实"}`，去除首尾空白后须为 1–6000 字符；不允许修改所有者或任意元数据。与原文本相同的更新直接返回，不重复写入变更历史。

例如，使用 app token 管理自己的记忆：

```sh
curl "$AGENT_URL/memories?limit=50" \
  -H "Authorization: Bearer <app-access-token>"

curl -X PUT "$AGENT_URL/memories/<memory-uuid>" \
  -H "Authorization: Bearer <app-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"text":"我现在喜欢咖啡，不再喜欢茶"}'

curl -X DELETE "$AGENT_URL/memories/<memory-uuid>" \
  -H "Authorization: Bearer <app-access-token>"

curl "$AGENT_URL/memories/<memory-uuid>/history" \
  -H "Authorization: Bearer <app-access-token>"

curl -X DELETE "$AGENT_URL/memories" \
  -H "Authorization: Bearer <app-access-token>"
```

`AgentClient` 提供 `get_memories`、`get_memory`、`update_memory`、`delete_memory`、`delete_all_memories` 和 `get_memory_history`，以及对应的 `a` 前缀异步方法。可信服务调用示例：

```python
client = AgentClient(base_url="http://127.0.0.1:8080", get_info=False)
page = client.get_memories(user_id="alice", limit=50)
memory_id = str(page.memories[0].id)
client.update_memory(memory_id, "我喜欢咖啡", user_id="alice")
client.delete_memory(memory_id, user_id="alice")
history = client.get_memory_history(memory_id, user_id="alice")
result = client.delete_all_memories(user_id="alice")
```

### 删除、历史与兼容语义

- 修改或删除成功后，清空该用户/智能体的全部有效近期提取上下文；批量删除在开始时清空，即使没有长期事实也会执行。缓存使用逻辑删除，防止旧消息继续作为 Mem0 提取参考。这不会删除完整聊天检查点；后续用户再次明确提供同一事实仍可生成新记忆。
- 同一集合、用户和智能体的查询、提取和管理操作使用 PostgreSQL session advisory lock，在多个服务进程之间串行执行。锁期间不保持跨模型调用的数据库事务，异常时释放锁。
- 长期事实属于第三方 SDK 管理的表，本功能沿用 SDK 的物理删除，同时保留第一方 `mem0_records` 审计历史和聊天记录；因此它是“删除可检索事实”，不是清除所有原始文本或隐私擦除功能。实体关联清理由 SDK 尽力执行。
- 新历史在 JSONB 中记录 `user_id`、`agent_id`。旧部署中仍存在的记忆可通过实时所有者检查读取历史，并在修改/删除前补齐其旧历史的所有者。升级前已经被删除、且历史没有所有者信息的记忆无法可靠授权，返回 404，不猜测归属。不新增表或破坏性迁移。
- 批量删除只影响当前用户的 `mem0-chatbot` 事实，不影响其他用户或其他智能体，超过 100 条也会继续分批删除；重复清空返回 `deleted_count: 0`。单条重复删除返回 404。
- SDK 的向量更新/删除、历史写入和缓存清理不是一个原子事务，批量失败可能已删除部分记录。超时返回 504，已接受的 SDK 线程可能继续完成；连接或供应商错误返回 503。此时应先查询状态和历史，再决定是否重试，不能把错误当作已回滚。自动聊天提取仍是增量新增；显式记忆管理指令走下述对话流程，内部复用记忆服务，不通过模型直接调用 HTTP 接口。

操作语义参考 [Mem0 官方 OSS REST 文档](https://github.com/mem0ai/mem0/blob/main/docs/open-source/features/rest-api.mdx)，具体参数以项目固定的 Mem0 2.2.1 为准。

## 通过语音或文字管理记忆

在已有语音会话中选择 `agent_id=mem0-chatbot`，用户可以直接说出要管理的内容，不需要朗读 UUID。STT 转写经现有智能体入口执行，操作提示和结果进入原有 TTS 播报链路；文字入口也支持同一流程。已有 `/memories`、聊天和语音接口、默认 agent 兼容行为及 WebSocket 事件结构保持原样。

| 用户说法 | 助手行为 |
| --- | --- |
| “列出你记住的我的信息” | 播报最多五条记忆及编号 |
| “把我喜欢茶的记忆改成我喜欢咖啡” | 查找原记忆，读出原文和新内容，请求确认修改 |
| “删除我喜欢茶的记忆” | 查找并读出目标，请求确认删除 |
| “删除第二条” | 使用本线程刚才列出的第二条，重新读取并预览 |
| “清空我的所有记忆” | 统计当前用户的全部长期记忆，播报数量，请求确认清空 |
| “查看刚才那条记忆的变更历史” | 查询最近操作过的记忆，即使它已被删除，也可按归属读取历史 |

例如一次完整的语音修改：

1. 用户：“把我喜欢茶的记忆改成我喜欢咖啡。”
2. 助手：“将把记忆‘我喜欢茶’修改为‘我喜欢咖啡’。请说确认修改，或说取消。”
3. 用户：“确认修改。”
4. 后端成功更新并清理提取缓存后，助手播报实际结果：“这条记忆已更新为‘我喜欢咖啡’。”

查找到多条可能的记忆时，助手先编号播报，用户说“第二条”等选择目标，再说“确认删除”或“确认修改”。清空全部时可说“确认清空”。“确认”“是的”等也可确认当前预览；明确说出的操作类型必须与预览一致。说“取消”“不要了”等会清除待确认操作；转向其他普通话题也会结束待确认流程。缺少新内容、没有匹配结果或候选编号无效时，不执行写操作。假设示例、否定指令、关于如何管理记忆的说明性问题由结构化分类与普通聊天处理，不直接触发修改或删除。

待确认操作保存在现有线程检查点的 `memory_dialogue` 中，绑定用户 ID，有效期五分钟，可在同一线程重连后继续；其他线程或用户不能沿用该确认。操作前在用户锁内重新核对原文及更新时间；语音清空会核对预览时全部记忆的摘要，期间有新增、修改或删除时要求重新发起确认。无需新增数据库表或调整已有管理接口。

语义分类和候选匹配使用当前选择的回答模型及 Pydantic 结构化输出，要求模型支持工具调用或原生结构化输出；百炼使用 Function Calling；普通且不含相关关键词的聊天不增加分类调用。模型、搜索或存储不可用时失败可控，不伪造成功。内部分类 JSON 标记为 `skip_stream`，不会当作聊天 token 下发或播报；只将应用验证过的候选编号映射到真实 ID，用户身份始终来自认证绑定，不由模型填写。参考 [LangChain 结构化输出](https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI/with_structured_output) 和 [百炼 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。

关闭长期记忆时，“修改代码”“删除代码中的注释”等不涉及记忆的请求直接进入普通聊天。涉及记忆的关键词仍经语义分类区分管理操作和说明性问题；分类模型不可用时返回记忆未启用，不执行操作。

管理请求、选择及确认轮次不进入 Mem0 自动提取；插话恢复附带的旧输入也不会被当作本轮新事实再次提取。成功、待确认、待选择及失败结果通过最终 AI 消息的 `response_metadata.memory_status` 区分，例如 `confirmation_required`、`selection_required`、`updated`、`deleted`、`cleared`、`cancelled`、`conflict`、`operation_failed`。普通聊天仍沿用原来的记忆状态。

插话仍会立即停止失效播报。取消待确认操作不撤销已经提交到同步 SDK 的调用；已接受的修改或删除可能继续完成，超时或断线也不能视为回滚，应先查询记忆及历史。重复确认已成功的相同文本更新不会重复写历史；已删除的指定 UUID 不会被替换为其他目标；旧的清空确认不能删除之后新增的记忆。完整聊天记录及审计历史继续保留。语音列表和历史播报最多五项，完整结果仍可通过原有接口分页查询。

## 语音和流式接口

创建语音会话时传入当前聊天选择的 agent：

```json
{
  "agent_id": "mem0-chatbot",
  "thread_id": "existing-mem0-thread",
  "voice": "Cherry"
}
```

`GET /voice/capabilities` 提供 `default_agent`，并把新 agent 标记为 `speech_mode=streaming`、`cancel_execution=true`。客户端应把当前选择的 agent 和 thread ID 传给语音接口；为兼容旧客户端，省略 `agent_id` 的语音会话仍使用 `chatbot`。STT 识别文本进入新 agent，只有该 agent 的回答进入 TTS。文字与语音使用同一身份时共享长期记忆；原始音频、助手回答和播放确认记录不参与事实提取。

记忆检索发生在生成前；完成生成后等待本轮用户事实提取，再提交最终消息与检查点。模型 token 保持流式输出，不缓冲完整回答。插话仍立即取消 agent/TTS 并丢弃失效音频。同步 SDK 在线程中运行，取消不能强制终止已接受的 SDK 调用：它可能继续完成用户事实写入，但不会保存被取消的助手回答或推断用户已听到该回答。服务关闭时等待正在运行的记忆操作，再关闭模型客户端和连接池。

普通聊天最终 AI 消息的 `response_metadata.memory_status` 为 `disabled`、`saved`、`saved_search_failed`、`write_failed` 或 `input_too_long`。`saved` 表示提取操作完成，可能没有产生新的事实。提取响应必须包含结构正确的 `memory` 数组，合法空数组表示没有新事实；空响应、非法 JSON、缺少数组或事实结构无效均返回 `write_failed`，不会被 SDK 转为空结果后误报成功。回答生成时只知道服务是否可用，自动提取尚未执行，因此助手不能声称本轮事实已经保存。运行期间的检索/写入异常不打断聊天，但会通过状态和日志报告，失败写入不会宣称成功；目前不提供后台重试队列，完整聊天检查点仍可用于人工补录。每个进程串行执行记忆操作，最多接受八个进行中或等待中的操作，队列满时降级并返回失败状态，避免无界任务堆积。需要高吞吐时应先设计持久任务队列、重试与跨进程幂等，再扩大并发。

## 日志和验证

通过 `python src/run_service.py` 启动时，统一使用标准库 logging 与 JSON formatter。在本地引导配置中设置 `LOG_LEVEL=INFO` 可查看 agent 开始/完成、记忆命中数量、写入数量、耗时与错误类型；`LOG_FORMAT=text` 可切换文本日志。这两个字段不能由 Nacos 覆盖。日志不输出用户消息、检索事实、连接串、密钥或原始音频，并过滤可能输出供应商载荷的 SDK 日志。Mem0 遥测在加载 SDK 前关闭。

```sh
uv sync --frozen
uv run pytest tests/agents/test_mem0_chatbot.py tests/memory/test_mem0_service.py tests/memory/test_mem0_sdk.py tests/memory/test_mem0_management.py tests/memory/test_mem0_history.py tests/service/test_memories.py tests/client/test_memory_client.py tests/voice/test_realtime_service.py -q
uv run pytest tests/agents/test_memory_commands.py tests/memory/test_mem0_mutations.py -q
./scripts/smoke_test.sh postgres
```

PostgreSQL 冒烟测试使用实际 Mem0 算法、pgvector 和 PostgreSQL history，以确定性的测试模型验证提取、去重、用户/agent 隔离、上下文上限和重连持久化，不访问付费模型。上线前仍需使用部署自己的模型配置做人工文字及语音联调；单元测试不能证明供应商模型名称、维度、权限和服务配额有效。

直接对本机 PostgreSQL 运行单个冒烟测试时，pytest 不会启动服务的 Nacos 配置加载流程；测试进程的 `POSTGRES_*` 配置必须与 Nacos 中的数据库配置一致。正常服务启动则会先加载 Nacos，再初始化 Mem0 和数据库连接。

参考：[Mem0 pgvector 配置](https://docs.mem0.ai/components/vectordbs/dbs/pgvector)、[Mem0 官方实现](https://github.com/mem0ai/mem0)、[pgvector 部署说明](https://github.com/pgvector/pgvector)。
