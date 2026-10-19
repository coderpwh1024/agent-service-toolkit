# 移动端实时语音 API

这套接口供独立 Flutter App 连接当前 FastAPI 后端。App 负责麦克风、AEC、本地唤醒、播放与界面；后端负责用户隔离、百炼实时 ASR/TTS、Agent、工具、会话和播放记录。协议版本为 `1`。

默认 `mem0-chatbot` 和 `chatbot` 支持通过语音调用 [当前用户邮件发送技能](Email_Skill.md)。在 Nacos 发布并启用该技能后，登录用户可说“给我发邮件，主题是会议提醒，正文是明天下午三点开会”。邮件只发送至登录用户的注册邮箱，沿用现有语音事件与输入去重机制，最终语音反馈以实际投递状态为准。

## 启用服务

语音配置统一维护在 Nacos 的 `agent-service-toolkit.yaml` 中，至少配置：

```yaml
models:
  dashscope:
    api_key: replace-with-bailian-api-key

authentication:
  admin_secret: replace-with-a-long-random-service-secret
  app_token:
    secret: replace-with-at-least-32-random-characters
    ttl_seconds: 1296000

voice:
  enabled: true
  llm:
    enable_thinking: false
  wake:
    enabled: true
    word: 小美小美
    confirm_with_asr: true
    pre_roll_ms: 2200
    confirm_seconds: 10
    greeting: 我在，小美随时为你服务。
    kws_score: 1.0
    kws_threshold: 0.5
  vad:
    client_rms_dbfs: -42
    client_frames: 3
  audio_metrics_seconds: 5
  realtime:
    stt_model: qwen3-asr-flash-realtime
    tts_model: qwen3-tts-flash-realtime
    voices:
      - id: Cherry
        name: 芊悦
        description: 阳光积极、亲切自然
      - id: Serena
        name: 苏瑶
        description: 温柔自然
      - id: Ethan
        name: 晨煦
        description: 阳光、温暖、活力
      - id: Maia
        name: 四月
        description: 知性温柔
      - id: Kai
        name: 凯
        description: 舒缓自然
      - id: Neil
        name: 阿闻
        description: 字正腔圆的新闻主持风格
```

以上候选均来自百炼当前的 [Qwen-TTS 实时音色列表](https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list)。音色偏好具有主观性，上线前仍应在目标设备和实际文案中试听。

PostgreSQL 是必需依赖。服务启动时会幂等创建 `app_thread_owners`、`app_runs`、`voice_sessions` 和 `voice_turns`。生产环境使用 HTTPS/WSS，并让反向代理允许 WebSocket Upgrade，空闲超时需大于 `VOICE_IDLE_SECONDS`。

百炼上游默认直连，不继承操作系统代理。确实需要 HTTP 代理时设置 `voice.realtime.proxy`；SOCKS 代理还需在部署镜像中安装 `websockets` 所需的 SOCKS 可选依赖。完整语音配置维护在 Nacos 的 `agent-service-toolkit.yaml` 中。

