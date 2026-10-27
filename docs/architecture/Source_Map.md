# 架构文档源码核验索引

核验日期：2026-10-27。本文记录本次跨项目阅读范围与可复核入口。源码优先于历史计划；表格中的测试是已阅读的交叉证据，不代表本次重新执行了全部测试。

## 项目和证据层级

| 项目 | 基线 | 阅读范围 | 纳入结论 |
| --- | --- | --- | --- |
| agent-service-toolkit | ec78ed3 + 当前工作区 | 主Agent、注册、研究、全部Mem0适配层、记忆管理、技能、服务/协议/配置、Streamlit、实时音频、部署及相关测试 | 主系统 |
| agent-voice-app | c43f095 + 当前工作区 | App入口、登录/配置、语音首页相关交互、完整会话控制器、协议/API、KWS、Android/iOS原生音频、记忆和历史页、视频卡片及相关测试 | 实时客户端 |
| user-management | d7f6222 | 项目约定、FastAPI入口、配置/数据库结构、用户API/模型、视频上报/worker | 独立系统；没有发现主系统调用关系 |
| user-management-web | 83d79b9 | 包依赖、Vue入口/用户页面结构、Axios API、Vite代理 | 只连接上项CRUD服务；没有实时语音 |

第一级证据是当前实现和固定依赖源码；第二级是schema与相关测试；第三级是仓库文档的设计和历史验证记录。没有访问实际Nacos配置、Prompt正文、环境密钥、真实用户数据或运行日志。

## 后端：主入口与Mem0

| 文件 | 关键对象 | 支撑结论 |
| --- | --- | --- |
| [注册表](../../src/agents/agents.py) | DEFAULT_AGENT、STREAMING_CHAT_AGENTS、agents | 默认mem0-chatbot；兄弟图不等于子图 |
| [Mem0主Agent](../../src/agents/mem0_chatbot.py) | mem0_chatbot、_chat_prompt、_extraction_context | 路由优先级、20条窗口、提取来源与提前返回 |
| [研究委派](../../src/agents/research_delegation.py) | answer_with_research、run_research | 一次研究、独立预算、主模型最终回答 |
| [研究图](../../src/agents/research_assistant.py) | research_subagent、research_assistant | 受限子图与独立Agent的工具不同 |
| [搜索适配](../../src/agents/web_search.py) | BoundedWebSearch | 同步请求的超时与容量 |
| [安全检查](../../src/agents/safeguard.py) | Safeguard、SafetyAssessment | 有无Groq配置的实际分支 |
| [记忆命令](../../src/agents/memory_commands.py) | _handle、_confirm、handle_memory_command | 意图、候选、预览、五分钟确认、并发冲突 |
| [Mem0服务](../../src/memory/mem0_service.py) | Mem0Service、mem0_lifespan | 单线程、有界pending、召回/记忆/管理及资源释放 |
| [SDK桥接](../../src/memory/mem0_sdk.py) | build_memory、ValidatedExtractionLlm | PostgreSQL历史注入、提取系统消息、JSON校验 |
| [历史适配](../../src/memory/mem0_history.py) | PostgresHistory | owner、context线程范围、advisory lock、history |
| [重排](../../src/memory/mem0_rerank.py) | Mem0Reranker | 候选校验、重排与失败处理 |
| [PostgreSQL](../../src/memory/postgres.py) | get_postgres_saver/store | 必需持久化和连接池 |
| [配置](../../src/core/settings.py) | Settings | 功能开关、默认值与Prompt属性 |
| [模型工厂](../../src/core/llm.py) | get_model | 模型缓存和thinking参数适用范围 |
| [提示布局](../../src/core/chat_context.py) | prepare_chat_prompt | 固定规则、运行上下文、可选显式缓存 |
| [模型计时](../../src/core/model_metrics.py) | ModelTiming、invoke_chat_model | 工具首输出、正文首输出和usage计时不同 |
| [Prompt契约](../../src/schema/prompts.py) | Mem0Prompts、Mem0PromptKeys | 六个远端模板与status占位校验 |

## 技能和外围能力

