# 当前用户邮件发送技能

`send-current-user-email` 使用 Nacos Skill 管理分发技能，复用 Nacos 中的 SMTP
配置发送纯文本邮件。默认 `mem0-chatbot` 和 `chatbot` 均支持文字和语音触发。
其他研究、RAG 或后台智能体暂不接入该技能。

## 配置与加载

在 Nacos 的 `agent-service-toolkit.yaml` 合并以下配置，不要覆盖现有配置：

```yaml
skills:
  email:
    enabled: true
    version: "1.1.0"
    label: latest
    timeout_seconds: 20
    timezone: Asia/Shanghai
```

这些字段映射为 `EMAIL_SKILL_ENABLED`、`EMAIL_SKILL_VERSION`、`EMAIL_SKILL_LABEL`、
`EMAIL_SKILL_TIMEOUT_SECONDS` 和 `EMAIL_SKILL_TIMEZONE`。默认禁用；设置 `version` 时优先下载该版本，
否则按 `label` 下载。启用时必须从 Nacos 下载有效技能，没有本地回退。
配置和技能均在启动时加载，发布新版本、更新配置后重启服务。

还需启用现有的邮箱登录、PostgreSQL、Redis、用户访问令牌以及 SSL/STARTTLS SMTP。
例如 QQ 邮箱的服务配置为 `smtp.qq.com:465`、`use_ssl: true`、`use_tls: false`。
发件账号、授权码和 `from_email` 继续由 Nacos 的 `authentication.email.smtp`
管理，不写入技能包或日志；用户名、授权码必须成对配置。
语音另外要求 `voice.enabled: true` 和可用的 ASR/TTS 配置。

## 通过 Nacos CLI 发布与同步

先用 `nacos-cli --help` 核实本机命令，并确保 CLI Profile 的服务地址、Namespace
与应用的 Nacos 引导配置一致。新版 CLI 的发布流程：

```sh
nacos-cli skill-upload skills/send-current-user-email
nacos-cli skill-review send-current-user-email --version 1.1.0
nacos-cli skill-describe send-current-user-email
```

审核是异步的。待目标版本审核通过后发布；若已自动上线，跳过发布：

```sh
nacos-cli skill-release send-current-user-email --version 1.1.0
nacos-cli skill-describe send-current-user-email
nacos-cli skill-get send-current-user-email --version 1.1.0 -o /path/to/skill-review
```

必须验证目标版本 `online`，不能以 `skill-list` 中出现名称作为上线成功的依据。
较早的 CLI 可能只有 `skill-publish`（仅上传草稿），应升级支持完整生命周期的 CLI，
或在控制台完成审核上线。不要绕过审核。后续修改应使用新版本并同步更新应用配置。
`skills/` 保存可审查的源码，运行时使用 Nacos 中的发布包，不能用本地源码替代下载。

## 语音使用

使用邮箱登录获得的用户令牌创建 `mem0-chatbot` 或 `chatbot` 语音会话，然后说：

> 给我发封邮件，主题是会议提醒，正文是明天下午三点开会。

未指定发送时间时沿用立即发送。也可以说：

> 十分钟后给我发邮件，主题是会议提醒，正文是明天下午三点开会。
>
> 明天上午九点给我发邮件，主题是提醒，正文是记得开会。

指定时间必须满足 `当前时间 < send_at ≤ 当前时间 + 24 小时`，恰好24小时允许，
当前时刻、过去时间和超过24小时均拒绝；不会改成明天或退回立即发送。
“明天上午九点”是否可接受取决于请求当时的当前时间。
语音解析使用运行时当前日期和 Nacos 的 `skills.email.timezone`，默认北京时间
`Asia/Shanghai`，可改为有效 IANA 时区。含糊的发送时间会追问；正文提到的会议时间
不会被当成发送时间。预约反馈会读出完整日期、时刻和时区，并说明尚未发送。

缺少主题或正文时会追问，草稿只在当前线程保存十分钟，换用户不会沿用。
补充内容时保留已指定的发送时间，提交前重新校验时间窗口。
说“取消”只会清除尚未提交的草稿；已预约的邮件暂不支持语音取消或改期。普通邮箱查询、假设性讨论和引用他人的指令不应触发发送。
收件地址从当前登录用户的有效数据库记录读取，不接受口述收件人或模型提供的地址。
管理员令牌不能代替用户身份发送，客户端也不能覆写 `authenticated_user_id`。

语音沿用已有转写、`input_id` 去重、播放、插话取消和重连协议。新能力没有新增
WebSocket 事件；通过现有 `text.delta`、音频帧及最终 AI 消息返回结果。
`response_metadata.email_status` 记录邮件执行状态，规划阶段不输出语音内容。

## 接口与附带脚本

`POST /skills/email/send` 使用当前用户 Bearer 令牌，输入：

```json
{
  "request_id": "b05a3f57-6a42-42e1-8781-b94026222762",
  "subject": "会议提醒",
  "body": "明天下午三点开会。"
}
```

