# 项目技术架构与业务交互总览

核验日期：2026-10-27。适用对象：项目负责人、后端、Flutter 与测试工程师。本文描述源码中的现状，接口按当前工作区核验；配置默认值不代表线上已启用。

本系统的业务中心是 `mem0-chatbot`：Flutter 提供连续语音和个人资料库，FastAPI 执行认证、语音调度与 LangGraph，PostgreSQL 保存对话、长期记忆和播放状态，Nacos 管理配置、提示词与技能。日常聊天、联网研究、邮件、记忆管理和视频任务共用主对话入口，但执行和持久化路径不同。

## 阅读导航

| 文档 | 回答的问题 |
| --- | --- |
| 本文 | 有哪些项目、组件和调用关系？主要业务如何串起来？ |
| [Mem0 智能体、技能与记忆](Mem0_Agent_and_Memory.md) | 哪些是真正的子智能体？事实如何提取、召回、修改？对话如何保存？ |
| [语音全链路与延时](Voice_End_to_End.md) | 从用户说话到 ASR、首字、TTS 和实际播放，哪里串行、哪里并发、延时如何计算？ |
| [源码核验索引](Source_Map.md) | 跨项目证据、函数、测试和本次验证边界在哪里？ |

离线浏览可打开 [完整图文图册](Architecture_Atlas.html)，在一页切换四部分并放大SVG。图片、正文与可编辑图源随本目录保存。

