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
    version: "1.0.0"
    label: latest
    timeout_seconds: 20
```

这些字段映射为 `EMAIL_SKILL_ENABLED`、`EMAIL_SKILL_VERSION`、`EMAIL_SKILL_LABEL`
和 `EMAIL_SKILL_TIMEOUT_SECONDS`。默认禁用；设置 `version` 时优先下载该版本，
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
nacos-cli skill-review send-current-user-email --version 1.0.0
nacos-cli skill-describe send-current-user-email
```

审核是异步的。待目标版本审核通过后发布；若已自动上线，跳过发布：

```sh
nacos-cli skill-release send-current-user-email --version 1.0.0
nacos-cli skill-describe send-current-user-email
nacos-cli skill-get send-current-user-email --version 1.0.0 -o /path/to/skill-review
```

必须验证目标版本 `online`，不能以 `skill-list` 中出现名称作为上线成功的依据。
较早的 CLI 可能只有 `skill-publish`（仅上传草稿），应升级支持完整生命周期的 CLI，
或在控制台完成审核上线。不要绕过审核。后续修改应使用新版本并同步更新应用配置。
`skills/` 保存可审查的源码，运行时使用 Nacos 中的发布包，不能用本地源码替代下载。

## 语音使用

使用邮箱登录获得的用户令牌创建 `mem0-chatbot` 或 `chatbot` 语音会话，然后说：

> 给我发封邮件，主题是会议提醒，正文是明天下午三点开会。

缺少主题或正文时会追问，草稿只在当前线程保存十分钟，换用户不会沿用。
说“取消”会清除草稿。普通邮箱查询、假设性讨论和引用他人的指令不应触发发送。
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

每用户十次/24 小时的限制与请求预留在 Redis 中原子完成。请求去重保留 24 小时，
重复提交同一请求应保持 `request_id` 不变。语音执行使用稳定的语音 turn 消息 ID。
客户端对全新语音请求使用新 ID；不同请求即使内容相同也视为新的发送请求。

SMTP 使用工作线程避免阻塞异步请求。取消语音无法撤回已被 SMTP 接受的邮件；
超时、中断或发送后 Redis 写入失败会保留不确定状态，先检查邮箱，不自动补发。
Redis 去重状态丢失或过期后不保证去重，SMTP 不提供跨系统的恰好一次投递保证。
日志记录技能名、请求摘要、状态和异常类型，不记录邮件内容、完整地址或凭据。

## 验证与参考

```sh
uv run pytest tests/agents/test_email_commands.py tests/service/test_email_skill.py tests/core/test_skills.py -q
uv run pytest tests/voice/test_realtime_service.py -k email -q
uv run pytest tests/service/test_nacos.py -q
```

语音自动化测试使用模拟的 ASR/TTS 和邮件服务，覆盖音频帧到语音反馈的链路，
不会发送真实邮件或调用收费模型。真实验收需使用登录用户在目标设备上发起一次
明确的主题/正文请求，并检查邮箱；不在测试脚本中硬编码真实邮箱或授权码。

实现参考 [Agent Skills 规范](https://agentskills.io/specification)、
[Nacos Skill 管理](https://nacos.io/en/docs/latest/manual/user/ai/skill-registry/)、
[Nacos CLI 使用指南](https://nacos.io/en/docs/latest/manual/admin/nacos-cli/) 和
[Python EmailMessage 文档](https://docs.python.org/3.13/library/email.message.html)。