可选字段 `send_at` 为带 UTC 偏移的 ISO 8601 时间；例如
`2030-01-01T09:00:00+08:00` 仅展示格式，实际请求必须替换为未来24小时内的时刻。
省略或传 `null` 表示立即发送；无时区、格式错误或越界返回 422。
服务在读取用户前和队列入队前校验时间，不能只依赖模型或客户端校验。

主题为 1–200 个字符且禁止控制字符，正文为 1–20000 个字符且禁止 NUL。
不支持附件、HTML、抄送、密送、指定收件地址或用户 ID，多余字段返回 422。
邮件由 Python `EmailMessage` 编码为 UTF-8，并包含 From、To、Subject、Date 和 Message-ID。

技能包内 `scripts/send_email.py` 使用 Python 标准库调用相同接口。设置可信服务的
`AGENT_SERVICE_URL` 和当前用户的 `AGENT_USER_ACCESS_TOKEN`，从标准输入传入上面的
JSON。令牌不作为命令行参数传入，不允许跨地址重定向；远程地址必须使用 HTTPS，
仅允许本机使用 HTTP。服务进程使用内置实现，绝不执行从 Nacos 下载的脚本。

返回沿用 `ApiResponse` 格式，其中 `data.status` 为：

| 状态 | 含义 |
| --- | --- |
| `sent` | SMTP 已接受邮件，不代表已进入收件箱 |
| `duplicate` | 同一用户的同一请求已提交，本次没有重复发送 |
| `unknown` | 已保留请求，但投递结果不确定，不自动重试 |
| `scheduled` | 预约已入队，`data.send_at` 返回实际预约时间，尚未发送 |

每用户十次/24 小时的限制与请求预留在 Redis 中原子完成，预约在提交时占用一次额度，
到期发送不再计数。立即发送的请求去重保留24小时，预约请求和载荷保留到到期后24小时。
预约、载荷写入和队列入队在同一 Lua 操作中完成；重复请求不会新增任务或提前发送。

重复提交同一请求应保持 `request_id` 不变。语音执行使用稳定的语音 turn 消息 ID。
客户端对全新语音请求使用新 ID；不同请求即使内容相同也视为新的发送请求。

## 定时任务与恢复

QQ SMTP 当前未声明 RFC 4865 的 `FUTURERELEASE` 扩展，因此本项目在应用层调度，
不依赖 QQ 网页端的定时发送能力，也不把未来日期写入邮件头来假装定时发送。
后台任务随邮件服务启动，每秒检查 Redis sorted set 中的到期任务，单次最多处理十封。
每封投递前重新查询有效用户记录，使用当前注册邮箱；用户已删除则跳过。
Redis 原子认领保证多个服务实例不会同时投递同一任务。

请为 Redis 配置适当的持久化和访问隔离。队列与正文保存在 Redis，而非进程内计时器；
在 Redis 数据完整的前提下，应用重启后继续处理未过期的预约。排队、停机或网络故障
可能使实际发送晚于预约时间，不能承诺精确到秒；未处理载荷到期后24小时自动过期，
调度器清除对应队列项，避免无限期补发旧邮件。
用户查找暂时失败时保持任务在队列，下一轮再检查。投递认领后先标记为 `unknown`，
从队列移除并删除载荷，随后才发送；进程崩溃或取消后不自动再次投递，以避免重复邮件。
这是有意采用的至多一次投递尝试：认领后崩溃可能导致漏发，并非恰好一次保证。

SMTP 使用工作线程避免阻塞异步请求。取消语音无法撤回已被 SMTP 接受的邮件；
超时、中断或发送后 Redis 写入失败会保留不确定状态，先检查邮箱，不自动补发。
Redis 去重状态丢失或过期后不保证去重，SMTP 不提供跨系统的恰好一次投递保证。
日志记录技能名、请求摘要、状态和异常类型，不记录邮件内容、完整地址或凭据。

## 验证与参考

```sh
uv run pytest tests/agents/test_email_commands.py tests/service/test_email_skill.py tests/core/test_skills.py -q
uv run pytest tests/voice/test_realtime_service.py -k email -q
uv run pytest tests/service/test_nacos.py tests/service/test_email_scheduling.py -q
```

语音自动化测试使用模拟的 ASR/TTS 和邮件服务，覆盖音频帧到语音反馈的链路，
不会发送真实邮件或调用收费模型。真实验收需使用登录用户在目标设备上发起一次
明确的主题/正文请求，并检查邮箱；不在测试脚本中硬编码真实邮箱或授权码。

实现参考 [Agent Skills 规范](https://agentskills.io/specification)、
[Nacos Skill 管理](https://nacos.io/en/docs/latest/manual/user/ai/skill-registry/)、
[Nacos CLI 使用指南](https://nacos.io/en/docs/latest/manual/admin/nacos-cli/)、
[Python EmailMessage 文档](https://docs.python.org/3.13/library/email.message.html)、
[SMTP 定时释放扩展 RFC 4865](https://www.rfc-editor.org/rfc/rfc4865.html) 和
[Redis sorted sets](https://redis.io/docs/latest/develop/data-types/sorted-sets/)。