飞书文档已通过CLI同步至AI知识库，分类入口见[Mem0与实时语音专题目录](https://k0ddd9qp4r.feishu.cn/wiki/OMg9wT217iHU1EkxWOScqkqNnJb)；以下正文均已回读检查：[技术架构总览](https://k0ddd9qp4r.feishu.cn/wiki/YjvywmuwQimb56kaYh1cpjIVncd)、[Mem0与记忆专篇](https://k0ddd9qp4r.feishu.cn/wiki/AV6kwW0vYif8v8kAzhicjedCn2c)、[语音与延时专篇](https://k0ddd9qp4r.feishu.cn/wiki/NcCMwglBAiRSmkkJp2qcs6OonEd)、[源码核验索引](https://k0ddd9qp4r.feishu.cn/wiki/WBckw7M1MiEH2rkd9apcosuYnQd)。

## 项目边界

| 项目 | 核验版本 | 职责与关系 |
| --- | --- | --- |
| `agent-service-toolkit` | `ec78ed3` + 当前工作区 | Python 3.12–3.14、FastAPI、LangGraph、Mem0、Streamlit；本系统业务后端 |
| `agent-voice-app` | `c43f095` + 当前工作区 | 独立 Flutter 客户端，包含 Dart 控制器、Android Kotlin 与 iOS Swift 音频桥；直接调用本后端 |
| `user-management` | `d7f6222` | 独立 FastAPI、SQLAlchemy、MySQL 用户/角色 CRUD 和模拟视频分析服务；核对入口、接口、持久化与任务实现后，未发现与本系统直接集成 |
| `user-management-web` | `83d79b9` | Vue 3、Vite、Element Plus；通过 `/api` 代理调用上项服务的 `/api/v1/users`，不承担实时语音 |

相邻目录中的 RAG、LangChain 与课程示例没有进入本次已核实的应用调用链。没有因为目录相邻而将它们画成依赖。主应用的账户实际存于 PostgreSQL `app_users`，不是相邻项目的 MySQL `t_user`；两套用户 ID 也没有已实现的映射。

现有工作区对 `docs/Mem0_Memory.md` 和延时报告已有修改。本次以当前内容作为文档证据，新增本目录交付，不覆盖原有修改，也不读取实际用户对话、运行日志或环境密钥。

## 总技术架构

![项目总技术架构](assets/01-system-architecture.png)

图源：[可编辑 SVG](assets/01-system-architecture.svg)。图中启用条件仍受 `Settings`、Nacos 和服务启动校验约束。

架构按五层理解：手机负责声音的采集与真实播放；传输层通过 HTTP 与双向 WebSocket 交换业务和音频；FastAPI 绑定身份、线程并调度执行；Mem0 主智能体选择对话或业务能力；数据和外部服务分别保存事实、执行模型或实际操作。Nacos 是启动时控制面，不是每个音频帧都会访问的中转站。

| 层 | 实现 | 关键边界 |
| --- | --- | --- |
| Flutter 终端 | `VoiceHomePage`、`VoiceSessionController`、原生 `AudioBridge` | 手机持有用户令牌；本地唤醒、AEC、停播与播放确认在终端执行 |
| Streamlit 入口 | `streamlit_app.py`、`VoiceManager`、`AgentClient` | 录音文件 HTTP ASR，SSE 展示回答，结束后整段 TTS；不走移动实时语音 WebSocket |
| API 与安全 | FastAPI、`access.py`、`voice.py` | Bearer、JWT 用户绑定、线程归属、运行锁；管理服务凭据与 App 用户令牌有不同权限 |
| 共享执行 | `_handle_input`、`graph_events` | SSE 与实时语音共用 Agent 事件层；AG-UI 使用官方独立适配器，共用身份与线程协调 |
| 主对话 | `mem0_chatbot`、`answer_with_research` | 主模型维持会话；研究子图、邮件、视频和记忆管理各有受控边界 |
| 持久化 | PostgreSQL、pgvector、Redis、七牛 | 对话与记忆分开；Redis 负责验证码、限额和邮件预约，不保存 LangGraph 根对话 |
| 模型与工具 | 百炼 ASR/LLM/TTS、DDGS、SMTP、HappyHorse/Wan | 模型、搜索、发送邮件和视频生成是不同调用，失败与完成含义分别处理 |
| 配置与观测 | Nacos、模型/语音计时、可选 Langfuse/LangSmith | 模板和技能在启动时加载；启用追踪不等于已具备完整声学延时统计 |

证据：`src/service/service.py::lifespan`、`src/service/agent_runner.py::graph_events`、`src/service/agui.py::_event_stream`、`src/core/settings.py`；Flutter `lib/features/voice_session/` 和 `lib/core/audio/`。

## 从登录到连续对话

1. 用户在 Flutter 输入邮箱；App 调用 `/auth/email/code`，服务用 Redis 管理验证码和频率，通过 SMTP 发送验证码。
2. `/auth/email/verify` 校验后查询或创建 `app_users`，签发带 `sub/exp/iat/jti` 的 HS256 App 令牌。Android Keystore / iOS Keychain 保存客户端会话。
3. App 查询 `/voice/capabilities`，读取协议、音色、默认智能体、唤醒和端侧 VAD 参数。当前返回的默认智能体是 `mem0-chatbot`。
4. 用户点击开始，或端侧 Sherpa ONNX 检测到唤醒词后，App 创建语音会话、连接 WebSocket、发送 `session.configure`。
5. 服务端 ASR 连接就绪后发送 `session.ready`，终端开始持续上传 PCM。中间转写用于字幕，最终有效转写才触发一轮 Agent。
6. Agent 的主回答文字流进入 UI 和分段 TTS；首包音频可以早于全文生成和记忆提取完成。客户端按响应 ID 播放并确认实际播完的分段。
7. 结束语音关闭传输与音频资源，线程仍保留；下一次可通过历史页继续同一线程。新线程可以召回同一用户的长期记忆。

重要默认差异：`/voice/capabilities.default_agent` 是 `mem0-chatbot`，但 `VoiceSessionInput.agent_id` 的 schema 默认仍为 `chatbot`。Flutter 明确提交所选 agent，所以正常 App 路径使用选定值；自行调用创建接口时应显式传 `agent_id`，不能假定省略字段与能力接口一致。

## Mem0 主业务路由

![Mem0 业务交互流程](assets/02-mem0-business-flow.png)

图源：[可编辑 SVG](assets/02-mem0-business-flow.svg)。完整机制见 [Mem0 专篇](Mem0_Agent_and_Memory.md)。

入口先处理邮件，再处理记忆管理。未被拦截时才检索长期事实并调用主模型；主模型可以直接回复、选择研究工具，或者按需加载视频技能。邮件、记忆管理、带 `video_status` 的视频分支提前返回；日常聊天和研究回答随后进入长期事实提取。

| 场景 | 主要交互 | 用户看到的结果 | 后续工作 |
| --- | --- | --- | --- |
| 日常聊天 | 召回个人记忆 → 主模型 | 文字增量、语音分段 | 提取最新用户消息中的事实，保存根对话 |
| 联网查证 | 主模型委派 → WebSearch → 子图摘要 → 主模型最终回答 | 主助手的回答与真实来源 | 研究内部内容隐藏；不把网页当作用户事实 |
| 记忆管理 | 意图 → 候选匹配 → 选择/预览/确认 → 更新 | 列表、数量、历史或变更结果 | 修改后清理提取参考，保留聊天和变更历史 |
| 发邮件 | 邮件线索 → `EmailIntent` → 当前用户邮箱 → Redis/SMTP | 草稿追问、已预约、已提交或结果不确定 | 预约队列由后台按时处理；已提交不支持口头撤回 |
| 视频生成/查询 | 加载技能 → 同一主模型 `video_operation` → `VideoService` | 任务、列表、数量卡片及可播报摘要 | 异步生成、状态查询、七牛转存与实际封面生成 |
| 回看对话 | 日期/线程列表 → 根检查点还原 | 历史消息和视频卡片 | 对话查询不等于长期记忆检索 |

注册表中其余 `research-assistant`、`rag-assistant`、两个 supervisor、GitHub MCP 等是可选择的兄弟智能体，不是 Mem0 自动调用的子智能体。两个 supervisor 的示例搜索函数返回固定文本，也不能画成 Mem0 的真实联网研究路径。

## 数据如何分工

| 数据 | 存储/范围 | 用途及生命周期 |
| --- | --- | --- |
| 完整线程对话 | LangGraph PostgreSQL checkpoints；线程与 Agent/用户元数据 | 恢复历史；Mem0 functional state 存在 `__previous__`；当前模型仅取最近 20 条 Human/AI |
| 长期用户事实 | 默认 `mem0_chat` pgvector collection；`user_id + agent_id` | 跨线程召回；`source_thread_id` 是来源元数据，不是检索隔离条件 |
| 实体关联 | SDK 按需建立的实体 collection | 为 SDK 检索提供实体增强；不是独立业务知识图谱服务 |
| 提取参考/记忆历史 | `mem0_records.kind=context/history` | context 按线程区分并保留最多 10 条有效记录；history 记录 ADD/UPDATE/DELETE |
| 语音会话与播放 | `voice_sessions`、`voice_turns` | 输入去重、生成文本、分段、已播放采样数、取消状态；未看到原始 PCM 落库路径 |
| 所有权与运行 | `app_thread_owners`、`app_runs` | 线程归属和反馈 run 授权 |
| 登录账户 | `app_users` | 邮箱身份、昵称、头像及审计字段 |
| 邮件临时任务 | Redis | 验证码、请求去重、发送额度、预约队列及到期清理 |
| 视频/文件 | PostgreSQL 业务记录 + 七牛对象 | 业务状态与文件实体分离；生成成功和转存成功分别表达 |
| 通用 Store / RAG | AsyncPostgresStore / `rag_documents` | 为其他能力提供存储；Mem0 自己的长期事实不通过通用 Store 写入 |

“最近 30 天聊天记录”是查询窗口，不是数据只保留 30 天的删除策略。代码没有据此自动删除旧检查点。删除长期记忆也不会清空根对话或变更历史；历史中仍有原始事实，所以不能承诺用户信息已彻底擦除。

## 部署与实际启用条件

Compose 包含 PostgreSQL（带 pgvector）、Redis、Agent 服务和 Streamlit。Nacos、SMTP、百炼及七牛是配置或外部依赖；可选 Langfuse/LangSmith 不应画成必经业务节点。容器服务默认 8080、Streamlit 8501；Linux Nginx 模板的 API 上游是 8000，App 的打包地址又可覆盖默认值，实际地址以部署配置为准。

启动顺序为 Nacos 普通配置 → 六条 Prompt → 已启用 Skill → PostgreSQL saver/store → 注册图配置 → Mem0、账户、邮件、语音、视频和文件生命周期 → 服务发现注册。涉及具体子服务的初始化次序以 `lifespan` 嵌套为准；不允许用 SQLite 或内存替代失败的生产持久化。

六条 Mem0 Prompt 在远端统一校验后安装，没有本地正文回退。即便 `MEM0_ENABLED=false`，`mem0-chatbot` 的聊天角色仍依赖 Prompt；关闭长期记忆不等于关闭模板依赖。Skill ZIP 只读取受限的 `SKILL.md`，不会执行下载脚本。

## 当前最值得关注的边界

| 发现 | 对业务/验收的影响 |
| --- | --- |
| 暂无停说到实际首声的闭环测量 | 不能把服务端首音频指标当作用户听到声音的时间；详见语音专篇 |
| Mem0 单工作线程、最多 8 个 pending，按用户加 advisory lock | 避免阻塞事件循环，但繁忙用户或慢提取会影响后续召回排队 |
| 记忆提取被等待，流式播报可以先行 | 首字、首声和 response.done 是不同体验指标 |
| 语音 capability 与创建 schema 的默认 Agent 不同 | 自定义客户端应始终显式提交 agent |
| 会话数限制的注释/错误文案比 SQL 范围宽 | `active_sessions()` 只统计 `created/connected`，而初建状态为 `ready`；“每用户最多两个未过期会话”不能作为所有状态下的严格保证 |
| 手机原生播放队列未看到时长硬上限 | 后端有界队列不能证明端侧永不积压；弱网和慢播放需独立观察 |
| 旧规划仍写默认 `research-assistant` | 新图和本文遵循当前注册表；历史计划不作为运行现状 |

这些是本次源码审阅结论，不表示本次执行了生产压测或故障修复。未访问线上配置、真实用户数据或付费模型；测试文件作为设计与边界的交叉证据，验证结果见 [源码核验索引](Source_Map.md)。