| 文件 | 核验用途 |
| --- | --- |
| [邮件命令](../../src/agents/email_commands.py) | 正则门控、EmailIntent、草稿、当前用户限制与状态回复 |
| [邮件运行时](../../src/skills/email.py) | Redis原子去重/额度、预约队列、SMTP状态 |
| [邮件HTTP与生命周期](../../src/service/email_skill.py) | 需要真实App用户、认证服务与加密SMTP |
| [邮件Skill参考](../../skills/send-current-user-email/SKILL.md) | 受控邮件工作流与失败语义 |
| [视频按需加载](../../src/skills/video_chat.py) | 同一主模型load/operation两阶段，无独立分类Agent |
| [视频Skill执行](../../src/skills/video.py) | 参数/身份/幂等、草稿、列表、数量和custom事件 |
| [视频Skill参考](../../skills/manage-user-videos/SKILL.md) | 生成/查询/编辑能力与素材限制 |
| [视频服务](../../src/video/service.py) | 异步任务、状态刷新与300秒后台同步周期 |
| [视频Provider](../../src/video/providers.py) | 百炼异步HTTP、七牛转存/封面 |
| [视频接口](../../src/service/video.py) | 用户令牌、任务CRUD和生命周期 |
| [Skill解析](../../src/core/skills.py) | ZIP限制、名称/路径校验、只读SKILL.md |
| [Nacos集成](../../src/service/nacos.py) | 普通配置、六Prompt、Skill下载、服务发现与关闭 |
| [邮箱登录](../../src/service/email_auth.py) | app_users、验证码、SMTP、JWT签发 |
| [用户schema](../../src/service/sql/create_app_users.sql) | PostgreSQL账户，与相邻MySQL项目区分 |
| [文件服务](../../src/service/file_uploads.py) | 上传元数据、七牛对象与用户绑定 |

同时核对 `chatbot.py`、两个 `langgraph_supervisor_*`、`rag_assistant.py`、`knowledge_base_agent.py`、GitHub MCP图及 `src/rag/`：这些是扩展/兄弟能力，未把它们错误连入Mem0的每轮必经链路。

## API、聊天和语音后端

| 文件 | 关键对象 | 支撑结论 |
| --- | --- | --- |
| [服务入口](../../src/service/service.py) | lifespan、_handle_input、message_generator、history | 初始化、用户配置保留键、SSE与历史查询 |
| [共享运行层](../../src/service/agent_runner.py) | graph_events、delivery_context、thread_guard | 子图隐藏、事件、已听内容与线程协调 |
| [访问控制](../../src/service/access.py) | authenticate、bind_user、authorize_thread | 用户/管理身份和线程/run所有权 |
| [AG-UI](../../src/service/agui.py) | _event_stream | 官方独立适配，非graph_events的SSE序列化 |
| [线程列表](../../src/service/threads.py) | list_user_threads | checkpoint派生、扫描上限 |
| [日期记录](../../src/service/chat_records.py) | list_chat_record_days、get_chat_record_detail | UTC30日窗口、当天消息增量 |
| [记忆接口](../../src/service/memories.py) | memory_access、PUT/DELETE/history | ownership和超时；REST不共用口头预览 |
| [语音接口](../../src/service/voice.py) | capabilities、create_session、voice_websocket | 协议、默认差异、会话/连接容量与身份 |
| [完整语音会话](../../src/voice/session.py) | submit、recognize、speak、generate、execute、sender、run | 并发、计时、取消、背压和清理 |
| [断句](../../src/voice/speech_output.py) | SpeechText | 24/160字符与180/400ms刷出策略 |
| [实时Provider](../../src/voice/providers/alibaba_realtime.py) | recognize、synthesizer、synthesize | 上游连接、首PCM期限与协议 |
| [任务协议Provider](../../src/voice/providers/alibaba_inference.py) | InferenceStream | 事件翻译、manual/server_vad、每段任务 |
| [音色目录](../../src/voice/catalog.py) | speech_protocol、voice_options | 模型/协议/音色绑定 |
| [唤醒确认](../../src/voice/wake_word.py) | match_wake_prefix | 重复和分段唤醒前缀 |
| [语音持久化](../../src/voice/persistence.py) | VoiceRepository | turn、播放状态、guard与active_sessions实际SQL |
| [语音schema](../../src/schema/voice.py) | VoiceSessionInput、VoiceTurn、encode/decode_audio | 默认agent、事件、45字节帧、状态 |
| [Streamlit](../../src/streamlit_app.py) | main、draw_messages | 录音输入与整轮后TTS |
| [Streamlit语音桥](../../src/voice/manager.py) | VoiceManager | 文件识别、同步合成、last_audio |
| [文件ASR](../../src/voice/providers/alibaba_stt.py) / [整段TTS](../../src/voice/providers/alibaba_tts.py) | AlibabaSTT、AlibabaTTS | 不同于实时Provider；文件/长度/下载路径 |
| [Python客户端](../../src/client/client.py) | AgentClient、_parse_stream_line | SSE解析与REST历史/记忆访问 |

