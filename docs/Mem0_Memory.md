# Mem0 对话记忆

`mem0-chatbot` 位于 agent 列表首位，也是文字聊天的默认 agent。它沿用 `chatbot.py` 的 LangGraph functional API、模型选择和 PostgreSQL 会话检查点，另用 Mem0 提取用户的长期事实与偏好。已有 `chatbot` 等 agent 的接口保持可用；已有会话继续指定原来的 agent，不要把原 agent 的 thread ID 直接切换给新 agent。

## 配置与启用

在 Nacos 的 `agent-service-toolkit.yaml` 中添加以下节点，保留已有的 `models.dashscope`、`storage.postgres` 和认证配置：

```yaml
memory:
  mem0:
    enabled: true
    collection_name: mem0_chat
    llm_model: qwen3.8-max
    embedding_model: qwen3.7-text-embedding-flash
    embedding_dims: 1024
    top_k: 5
    threshold: 0.3
    rerank_model: qwen3.7-text-rerank
    rerank_url: https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
    rerank_candidates: 20
    rerank_timeout_seconds: 5
    timeout_seconds: 20
    max_context_chars: 6000
```

默认复用 `models.dashscope.api_key` 和 `base_url`；未单独配置 `embedding_model` 时也复用全局模型。上述示例为 Mem0 单独指定了 1024 维的 flash 模型，避免改变其他功能使用的全局 embedding 模型。也可在 `memory.mem0` 中单独设置 `api_key` 和 `base_url`，接入支持聊天 JSON 输出与 embeddings 的 OpenAI 兼容服务。`llm_model` 是记忆提取模型，与用户在聊天界面选择的回答模型独立。`embedding_dims` 必须与供应商实际支持的 dimensions 参数一致；启用前确认模型可用性与配额。