普通语音聊天 `chatbot`、`mem0-chatbot` 在千问模型上默认显式关闭思考，以减少日常对话的首字等待。Nacos 的 `voice.llm.enable_thinking` 对应 `VOICE_LLM_ENABLE_THINKING`，默认 `false`，需要深度推理时可设为 `true` 并重启服务。该选项只改变这两个语音聊天 Agent 的千问请求参数，不修改全局模型默认值，不改变普通文字入口或复杂工具 Agent 的现有行为，其他供应商不会收到百炼扩展字段。思考模式的质量与延时取舍参见[百炼官方说明](https://help.aliyun.com/zh/model-studio/deep-thinking/)。

移动 App 通过 `POST /auth/email/code` 和 `POST /auth/email/verify` 完成邮箱验证码登录，详见[账号与认证说明](Accounts_and_Credentials.md)。验证成功后，用户令牌位于统一响应的 `data.access_token`，默认有效期为 15 天。兼容的可信后台也可以配置 `AUTH_SECRET`，在自行验证用户后调用：

```http
POST /auth/token
Authorization: Bearer <AUTH_SECRET>
Content-Type: application/json

{"user_id":"user-123"}
```

返回的 `data.access_token` 是带 `sub`、`exp`、`aud` 和 `iss` 的 HS256 JWT，`data.expires_at` 是过期时间的 Unix 秒数。Flutter 后续 HTTP 和 WebSocket 握手都使用 `Authorization: Bearer <access_token>`。Token 用户不能读取其他用户的线程、语音会话、运行或反馈记录。已有令牌不会因配置变更自动延长；更新配置并重启服务后，用户下次登录获取的新令牌才有 15 天有效期。

## 模型升级与音色绑定

后端按模型家族选择百炼的两套流式协议，再转换成本文的统一协议。App 始终上传 16 kHz 单声道 PCM16、播放 24 kHz 单声道 PCM16，无需随上游模型版本修改音频帧。

| 模型家族 | 上游协议 | 音色配置 |
| --- | --- | --- |
| `qwen3-asr-flash-realtime` 及日期快照 | Realtime：`session.update`、Base64 音频 | 不适用 |
| `qwen-audio-<版本>-asr-flash-message/streaming` | Inference：`run-task`、二进制音频 | 不适用 |
| `fun-asr-realtime`、16 kHz `paraformer-realtime` | Inference | 不适用 |
| `qwen3-tts-*-realtime`、`qwen-tts-realtime` 及日期快照 | Realtime：文本缓冲、Base64 PCM | Flash 系统音色兼容原有 `voices`；VC/VD 必须绑定模型专属目录 |
| `qwen-audio-3.0-tts-plus/flash`、`qwen-audio-3.1-tts-flash` | Inference：文本任务、二进制 PCM | 按模型选择内置系统音色，或使用模型专属目录 |
| `cosyvoice-*` | Inference | 必须提供模型专属音色目录 |

协议依据：[WebSocket 协议总览](https://help.aliyun.com/zh/model-studio/realtime-websocket-overview)、[ASR Message 客户端事件](https://help.aliyun.com/zh/model-studio/qwen-asr-message-client-events)、[ASR Message 服务端事件](https://help.aliyun.com/zh/model-studio/qwen-asr-message-server-events)、[TTS 客户端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-client-events)。音色依据：[Qwen Audio TTS 官方列表](https://help.aliyun.com/en/model-studio/qwen-audio-tts-voice-list)。内置目录是其中的系统音色子集，不是账号音色发现接口。

例如使用 Qwen Audio 3.1 时，在已有 Nacos YAML 中修改以下节点即可，保留其他认证、存储配置：

```yaml
voice:
  enabled: true
  realtime:
    url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime
    stt_model: qwen-audio-3.1-asr-flash-message
    tts_model: qwen-audio-3.1-tts-flash
    stt_protocol: auto
    tts_protocol: auto
```

`auto` 会保留配置地址中的地域和 Workspace 域名，将路径切换为所选协议的 `/api-ws/v1/realtime` 或 `/api-ws/v1/inference`。不会跨地域回退。`stt_url`、`tts_url` 可分别指定完整 WSS 地址，适合专有网关；不允许 URL 内嵌凭据、查询串或 fragment，认证仍使用 `models.dashscope.api_key`。

Qwen Audio 3.1 默认使用 `longanhuan_v3.1`，并公布其他匹配音色；不会把旧版的 `Cherry`、`Serena` 当成新模型的音色。需要定制或使用其他模型时，为模型配置明确的目录：

```yaml
voice:
  realtime:
    voice_catalog:
      qwen-audio-3.1-tts-flash:
        - id: longanlingxin_v3.1
          name: 龙安灵心
          description: 多语言与方言女声
          languages: [zh, en]
```

查找顺序为：`voice_catalog` 中精确匹配当前模型的目录、`voices_model` 与当前模型相同时绑定的旧 `voices`、已知家族的内置目录。原有 Qwen Realtime 配置未指定 `voices_model` 时继续使用 `voices`，保持兼容。模型专属目录的 ID、支持语言及地域必须由部署者按供应商文档核实；复刻、设计音色也须绑定其实际目标模型。目录不会从旧模型自动迁移。每条音色的 `languages` 可配置 `zh`、`en`，默认两者；服务在创建会话时拒绝不支持的明确语言。

同协议的快照升级无需改 App。新家族无法自动识别时，可显式设置 `stt_protocol` 或 `tts_protocol` 为 `realtime` / `inference`，同时提供正确地址和模型专属音色；这仅适用于事件结构兼容的流式模型。HTTP 文件转写、离线合成、8 kHz 专用识别、全模态对话或未来全新协议不能仅靠模型名兼容，需实现对应的转换后才能使用。未知家族或缺少目录会在启动校验时明确失败。

Inference 识别按任务和句子标识归一化：中间结果替换显示，最终结果去重；手动模式聚合本次任务的句子，仅在提交完成时产生一次最终输入，然后启动下一任务。Inference TTS 为每个语音段创建独立任务并流式转发 PCM，保留首包超时、取消、回压和连接关闭行为。供应商错误不会被当成空转录或合成成功。

`GET /voice/capabilities` 保留 `voices`、`default_voice`、`voice_options`，并增加 `voice_catalog_model`、音色 `languages` 和 `turn_detection_modes`。Flutter 每次建立会话前重新读取能力，旧音色失效时选用当前目录的默认音色；中文聊天只显示支持中文的音色，并校验目录模型和音频格式。已存储会话的音色若不属于当前模型，WebSocket 返回 4409，客户端需新建会话。

配置仍在服务启动时从 Nacos 加载，修改模型或目录后需重启后端，再在 App 中重连。关闭 Nacos 时，上述新增字段分别对应 `VOICE_REALTIME_STT_PROTOCOL`、`VOICE_REALTIME_TTS_PROTOCOL`、`VOICE_REALTIME_STT_URL`、`VOICE_REALTIME_TTS_URL`、`VOICE_REALTIME_VOICES_MODEL`、`VOICE_REALTIME_VOICE_CATALOG`，目录环境变量使用 JSON。

## HTTP 接口

| 方法与路径 | 用途 |
| --- | --- |
| `GET /voice/capabilities` | 获取协议、采样格式、模型、音色和 Agent 语音能力 |
| `GET /voice/protocol` | 获取 App → 后端控制事件的 JSON Schema |
| `POST /voice/sessions` | 创建语音会话，可绑定已有 `thread_id` |
| `GET /voice/sessions/{session_id}` | 查询会话及重连状态 |
| `GET /voice/sessions/{session_id}/turns` | 查询该语音会话的轮次和播放记录 |
| `DELETE /voice/sessions/{session_id}` | 幂等关闭会话，不删除聊天历史 |

这些 HTTP 接口统一返回 `code`、`message`、`data`，成功时 HTTP 状态码和 `code` 均为 200。后续示例中的业务字段均位于 `data`。WebSocket 消息是独立事件协议，不使用该封装。

`GET /voice/capabilities` 的 `voices` 保留为音色 ID 字符串数组，以兼容已有客户端；`default_voice` 是 Nacos 列表中的第一项，`voice_options` 提供 `id`、`name` 和 `description`，供前端直接展示选择器。旧的 Nacos 字符串列表写法仍可读取，但不会提供独立的中文展示名和描述。

创建会话示例：

```json
{
  "agent_id": "chatbot",
  "thread_id": null,
  "voice": "Cherry",
  "language": "zh",
  "turn_detection": "server_vad"
}
```

`voice` 可省略；省略时后端采用 `default_voice`。传入的值必须来自 `capabilities.data.voices`，否则返回 422。

本地唤醒创建会话时还需提交 `"activation":"wake_word"`、能力接口下发的 `wake_word`、客户端引擎标识 `wake_engine` 和已缓存的 `pre_roll_samples`。默认完整唤醒词为“小美小美”，允许空格、标点、短暂停顿及三个或更多连续呼喊；单声不完成确认。旧配置及旧客户端元数据“小美”会归一为“小美小美”，ASR 仍需双声确认。默认缓存 2200 ms 前滚音频；已有显式 `pre_roll_ms: 1200` 的部署应同步调整，避免截断第一声。

后端限制前滚长度，用百炼 ASR 最终文本二次确认，并兼容两声被拆为相邻两段文本。匹配后发出 `wake.accepted`，其中新增可选 `command` 字段是剥离全部开头呼喊后的业务文本（无问题时为空字符串）；客户端应优先采用该字段展示用户问题。确认后继续呼喊的残留片段也会剥离，避免再次触发回答。明确不匹配或自 ASR 连接就绪后默认 10 秒仍未确认时，发出 `wake.rejected` 后关闭连接并释放会话容量，客户端恢复待机。按钮启动保持 `"activation":"tap"`，不得携带唤醒元数据。

确认唤醒后，后端通过原有 `response.started` → `text.delta` / 音频分段 → `response.done` 播报“我在，小美随时为你服务。”，使用会话所选音色，支持现有取消和播放确认。仅呼喊或呼喊后只有语气词时，生成无业务输入、无 Agent run 的语音轮次，不调用聊天模型，也不写入 LangGraph 对话；同时提出问题时，先播报应答再流式回答，传给 Agent 的输入只含问题。普通按钮会话不主动播报此应答。

应答与超时分别由 Nacos `voice.wake.greeting`、`voice.wake.confirm_seconds` 控制；不使用 Nacos 时对应 `VOICE_WAKE_GREETING`、`VOICE_WAKE_CONFIRM_SECONDS`。默认超时为 10 秒，可配置 2–30 秒；应答长度为 1–100 字符。App 与后端应同时更新以完整启用新体验。

响应的 `data` 包含 `session_id`、最终采用的 `thread_id`、过期时间和协议版本。默认每个用户最多有两个未过期会话；整个进程的 WebSocket 容量由 `VOICE_MAX_SESSIONS` 限制。

## 建立 WebSocket

连接地址：

```text
wss://<host>/voice/sessions/<session_id>/ws
Authorization: Bearer <access_token>
```

连接成功后，第一条消息必须是文本 JSON：

```json
{
  "type": "session.configure",
  "event_id": "客户端生成的唯一ID",
  "protocol_version": 1,
  "input_format": "pcm16_16000_mono",
  "output_format": "pcm16_24000_mono"
}
```

后端完成百炼 ASR 握手后返回 `session.ready`，其中的 `connection_id` 是本次连接 epoch。收到它以后才能发送二进制音频。重连会得到新的 `connection_id`，App 必须清空旧连接的音频缓冲。

## 二进制音频帧

网络字节序的大端帧头固定为 45 字节：

| 偏移 | 长度 | 字段 |
| ---: | ---: | --- |
| 0 | 4 | ASCII `VCE1` |
| 4 | 1 | `kind`：上行 `1`，下行 `2` |
| 5 | 16 | `connection_id` UUID 原始字节 |
| 21 | 16 | `response_id` UUID 原始字节；上行固定全零 |
| 37 | 4 | `segment_index`；上行固定 `0` |
| 41 | 4 | `sequence`，无符号递增序号 |
| 45 | ≤6400 | PCM16 little-endian 单声道数据，必须为偶数字节 |

上行是 16 kHz PCM16，首帧序号为 `0`，同一连接严格逐一递增。建议 App 每 20–40 ms 发送一帧并按实时速度上传；后端会拒绝突发灌入的预录音频。下行是 24 kHz PCM16；App 按帧头中的 `connection_id` 和 `response_id` 过滤迟到数据。

Dart 可用 `ByteData` 按 `Endian.big` 写帧头，PCM payload 保持设备重采样后的小端样本，不要对 payload 再做字节序转换。

## App 控制事件

所有事件都要带 1–128 字符的唯一 `event_id`。重复事件会返回 `event.ack` 或被轮次持久化层去重。

| `type` | 关键字段 | 行为 |
| --- | --- | --- |
| `input.text` | `text` | 跳过 ASR，直接提交一轮 Agent，适合调试或文字输入 |
| `input.commit` | 无 | 仅 `manual` 模式提交当前 ASR 音频 |
| `input.speech_hint` | 无 | App 本地检测到疑似插话；若未被服务端确认，约 800 ms 后收到 `playback.resume` |
| `audio.metrics` | `frames`、`rms_dbfs`、`peak_dbfs`、`clipped_samples`、AEC/降噪状态与 `mode` | 汇总端侧音频质量和设备 DSP 实际启用状态 |
| `response.cancel` | `response_id` | 明确废弃该回答，App 同时立即停播并清缓冲 |
| `playback.progress` | `response_id`、`segment_index`、`played_samples` | 报告实际播放采样位置，只能单调增加 |
| `playback.finished` | `response_id` | 确认完整回答已经播放完 |
| `approval.submit` | `interrupt_id`、`value` | 恢复指定 LangGraph 业务中断 |
| `ping` | 无 | 返回 `pong` |
| `session.close` | 无 | 正常结束会话 |

`server_vad` 模式持续发送麦克风流，包括判定句末所需的静音。百炼确认有效的最终转写后，后端只提交一次 Agent。只有空白、Unicode 标点（如 `。`、`.`、`...`、`，？！`）或已有语气词过滤规则命中的 ASR 中间/最终结果会下发为空文本，不创建业务轮次、不写入对话，也不取消正在播报的回答；唤醒确认期间遇到这类结果仍继续等待有效唤醒词。正常句子保留原文及标点，例如“你好。”和“C++ 的 1.5 版本怎么用？”。`input.text` 手动文字输入不受这条 ASR 过滤规则影响。

播放期间仍可上传经过 AEC 的麦克风音频；ASR `speech_started` 仅发出 `input.started`，收到有效的最终转写并接受新输入后才使当前回答失效，发出 `response.cancelled`。本地能量 VAD 的 `input.speech_hint` 仍可暂时暂停播报，未被确认时约 800 ms 后恢复。

## 后端事件

JSON 事件通常包含 `event_id`、`sequence`、`session_id` 和 `connection_id`。主要事件为：

- 会话：`session.ready`、`session.closed`、`pong`、`error`
- 唤醒：`wake.accepted`、`wake.rejected`
- 输入：`input.started`、`input.ended`、`transcript.partial`、`transcript.final`、`input.accepted`
- 回答：`response.started`、`text.delta`、`audio.segment.started`、`audio.segment.done`、`response.cancelled`、`response.done`
- Agent：`tool.started`、`tool.finished`、`agent.custom`、`approval.required`

`response.done` 表示生成/TTS 流程结束，不表示用户已经听完。事件中的 `execution_status` 表示 Agent 执行结果，`audio_status` 表示 TTS 交付结果，两者独立；例如 Agent 已生成完整文字但 TTS 失败时，`execution_status` 为 `completed`、`audio_status` 和总 `status` 为 `failed`。App 应让已经进入播放缓冲的音频自然播完，并在原生播放器确认结束后再进入监听状态。只有 App 上报 `playback.finished` 或完整的 `playback.progress` 后，后端才记录已播放。`response.cancelled.execution_status` 单独说明 Agent 是否也已取消；含外部工具的复杂 Agent 可能继续完成业务执行，因此 App 不应把停播解释为事务回滚。

后端对 Agent → TTS 和 TTS → WebSocket 使用有界队列进行自然背压。队列暂时满载不代表失败，也不会丢弃剩余文字；只有上游 TTS 明确失败、回答被取消或连接关闭时才停止语音交付。

百炼确认文本提交不代表已经生成音频。每段等待首包 PCM 最多 5 秒（若 `VOICE_UPSTREAM_TIMEOUT` 更短则采用该值），期间收到提交确认等控制事件不会重置期限。尚未向 App 下发任何 PCM 的分段若超时或供应商任务暂时失败，后端关闭旧 TTS 连接并重连，原段最多尝试 3 次；重试沿用同一个分段编号，成功后继续剩余文字。`InvalidParameter`、`InvalidApiKey`、`AccessDenied` 不重复请求。日志记录安全的供应商错误码、分段编号、字符数和已发送采样数，不记录原文或原始错误详情。已经下发部分 PCM 的分段不自动重试，避免重复播报；重试耗尽或其他合成错误仍返回 `tts_failed`，保留完整文字及已收到的音频。取消与会话关闭会中止重试。

Agent token 与 TTS 合成并行进行。文字增量立即下发，独立语音任务负责断句：短逗号不阻塞后续句号，首段最多先积累 24 字；首段之后合并队列中已经到达的相邻文字，在 160 字以内选择较后的断句边界，减少重复开启合成任务和短句间的停顿。没有可用断句时，首段缓冲约 180 ms 后提交，后续段约 400 ms 后提交，不等待 Agent 或 Mem0 写入结束。这些时间只约束文本缓冲，不包含 TTS 连接、排队、合成、网络和播放器耗时，不能解释为端到端首声保证。代码围栏和不含字母、汉字或数字的孤立标点、表情片段不送入 TTS；仅有句号、问号或右引号的增量不会中断后续朗读。取消和有界背压语义保持不变。

App 的自动能量检测只发送 `input.speech_hint`，不立即暂停外放；有效转录确认新输入后由 `response.cancelled` 停止旧回答。用户点击“我要插话”仍立即暂停，服务端约 800 ms 后可发送 `playback.resume`，客户端另有 1 秒恢复兜底。Android 暂停或输出缓冲满时保留尚未写入的 PCM，恢复后继续写入；取消、清空 AudioTrack 后同步重置播放计数，避免把计数归零误判为溢出并提前报告播完。

`response.done.latency_ms` 是可选诊断字段，旧客户端可忽略。`first_text_ms`、`tts_ready_ms`、`first_speech_ms`、`first_audio_ms`、`first_audio_sent_ms` 分别表示自服务端接受本轮输入后，到首个文字增量、TTS 握手完成、提交首段、生成首包 PCM、首包成功交给 WebSocket 的耗时；`text_to_audio_ms` 是首字到首包 PCM 的差值。没有发生的阶段不返回字段。它们使用服务端单调时钟，不代表手机已经出声，也不能与手机时钟直接相减。

客户端应在收到并交给播放器首包音频后再进入播报状态，不能仅凭 `text.delta` 标记开始播放。Android 12 及以上可独立降低 AudioTrack 启动阈值，本客户端请求 40 ms 的起播采样量，同时保留原有缓冲容量；最终阈值和实际声学延时取决于系统与音频路由。调试版客户端记录同一设备时钟下的首字、首包接收和原生入队耗时。最终声学首声、弱网连续性与蓝牙延时仍需目标真机测量。

控制事件使用优先队列。取消后仍可能有旧数据已经进入网络，App 必须立刻把对应 `response_id` 加入无效集合，并丢弃随后到达的文字、段事件和音频帧。

## 正常一轮

```text
App -> session.configure
API -> session.ready(connection_id)
App -> 上行 PCM 帧
API -> input.started / transcript.partial / input.ended / transcript.final
API -> input.accepted(turn_id, response_id)
API -> response.started(run_id)
API -> text.delta + audio.segment.started + 下行 PCM + audio.segment.done
API -> response.done
App -> playback.finished(response_id)
```

Flutter 对同一句最终转写不要再调用 `/stream`，否则 Agent 会执行两次。普通 HTTP、SSE、AG-UI 和语音入口共享线程锁与所有权；同一 `thread_id` 同时运行会返回冲突或中止该流。

## 错误与重连

鉴权和会话错误在 WebSocket 握手阶段映射为 `4401/4403/4404/4409/4410/4429`。协议错误会以 `session.closed(reason="invalid_protocol")` 结束。可恢复错误会发 `error`，常见 code 包括 `invalid_event`、`input_queue_full`、`approval_required`、`stale_approval`、`tts_failed` 和 `agent_failed`。

断线后可用原 `session_id` 重连，只要会话没有关闭或过期。重连会新建百炼实时连接和传输状态，不续播旧音频；业务 `thread_id` 和持久化历史继续保留。客户端重试 `input.text` 时复用原 `event_id`，后端会避免重复执行已保存的输入。

## Mem0 聊天记忆

同一语音会话中可以说“把我喜欢茶的记忆改成我喜欢咖啡”“删除我喜欢茶的记忆”或“清空我的所有记忆”。助手先播报目标及变更内容，用户再说“确认修改”“确认删除”或“确认清空”；多条候选先说“第几条”选择，或说“取消”结束。操作由认证绑定的当前用户执行，结果经现有 `text.delta` 和音频帧返回，不新增控制事件，不需要客户端调用记忆管理接口。待确认状态保存在本线程检查点中，五分钟有效；可在同一线程重连后继续。修改前会检查目标是否在确认期间发生变化。

也可以说“查询关于茶的记忆”“查询第二条记忆”或“我的记忆总共有多少条”。查询直接播报结果，总数覆盖当前账号的全部长期记忆。需要批量删除部分记忆时，先查询或列出记忆，再说“删除第一条和第三条记忆”，听到内容和数量预览后说“确认删除”。“批量删除关于茶的记忆”会先列出最多五条候选并要求指定编号；检索结果不会被当作该主题的全部记忆。清空全部仍使用“清空我的所有记忆 → 确认清空”。单条、批量及全部删除仅影响当前用户的 `mem0-chatbot` 长期事实，保留聊天记录和变更历史。

内部意图和候选匹配使用支持工具调用或原生结构化输出的聊天模型，其结构化 JSON 不进入语音流。普通聊天仍保持原有流式输出。重复 STT 完成事件沿用现有输入去重，插话继续取消失效播报；已经提交给 Mem0 同步线程的操作可能继续完成，不能把音频取消、断线或超时视为数据回滚。失败时助手会说明结果未确认，不宣称操作成功。详见 [对话式记忆管理及示例](Mem0_Memory.md#通过语音或文字管理记忆)。

`mem0-chatbot` 支持流式播报和插话取消，文字与语音可共享同一用户的长期事实。创建语音会话时显式传入当前选择的 `agent_id` 和对应线程 ID；`GET /voice/capabilities` 的 `default_agent` 返回新默认 agent，省略语音请求的 `agent_id` 仍保留原有 `chatbot` 行为。详见 [Mem0 配置与记忆生命周期](Mem0_Memory.md)。