## Flutter与原生端

以下路径位于相邻 `agent-voice-app` 仓库，链接按本地目录关系提供；飞书版用文字路径便于检索。

| 文件/目录 | 关键对象 | 核验结论 |
| --- | --- | --- |
| [App入口](../../../agent-voice-app/lib/app.dart) | AgentVoiceApp | 登录恢复、配置、退出与控制器生命周期 |
| [语音控制器](../../../agent-voice-app/lib/features/voice_session/voice_session_controller.dart) | VoiceSessionController完整实现 | 冷启/KWS/预滚、事件、首包计时、语音/视频、取消与重连 |
| [语音首页](../../../agent-voice-app/lib/features/voice_session/voice_home_page.dart) | 生命周期、默认唤醒、历史/视频导航、按钮回调 | UI动作实际触发的控制器逻辑 |
| [原生桥](../../../agent-voice-app/lib/core/audio/audio_bridge.dart) | AudioBridge、NativeAudioBridge | MethodChannel与两种EventChannel |
| [唤醒引擎](../../../agent-voice-app/lib/core/audio/wake_word_detector.dart) | SherpaWakeWordDetector、isolate worker | 模型素材、16k输入、离线检测与reset |
| [音频协议](../../../agent-voice-app/lib/core/api/voice_protocol.dart) | encodeInputAudioFrame、decodeOutputAudioFrame | 与Python协议对应 |
| [WebSocket](../../../agent-voice-app/lib/core/api/voice_socket.dart) | VoiceSocket | 有序混合事件流、鉴权headers、连接超时 |
| [API客户端](../../../agent-voice-app/lib/core/api/api_client.dart) | AgentApiClient | 会话、历史、记忆、用户、上传与统一响应 |
| [客户端模型](../../../agent-voice-app/lib/core/api/api_models.dart) | VoiceCapabilities、ChatItem | 能力默认和历史metadata解释 |
| [配置](../../../agent-voice-app/lib/core/config/app_config.dart) | AppConfig | 打包/运行配置覆盖，默认地址非部署事实 |
| [Android完整实现](../../../agent-voice-app/android/app/src/main/kotlin/com/coderpwh/agent_voice_app/MainActivity.kt) | VoiceAudioEngine | 40ms采集、AEC、AudioTrack、真实进度、路由恢复 |
| [iOS完整实现](../../../agent-voice-app/ios/Runner/VoiceAudioEngine.swift) | IOSVoiceAudioEngine | 硬件率重采样、voice processing、dataPlayedBack |
| [长期记忆页面](../../../agent-voice-app/lib/features/memories/) | MemoryFeed、MemoryDetailPage、History/Edit | 分页、操作确认、写入不确定后刷新 |
| [聊天记录页面](../../../agent-voice-app/lib/features/conversations/) | ChatRecordsPage、ChatRecordDetailPage | 日期聚合、线程恢复与继续聊天 |
| [视频引用](../../../agent-voice-app/lib/core/api/video_reference.dart) / [消息卡片](../../../agent-voice-app/lib/features/videos/video_message_card.dart) | VideoReference、卡片 | custom事件和历史回复映射到同一业务字段 |