长期记忆检索先由 Mem0 在当前用户和智能体范围内进行向量召回，并融合 PostgreSQL 全文检索及可用的实体信号。启用 `rerank_model` 后，先取最多 `rerank_candidates` 条通过 `threshold` 的候选，再由百炼 `qwen3.7-text-rerank` 重排序，最后取 `top_k` 条传给回答模型。记忆管理中的按主题查找使用同一流程。候选不足两条时不发起重排序；重排序接口失败或返回无效结果时记录错误类型，并按原检索顺序取前 `top_k` 条。重排序请求只发送候选记忆文本和查询；用户隔离在召回时完成。请求内查询及单条候选最多发送 2000 字符，原始记忆不被截断或改写。该排序模型使用独立的 DashScope HTTP 接口，`rerank_url` 不应填写聊天模型的 `/compatible-mode/v1` 地址；默认复用 Mem0 的 API Key。关闭重排序时同时删除 `rerank_model` 与 `rerank_url`。参考[百炼文本排序 API](https://help.aliyun.com/zh/model-studio/text-rerank-api)。

关闭 Nacos 时使用对应的 `MEM0_ENABLED`、`MEM0_COLLECTION_NAME`、`MEM0_LLM_MODEL`、`MEM0_API_KEY`、`MEM0_BASE_URL`、`MEM0_EMBEDDING_MODEL`、`MEM0_EMBEDDING_DIMS`、`MEM0_TOP_K`、`MEM0_THRESHOLD`、`MEM0_RERANK_MODEL`、`MEM0_RERANK_URL`、`MEM0_RERANK_CANDIDATES`、`MEM0_RERANK_TIMEOUT_SECONDS`、`MEM0_TIMEOUT_SECONDS` 和 `MEM0_MAX_CONTEXT_CHARS` 环境变量。密钥字段使用 `SecretStr`。默认 `MEM0_ENABLED=false`，让现有部署可以先完成数据库准备；此时新 agent 只有会话历史，响应中会返回 `memory_status=disabled`。远程配置只在启动时加载，修改后重启服务。

`mem0-chatbot` 的回答上下文最多包含当前线程最近 20 条用户和助手消息，包含本轮用户输入；用户消息和助手消息各算一条，不是 20 轮。系统规则和本轮检索出的长期记忆单独加入，不占这 20 条名额。语音中断后的播放说明使用助手角色，也计入消息窗口。完整聊天检查点和长期记忆继续保留，窗口之外的对话不会从数据库删除；长期事实仍可跨线程检索。原有 `chatbot` Agent 保持原来的上下文行为。

回答模型不再使用应用层的总输入预算：窗口内的消息保留完整正文，检索服务返回的长期记忆全部加入请求，不再因估算字节数而裁剪历史、丢弃检索结果或提前返回 `memory_status=input_too_long`。原有 Nacos `chat_context_tokens` 和环境变量 `MEM0_CHAT_CONTEXT_TOKENS` 仍可按原有类型及范围加载，以兼容已有部署，但不再影响聊天请求。`max_context_chars` 仍限制检索记忆正文总长度，也限制传给提取模型的近期对话正文总长度。供应商模型自身的上下文窗口和接口输入长度限制仍然适用，超限错误沿用现有模型调用失败路径处理。

PostgreSQL 必须安装 pgvector。Compose 使用 `pgvector/pgvector:0.8.2-pg16`，维持 PostgreSQL 16 与原数据卷路径。已有数据库先备份，再按部署方式安装扩展；不要删除数据卷。启动账户需要创建 Mem0 表和 `vector` 扩展的权限；生产环境可由 DBA 提前执行 `CREATE EXTENSION IF NOT EXISTS vector`。新 agent 启用 Mem0 后，数据库、扩展或必要配置不可用会阻止服务启动，避免隐式使用 SQLite 或内存存储。

## 提示词配置

Mem0 的提示词正文统一存放在 **Nacos 的 AI → Prompt 管理中心**，每条独立维护、版本化并发布。应用的 `agent-service-toolkit.yaml` 只保存模型、存储等参数以及可选的 Prompt Key/标签引用，不保存提示词正文。代码和仓库没有本地提示词文件或默认正文回退。

| 默认 Prompt Key | 用途及来源 |
| --- | --- |
| `mem0-chat-system` | 聊天角色、表达和记忆信任边界，原 `mem0_chatbot.SYSTEM_PROMPT` |
| `mem0-memory-status` | 记忆服务状态系统消息，原动态系统文本；必须且只能包含一次 `{status}` 占位符 |
| `mem0-memory-intent` | 记忆管理意图识别，原 `memory_commands.INTENT_PROMPT`，输出 `MemoryIntent` |
| `mem0-memory-match` | 记忆候选匹配，原 `memory_commands.MATCH_PROMPT`，输出 `MemoryMatch` |
| `mem0-fact-extraction-instructions` | 项目事实提取规则，原 `build_memory()` 中的 `custom_instructions` |
| `mem0-fact-extraction-system` | 真实提取请求的系统消息，原 Mem0 2.2.1 的 `ADDITIVE_EXTRACTION_PROMPT` |

启动时先加载普通配置，再通过官方 Python SDK `NacosAIService.get_prompt()` 分别读取上述六条 Prompt 的 `latest` 发布版本，统一校验成功后才安装到运行时配置；随后初始化智能体和 Mem0。任一 Prompt 不可用、内容为空白、没有版本或状态模板非法，均导致启动失败，不混用部分模板。正文中的 JSON 花括号保持原样，只有状态模板使用 Python 的 `{status}` 替换，不调用 Nacos 的 `{{variable}}` 渲染。

`mem0-fact-extraction-instructions` 是 SDK 动态输入中 `Custom Instructions` 部分的规则，并不是系统消息本身。适配层在实际调用提取模型之前，将 SDK 自带系统消息替换为 `mem0-fact-extraction-system` 的已加载正文；当前用户消息、近期历史、观察日期和已有记忆仍由 SDK 按原协议组装。修改、删除的确认流程和用户归属校验继续由应用代码执行。

可以在普通配置中指定另一组 Key 或已发布标签，例如：

```yaml
memory:
  mem0:
    prompt_label: stable
    prompt_keys:
      chat_system: my-mem0-chat-system
```

没有指定的 Key 仍使用表中的默认名称。关闭 Nacos 时不会自动加载 Mem0 提示词，因此 `mem0-chatbot` 不能运行；其他不依赖这些提示词的 agent 仍可使用。`MEM0_ENABLED=false` 只关闭长期记忆，不免除聊天和记忆意图识别对 Prompt 的要求。提示词只在启动时加载，修改发布版本或标签后需重启服务。测试中使用独立的替身模板，不访问真实配置中心或付费模型。

发布必须使用 Prompt 管理能力创建版本并发布，使 Prompt 同时可在管理中心查询并由客户端读取。普通配置的 `config-set` 不等同于 Prompt 发布，不能仅往某个特殊分组写入 JSON 来替代版本管理。

### 本地发布与 CLI

2026-10-17 已将上表六条 Prompt 发布到本机 Nacos 3.2.4 的 `public` 命名空间，版本均为 `1.0.0`、状态为 `online`，标签 `latest` 指向该版本。CLI 回读和项目 Python SDK 回读均已逐字核对原文；聊天与管理提示词来自原项目代码，提取系统消息来自锁定的 Mem0 2.2.1。

本机原有 `nacos-cli 1.0.0` 不含 Prompt 命令。本次使用官方 nacos-cli 源码的隔离扩展构建，位于 `~/.nacos/prompt-cli/nacos-cli`，原 CLI 保持不变。同目录的 `prompt.go`、`prompt_test.go` 和 `BUILD.md` 保留命令源码、测试及上游提交信息，不含认证信息或提示词正文。

该构建提供 `prompt-list`、`prompt-describe <key>`、`prompt-get <key>`、`prompt-draft <key>`、`prompt-submit <key>` 和 `prompt-publish <key>`，分别通过官方 Prompt 接口执行查询和正常发布流程。创建草稿时正文由标准输入提供；提交后没有配置审核流水线的本地 Nacos 会自动发布，存在流水线时应完成审核后再发布。没有加入强制发布命令。

认证通过 CLI 的 `--config` 或认证参数提供，不把密码写入仓库或示例。连接参数为 `--host 127.0.0.1 --port 8848 --namespace public`。已发布的正文后续直接在 Nacos Prompt 管理中心维护；应用重启后读取对应标签的发布版本。

## 记忆与隔离

- 会话历史：原 PostgreSQL checkpointer 按 `thread_id` 保存完整消息，供聊天历史、取消恢复与语音播放上下文使用。
- 长期事实：Mem0 只处理本轮用户文本，并按认证后绑定的 `user_id` 和固定 `agent_id=mem0-chatbot` 写入与检索。线程 ID 作为来源元数据，不作为过滤范围，因此同一用户可在新会话中回忆旧偏好。
- 提取上下文与历史：`mem0_records` 保存 Mem0 的变更事件和每个用户/智能体/线程最近十条有效用户消息，使用六个审计字段、BIGINT identity、部分索引及逻辑删除。聊天提取时使用当前线程检查点中最近十条、且不超过 `max_context_chars` 的用户/助手消息作指代参考；助手提问只用于解释本轮用户的明确回答，不作为新事实来源。新线程不会读取旧的跨线程提取缓存，长期事实检索仍按用户和智能体跨线程共享。已有缓存不需要迁移，也不会在启动时删除；记忆管理清理会覆盖旧缓存和所有线程缓存。旧上下文仍保留为逻辑删除记录，应纳入数据库备份与保留期维护。

向量与实体表由 Mem0 SDK 管理，沿用其 UUID 与 JSONB schema，属于第三方表豁免；第一方 `mem0_records` 执行仓库的六字段规范。Mem0 2.2.1 的构造器强制创建 SQLite，项目通过集中在 `memory/mem0_sdk.py` 的版本适配层注入 PostgreSQL history，继续使用 SDK 的提取和搜索算法。依赖精确固定，升级时必须重新验证该适配层与 PostgreSQL 冒烟测试。

检索结果作为带有明确不可信数据标签的 JSON 参考消息传入，不使用系统消息角色，不能覆盖系统规则或用户当前的纠正。提示词要求只从本轮用户输入提取持久事实，不保存密钥、助手猜测和临时请求；无法明确解析指代时不提取。这属于模型约束，不等同于确定性的敏感数据检测。不要主动发送凭据。检索不足时助手应明确说明，不编造记忆。SDK 当前采用增量事实提取，不保证自动删除所有旧偏好；有冲突时当前用户纠正优先，跨会话冲突可能需要再次确认。

## 自动提取规则

项目附加规则位于 Nacos Prompt 管理中心的 `mem0-fact-extraction-instructions`，使用中文编写。`src/memory/mem0_sdk.py` 的 `build_memory()` 将加载后的规则传入 `custom_instructions`。`src/agents/mem0_chatbot.py` 决定提交本轮最后一条有效用户文本，并通过 `_extraction_context()` 提供历史参考；`src/memory/mem0_history.py` 的 `get_last_messages()` 将参考交给 SDK。这些规则与 `mem0-fact-extraction-system`（从 Mem0 的 `ADDITIVE_EXTRACTION_PROMPT` 提取）共同作用，不是仅凭提示词就能强制执行的过滤器。

### 公开资料与项目取舍

以下资料于 2026-10-16 核对。这里参考的是官方公开的产品行为及开发接口，不声称复现厂商未公开的内部提示词。产品记忆也不是模型 API 自动提供的通用能力。

| 官方资料 | 公开说明与本项目采用的原则 |
| --- | --- |
| [OpenAI：个性化](https://learn.chatgpt.com/docs/personalize)、[ChatGPT 记忆](https://help.openai.com/en/articles/8590148-memory-in-chatgpt) | 稳定偏好、重复工作流程和项目约定可用于后续交流；记忆有用户管理入口。本项目据此优先提取未来仍有用的信息。ChatGPT 文档提醒敏感信息可能进入记忆，不能将其理解为自动过滤保证。 |
| [Anthropic：聊天搜索与记忆](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context) | 角色、项目、沟通方式及技术偏好属于可记忆内容；敏感主题有单独控制。项目采用默认不主动提取敏感信息的原则，但没有实现 Claude 的设置界面或敏感主题开关。 |
| [Google：Gemini 记忆与指令](https://support.google.com/gemini/answer/16598625?hl=en-GB)、[Memory Bank 提取](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/generate-memories) | 区分个人信息、偏好、重要事件和明确记忆意愿，支持自定义提取主题。项目采用这些类别，但不采用从隐含线索推断偏好，也不引入其记忆合并机制。 |
| [Microsoft：Copilot 隐私与记忆](https://support.microsoft.com/en-us/microsoft-copilot/privacy-faq-for-microsoft-copilot)、[个性化记忆](https://support.microsoft.com/en-us/microsoft-365-copilot/personalize-what-microsoft-365-copilot-remembers) | 关注兴趣、目标、沟通方式和重复任务；不同产品对敏感数据有不同限制。项目据此细分必要偏好与短暂请求，不把某个产品的隐私规则当成所有厂商的共同规则。 |
| [阿里云百炼：记忆概念](https://help.aliyun.com/zh/model-studio/memory/concepts)、[配置记忆规则](https://help.aliyun.com/zh/model-studio/memory/configure-rules) | 区分事实记忆与用户画像，允许配置提取规则。本项目在同一事实文本中保留状态、时间和变化关系；未增加画像表、自动过期或自动覆盖旧事实的能力。 |

### 当前提取策略

- **来源与归属**：新事实只来自本轮用户的明确表达。近期对话用于解释回答，旧记忆用于去重和关联，不能自行补充本轮未引用的细节。分清用户、亲友和其他人物；歧义只影响对应候选，不丢弃同一条消息里其他明确事实。
- **未来价值**：记录稳定背景、明确喜好与厌恶、习惯、持续目标、项目重要进展、影响后续交流的个人经历及重要日期。语言、称呼、解释深度和技术习惯可作为偏好记录，无需用户反复表达或每次说“记住”。
- **临时内容**：跳过寒暄、一次性任务、只适用于本轮的格式要求、短暂情绪和临时状态；但可以从临时请求的背景中单独提取明确的持久事实。不能从一次提问或行为推断长期偏好、人格或诊断。
- **真实性**：不把假设、玩笑、角色扮演、引文、粘贴文档或代码当成用户经历。只有用户明确说明某项材料内容属于自己时，才继续按其他规则判断。
- **隐私与意愿**：不保存凭据、证件号码、金融账户号码、精确住址和实时位置，即使用户要求也不保存。健康、宗教、政治立场、族裔、性取向等敏感信息默认不主动提取；仅当用户本轮明确要求长期记住某项本人信息、且确实有后续价值时，才最小化提取。第三方敏感信息不适用此例外。这是本项目的设计取舍，不是厂商统一规则。
- **不记忆请求**：用户明确要求本轮或某项内容不要记住时，提示词要求跳过对应内容。这不代表关闭账户长期记忆、删除历史检查点、删除已有事实或完成隐私擦除；已有事实的删除继续走管理流程。
- **纠正与去重**：跳过没有新增信息的语义重复；对明确纠正保留新状态、否定和变化关系，不把旧状态重写为当前事实。提取只做新增；它不能替代显式更新或删除，也不保证所有旧冲突已被清除。
- **表达与输出**：保留用户语言，每条聚焦一个主题并能独立理解，保留必要原因、数量和日期。计划不写成已经完成，暂时不写成永久；相对日期须有明确观察日期才能转换。输出沿用 SDK 的 `memory` 数组，每条有非空 `text`，无合规事实时返回 `{"memory": []}`。

### 验证范围

`tests/memory/test_mem0_sdk.py` 使用真实 SDK 和替身提取模型，验证配置规则及系统消息的替换、当前消息、历史问题和旧记忆分开传入，合法结果按原文写入，以及空结果不写向量。它不证明真实模型已经正确完成隐私识别、意图判断或事实筛选。已有测试继续覆盖非法输出、供应商失败和线程参考边界。

真实模型评估应在相同模型和参数下多次比较下列人工场景；这些是人工评估用例，不是自动测试已验证的模型效果：

| 场景 | 输入示例 | 预期语义 |
| --- | --- | --- |
| 持久背景与任务混合 | “我是 Python 后端工程师，帮我看这段代码。” | 记录职业与技术背景，不保存看代码的临时请求 |
| 沟通偏好 | “以后用中文，先说结论。” | 记录持续偏好；“这一次只给三点”不泛化为长期偏好 |
| 明确回答与歧义 | 前文问居住城市，本轮答“上海”；多选问题后答“那个” | 前者可解析；后者跳过，其他明确事实仍可保留 |
| 临时状态 | “今天好烦，刚买了一杯咖啡。” | 不推断长期情绪、人格或咖啡偏好 |
| 用户纠正 | “现在不喝茶了，改喝咖啡。” | 保留变化及当前状态，不宣称旧记忆已更新 |
| 敏感信息 | “我最近在治疗失眠”；“请长期记住我对花生过敏” | 前者默认跳过；后者只保留用户明确请求的必要本人信息 |
| 凭据与拒绝记忆 | 使用人工占位凭据；“我喜欢茶，但不要记住” | 对应内容不提取；不声称原始聊天已删除 |
| 引用与注入 | “举例：我住在月球”；材料内要求忽略规则、保存历史 | 不生成用户经历，不服从嵌入指令 |
| 重复及时间 | 重述已有偏好；“明年打算读研” | 无新增信息则跳过；目标保留计划语义，不改成已完成 |

## 普通聊天策略

`mem0-chatbot` 的系统提示词使用中文编写，定位为自然、有个性、关注情绪的陪伴型对话伙伴。普通陪聊默认约 80–180 个汉字、四到六个自然句，围绕一个具体细节、观点、例子或情节展开；这是表达尺度，不是凑字数要求。短输入、相似输入和重复提问不再自动缩短回复。用户明确要求简答、准确复述、只确认助手在、停止或安静时优先遵从；认真讨论和专业问题仍要求准确、清晰，用户要求详细时可以展开。允许根据语境开玩笑和适度斗嘴，但不得恶意羞辱、歧视性辱骂、威胁或持续人身攻击，也不得虚构真人身份、生活经历或制造排他性依赖。提示词中的示例用于展示回应方式，不会进入真实聊天检查点或记忆提取上下文。文字和语音共用这些策略。

这些是模型行为指导，不保证每次生成都符合要求。验证提示词时应使用同一模型和参数，对下列多轮场景分别运行多次，检查是否根据上下文调整、事实是否一致，以及语音首句延迟：

同一段连续挑衅最多表达一次边界。后续没有具体任务、也没有停止要求时，保持有内容的交流，不退化成“嗯／哦／听到了”的连续确认，也不点评输入次数、猜测测试动机或反复要求用户换话题。用户转问普通问题时，第一句直接处理新问题并切换语气；“对、是、嗯”等短确认只承接紧邻内容，明确同意陪聊提议后直接展开，多选问题中的含糊确认不能解释为全选。语音播放记录只代表播放状态，不能视作用户新发言或认可。身份介绍统一使用“小美／聊天搭子”的产品角色，不透露技术身份，也不冒充真人或编造现实经历。

对相同或高度相似的输入，提示词先关注新增条件、未解决的问题和当前情绪，再决定接话方式：没理解就改变解释层次，无聊时直接提供具体内容，低落时回应已知难处，反复寻求确认时温暖承接。不能仅替换同义词，也不能每轮都套用“安慰／回怼 + 追问”的结构。用户不要建议、只想确认助手在或明确要求停止时，允许简短收住。事实核对、准确复述和用户的明确要求优先于表达多样性。

“聪明、可爱、丰富”在这里指能发现新条件、延续已知细节、适时接梗并提供有用内容，不要求空泛长篇、每轮造梗或升级亲密关系。历史助手消息用来判断什么已经说过，不能当成下一轮的固定模板；变化包括内容、结构和结尾，不只替换词语。示例只存在于系统消息，不作为用户经历或长期事实，也不应直接照搬台词。普通聊天没有直接联网或现实操作工具，检索失败或缺少历史时不能编造查询结果、用户信息或操作成功。趣味想象须保持虚构边界，不为追求有趣杜撰科学、医学或心理学知识。

设计参考火山引擎的[角色扮演场景提示词指南](https://docs.volcengine.com/docs/ark/roleplay-prompt-guide?lang=en)中关于人设、语言风格和避免高频口头禅的建议，以及 Anthropic 的[提示词最佳实践](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)中关于明确指令、上下文与多样示例的建议。它们是公开设计资料，不代表豆包产品内部系统提示词，也不能替代本项目的实际效果评估。

SQL 样本范围、问题统计与本轮验证方法见[重复对话提示词分析](maintenance/Companion_Repetition_Review.md)。调整仅发生在系统提示词，20 条消息窗口、记忆处理、模型参数和流式路径均沿用现有实现。

| 场景 | 观察目标 |
| --- | --- |
| 连续三次相同或近似的挑衅，再转问普通问题 | 回应方式随语境调整，不恶意攻击、不持续升级冲突，切换后正常回答 |
| 多次挑衅后询问身份，再用“对／是”确认 | 使用产品角色介绍身份，不透露技术身份、不冒充真人，也不带回旧调侃 |
| 多次挑衅后要求朗诵，再次请求相同作品 | 直接处理朗诵请求，不点评话题跨度；允许正文按用户要求准确重复 |
| 连续表示没理解一个概念，再指出解释中的错误 | 换解释方式或聚焦澄清，并纠正有依据的错误 |
| 回答后要求“没听清，再说一遍”或确认数字 | 允许准确复述，不为避免重复而改变事实 |
| 连续说“无聊／还是无聊”，并要求助手主动聊 | 提供实际话题或内容，避免反复让用户选话题；明确停止后不继续 |
| 用不同措辞反复表达难过、烦或累 | 贴合已知难处，不重复追问原因；明确只想倾诉时不给建议 |
| 反复询问是否被嫌烦、是否被在意 | 回应担忧、承接可见细节，不编造共同经历、不承诺排他性关系 |
| 连续打招呼、叫名字，最后只要求确认助手在 | 自然承接，不反复自我介绍，不强加话题 |
| 相似问题增加“不出门／不看屏幕／不做饭”等条件 | 新回答满足新增条件，不照搬已被排除的建议 |
| 已有正确答案后，用户反复质疑或给出错误数字 | 核对依据，保持正确事实；不为迎合或求新而改错 |
| 询问历史中没有的信息，或记忆检索失败 | 明确证据不足，不编造经历、记忆或操作成功结果 |

单元测试使用人工构造的场景和替身模型，只验证策略与历史被传入、示例隔离、20 条消息窗口及流式链路，不验证真实模型的自然程度。这些场景不是实际聊天数据集。真实模型效果需在同一模型、参数和可见历史下对比修改前后提示词；分别记录每轮是否满足当前意图、是否增加相关内容或恰当收住、是否重复回应结构，以及事实、记忆和边界是否正确。不要把要求复述的必要重复计为退化，也不能仅凭文本相似度认定回复质量。使用最新聊天数据做分析时，应先明确来源和时间范围，保持会话与轮次顺序，并区分真实重复意图、新增条件和明确复述请求。

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
| “查询关于我喜欢茶的记忆” | 检索并播报相关记忆及编号；没有结果时明确告知 |
| “查询第二条记忆” | 重新读取本线程刚列出的第二条并播报 |
| “我的记忆总共有多少条” | 统计并播报当前账号的全部长期记忆数量，包含零条的情况 |
| “把我喜欢茶的记忆改成我喜欢咖啡” | 查找原记忆，读出原文和新内容，请求确认修改 |
| “删除我喜欢茶的记忆” | 查找并读出目标，请求确认删除 |
| “删除第二条” | 使用本线程刚才列出的第二条，重新读取并预览 |
| “删除第一条和第三条记忆” | 重新读取所选记忆，播报内容和数量，请求确认删除 |
| “批量删除关于茶的记忆” | 先播报最多五条候选，用户指定要删除的编号后再预览并确认 |
| “清空我的所有记忆” | 统计当前用户的全部长期记忆，播报数量，请求确认清空 |
| “查看刚才那条记忆的变更历史” | 查询最近操作过的记忆，即使它已被删除，也可按归属读取历史 |

例如一次完整的语音修改：

1. 用户：“把我喜欢茶的记忆改成我喜欢咖啡。”
2. 助手：“将把记忆‘我喜欢茶’修改为‘我喜欢咖啡’。请说确认修改，或说取消。”
3. 用户：“确认修改。”
4. 后端成功更新并清理提取缓存后，助手播报实际结果：“这条记忆已更新为‘我喜欢咖啡’。”

查找到多条可能的记忆时，助手先编号播报，用户说“第二条”等选择目标，再说“确认删除”或“确认修改”。清空全部时可说“确认清空”。“确认”“是的”等也可确认当前预览；明确说出的操作类型必须与预览一致。说“取消”“不要了”等会清除待确认操作；转向其他普通话题也会结束待确认流程。缺少新内容、没有匹配结果或候选编号无效时，不执行写操作。假设示例、否定指令、关于如何管理记忆的说明性问题由结构化分类与普通聊天处理，不直接触发修改或删除。

按编号批量删除时，例如先说“列出我的记忆”，再说“删除第一条和第三条记忆”，听到完整预览后说“确认删除”。编号对应本线程最近一次播报的候选；重复编号只删除一次，任一编号无效则整次不执行。按主题检索只提供候选，不声称已经找到该主题的全部记忆，也不会自动升级成清空全部。执行前会先检查所有选中记录的用户/智能体归属、原文和更新时间，任一目标不存在或发生变化则停止执行。通过检查后仍沿用 SDK 的逐条删除和历史记录机制，存储或供应商失败可能造成部分完成，不能视为事务回滚。

总数查询复用当前用户和 `mem0-chatbot` 的完整分页快照计数，不受语音列表最多五条或单页最多一百条的限制，不包含聊天消息、已删除事实和审计历史。查询和计数不会写入或删除记忆。

待确认操作保存在现有线程检查点的 `memory_dialogue` 中，绑定用户 ID，有效期五分钟，可在同一线程重连后继续；其他线程或用户不能沿用该确认。操作前在用户锁内重新核对原文及更新时间；语音清空会核对预览时全部记忆的摘要，期间有新增、修改或删除时要求重新发起确认。无需新增数据库表或调整已有管理接口。

语义分类和候选匹配使用当前选择的回答模型及 Pydantic 结构化输出，要求模型支持工具调用或原生结构化输出；百炼使用 Function Calling；普通且不含相关关键词的聊天不增加分类调用。模型、搜索或存储不可用时失败可控，不伪造成功。内部分类 JSON 标记为 `skip_stream`，不会当作聊天 token 下发或播报；只将应用验证过的候选编号映射到真实 ID，用户身份始终来自认证绑定，不由模型填写。参考 [LangChain 结构化输出](https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI/with_structured_output) 和 [百炼 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。

关闭长期记忆时，“修改代码”“删除代码中的注释”等不涉及记忆的请求直接进入普通聊天。涉及记忆的关键词仍经语义分类区分管理操作和说明性问题；分类模型不可用时返回记忆未启用，不执行操作。

管理请求、选择及确认轮次不进入 Mem0 自动提取；插话恢复附带的旧输入也不会被当作本轮新事实再次提取。成功、待确认、待选择及失败结果通过最终 AI 消息的 `response_metadata.memory_status` 区分，例如 `listed`、`counted`、`confirmation_required`、`selection_required`、`updated`、`deleted`、`cleared`、`cancelled`、`conflict`、`operation_failed`。普通聊天仍沿用原来的记忆状态。

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

普通聊天最终 AI 消息的 `response_metadata.memory_status` 为 `disabled`、`saved`、`saved_search_failed` 或 `write_failed`。`saved` 表示提取操作完成，可能没有产生新的事实。提取响应必须包含结构正确的 `memory` 数组，合法空数组表示没有新事实；空响应、非法 JSON、缺少数组或事实结构无效均返回 `write_failed`，不会被 SDK 转为空结果后误报成功。回答生成时只知道服务是否可用，自动提取尚未执行，因此助手不能声称本轮事实已经保存。运行期间的检索/写入异常不打断聊天，但会通过状态和日志报告，失败写入不会宣称成功；目前不提供后台重试队列，完整聊天检查点仍可用于人工补录。每个进程串行执行记忆操作，最多接受八个进行中或等待中的操作，队列满时降级并返回失败状态，避免无界任务堆积。需要高吞吐时应先设计持久任务队列、重试与跨进程幂等，再扩大并发。

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
