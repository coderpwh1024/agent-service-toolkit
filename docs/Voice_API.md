# 移动端实时语音 API

这套接口供独立 Flutter App 连接当前 FastAPI 后端。App 负责麦克风、AEC、本地唤醒、播放与界面；后端负责用户隔离、百炼实时 ASR/TTS、Agent、工具、会话和播放记录。协议版本为 `1`。

## 启用服务

至少配置：

```dotenv
DASHSCOPE_API_KEY=...
AUTH_SECRET=只供可信后台使用的随机管理密钥
APP_TOKEN_SECRET=至少32字符的随机签名密钥
VOICE_ENABLED=true
```

PostgreSQL 是必需依赖。服务启动时会幂等创建 `app_thread_owners`、`app_runs`、`voice_sessions` 和 `voice_turns`。生产环境使用 HTTPS/WSS，并让反向代理允许 WebSocket Upgrade，空闲超时需大于 `VOICE_IDLE_SECONDS`。

百炼上游默认直连，不继承操作系统代理。确实需要 HTTP 代理时显式配置 `VOICE_REALTIME_PROXY`；SOCKS 代理还需在部署镜像中安装 `websockets` 所需的 SOCKS 可选依赖。

移动 App 通过 `POST /auth/email/code` 和 `POST /auth/email/verify` 完成邮箱验证码登录，详见[账号与认证说明](Accounts_and_Credentials.md)。验证成功后直接返回用户短期令牌。兼容的可信后台也可以配置 `AUTH_SECRET`，在自行验证用户后调用：

```http
POST /auth/token
Authorization: Bearer <AUTH_SECRET>
Content-Type: application/json

{"user_id":"user-123"}
```

返回的 `access_token` 是带 `sub`、`exp`、`aud` 和 `iss` 的短期 HS256 JWT。Flutter 后续 HTTP 和 WebSocket 握手都使用 `Authorization: Bearer <access_token>`。Token 用户不能读取其他用户的线程、语音会话、运行或反馈记录。

## HTTP 接口

| 方法与路径 | 用途 |
| --- | --- |
| `GET /voice/capabilities` | 获取协议、采样格式、模型、音色和 Agent 语音能力 |
| `GET /voice/protocol` | 获取 App → 后端控制事件的 JSON Schema |
| `POST /voice/sessions` | 创建语音会话，可绑定已有 `thread_id` |
| `GET /voice/sessions/{session_id}` | 查询会话及重连状态 |
| `GET /voice/sessions/{session_id}/turns` | 查询该语音会话的轮次和播放记录 |
| `DELETE /voice/sessions/{session_id}` | 幂等关闭会话，不删除聊天历史 |

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

响应包含 `session_id`、最终采用的 `thread_id`、过期时间和协议版本。默认每个用户最多有两个未过期会话；整个进程的 WebSocket 容量由 `VOICE_MAX_SESSIONS` 限制。

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
| `response.cancel` | `response_id` | 明确废弃该回答，App 同时立即停播并清缓冲 |
| `playback.progress` | `response_id`、`segment_index`、`played_samples` | 报告实际播放采样位置，只能单调增加 |
| `playback.finished` | `response_id` | 确认完整回答已经播放完 |
| `approval.submit` | `interrupt_id`、`value` | 恢复指定 LangGraph 业务中断 |
| `ping` | 无 | 返回 `pong` |
| `session.close` | 无 | 正常结束会话 |

`server_vad` 模式持续发送麦克风流，包括判定句末所需的静音。百炼确认最终转写后，后端只提交一次 Agent。播放期间仍可上传经过 AEC 的麦克风音频；后端收到 ASR `speech_started` 会使当前回答失效并发出 `response.cancelled`。

## 后端事件

JSON 事件通常包含 `event_id`、`sequence`、`session_id` 和 `connection_id`。主要事件为：

- 会话：`session.ready`、`session.closed`、`pong`、`error`
- 输入：`input.started`、`input.ended`、`transcript.partial`、`transcript.final`、`input.accepted`
- 回答：`response.started`、`text.delta`、`audio.segment.started`、`audio.segment.done`、`response.cancelled`、`response.done`
- Agent：`tool.started`、`tool.finished`、`agent.custom`、`approval.required`

`response.done` 表示生成/TTS 流程结束，不表示用户已经听完。只有 App 上报 `playback.finished` 或完整的 `playback.progress` 后，后端才记录已播放。`response.cancelled.execution_status` 单独说明 Agent 是否也已取消；含外部工具的复杂 Agent 可能继续完成业务执行，因此 App 不应把停播解释为事务回滚。

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