## 固定Mem0依赖的二次核验

本机安装路径为 `.venv/lib/python3.13/site-packages/mem0/`。这是版本实现证据，不要求把依赖源码提交进仓库。

| 源码 | 位置 | 已核验算法 |
| --- | --- | --- |
| `memory/main.py` | `_add_to_vector_store`，约881行 | 已有事实检索、单次提取、批量embedding、hash去重、INSERT/ADD历史 |
| `memory/main.py` | 约1099–1210行 | 实体关联的批量嵌入/链接及提取参考保存 |
| `memory/main.py` | `_search_vector_store`，约1642行 | 语义/关键词候选、归一化与实体增强综合排序 |
| `vector_stores/pgvector.py` | `create_col/search/keyword_search/delete/update` | UUID第三方schema、cosine距离、ts_rank_cd全文检索和实际事实删除 |

行号只适用于当前固定安装版本；正文以函数名和算法为稳定引用。

## 测试交叉证据

| 测试 | 重点检查 |
| --- | --- |
| `tests/agents/test_mem0_chatbot.py` | 窗口20条/完整checkpoint、当前user提取、参考预算、清空边界、流式和thinking |
| `tests/memory/test_mem0_sdk.py` | 真SDK加替身Provider，Prompt接入、禁止SQLite、空提取/非法结构、更新删除历史 |
| `tests/memory/test_mem0_history.py` | 用户锁、owner、线程context与逻辑清理 |
| `tests/memory/test_mem0_service.py` | 作用域、重排降级、取消后容量与生命周期 |
| `tests/agents/test_research_delegation.py` | 子图隔离、一次委派、来源、超时与取消 |
| `tests/voice/test_realtime_service.py` | 首PCM早于最后正文、延时字段、背压、未发送段重试、插话、业务确认、研究/记忆/邮件/视频 |
| `tests/voice/test_speech_output.py` | 24字符、定时flush、积压合并、代码/标点清洗 |
| `tests/voice/providers/test_alibaba_realtime.py` | 首PCM期限不被控制ack重置 |
| `tests/service/test_chat_records.py` | UTC窗口、当天增量、用户隔离 |
| Flutter `test/features/voice_session_controller_test.dart` | 原生入队顺序、首字/首包计时、实际播放完成、自动提示与手动恢复 |
| Flutter `integration_test/audio_playback_test.dart` | 原生播放检查入口；不把源码存在当作本次真机执行 |

## 本次验证与未验证范围

本次只新增文档和图表，无运行时代码/数据库/依赖变化。检查项目包括Markdown规则、链接目标、SVG XML与自包含性、五张PNG的尺寸和逐图视觉检查、飞书草稿解析和发布后回读。实际执行结果在交付记录中维护；若有失败会明确列出。

| 本次执行项 | 结果 |
| --- | --- |
| `uv run --frozen pymarkdown scan`：AGENTS.md与四份Markdown | 通过 |
| 本地文档/资源链接检查 | 通过；相邻仓库源码链接需要保留本地目录关系 |
| 五张SVG的XML、viewBox和自包含检查 | 通过 |
| 五张PNG逐图视觉检查 | 通过；1600像素宽、1370–1580像素高，无裁切或文字遮挡 |
| 四份飞书XML `docs +script --command parse` | 全部 `assessment.status=passed` |
| 飞书用户身份发布 | 完成CLI用户授权，已创建四份文档 |
| 飞书正文与资源回读 | 四份全文回读成功；总览、Mem0和语音专篇各有两个画板 |
| 飞书画板预览 | 六个画板均成功导出并逐图检查；共五张独立图，业务流程图在两篇复用 |

没有重新执行后端和Flutter功能测试、Docker冒烟、真实ASR/LLM/TTS、设备声学测试、邮件或视频副作用。没有测出本次新的端到端延时数据；历史1.376秒HTTP均值来自已有三次合成样本报告，不能解释为语音SLA。

没有把缺少实测数据解释为链路代码未实现，也没有用测试中的模拟时间冒充百炼或真机延时。
