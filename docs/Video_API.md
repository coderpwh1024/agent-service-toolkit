# HappyHorse 视频生成

视频功能由独立的 `src/video/` 模块维护，`src/service/video.py` 负责路由和生命周期适配，提供文生视频、查询并回写数据库、七牛云持久转存和后台同步。默认关闭，不影响现有聊天、语音和头像功能。没有新增生产依赖，复用项目已有的 httpx、psycopg 和七牛 SDK。

接口依据 [HappyHorse 官方文生视频文档](https://docs.bailian.console.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference)，同时核对了[阿里云帮助中心版本](https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference)。阿里云任务 ID 和成功结果 URL 均有 24 小时有效期；任务创建成功后只查询原任务，不重复提交。七牛使用 [SDK 的 fetch 抓取接口](https://developer.qiniu.com/kodo/api/fetch)把生成的视频复制到对象存储，不把视频完整缓存在应用内存中。

## PostgreSQL 表设计

DDL 位于 [create_video_generation_tasks.sql](../src/video/sql/create_video_generation_tasks.sql)。启动启用的视频模块时，在 PostgreSQL 中幂等创建新表和索引；不修改或删除现有表。并发启动的建表操作通过事务级 advisory lock 串行化。部署账号需要建表、索引和注释权限；也可由管理员提前执行此 DDL。

| 字段 | PostgreSQL 类型 | 含义 |
| --- | --- | --- |
| `id` | BIGINT IDENTITY PRIMARY KEY | 本地业务记录 ID |
| `user_id` | BIGINT NOT NULL | 所属用户，绑定应用令牌中的数字用户 ID |
| `idempotency_key` | VARCHAR(128) NOT NULL | 用户范围内的幂等键 |
| `request_hash` | VARCHAR(64) NOT NULL | 规范化入参的 SHA256 摘要 |
| `request_payload` | JSONB NOT NULL | 校验后的完整生成参数；包含敏感提示词 |
| `task_id` | VARCHAR(128) NULL | 阿里云异步任务 ID；提交前为空 |
| `status` | VARCHAR(32) NOT NULL | 视频生成状态枚举，由 CHECK 约束 |
| `storage_status` | VARCHAR(16) NOT NULL | 七牛转存状态枚举，由 CHECK 约束 |
| `aliyun_video_url` | TEXT NULL | 阿里云临时视频链接，含签名，不记入日志 |
| `qiniu_video_url` | TEXT NULL | 七牛云持久链接，确认转存成功后写入 |
| `qiniu_object_key` | TEXT NULL | 固定对象键 `videos/happyhorse/{task_id}.mp4` |
| `provider_request_id` | TEXT NULL | 阿里云请求追踪 ID |
| `error_code` | TEXT NULL | 安全错误分类，不保存原始错误正文 |
| `next_poll_time` | TIMESTAMPTZ NOT NULL | 下一次可轮询时间，避免失败任务占满批次 |
| `create_by`、`update_by` | BIGINT NOT NULL | 创建人、最后更新人；后台更新使用服务账号 ID |
| `create_time`、`update_time` | TIMESTAMPTZ NOT NULL | 插入默认当前时间，每次更新显式刷新 `update_time` |
| `is_delete` | SMALLINT NOT NULL DEFAULT 0 | 逻辑删除标记，CHECK 限制为 0/1 |

约束与索引：

- `(user_id, idempotency_key)` 对有效记录具有部分唯一索引，并发重复提交只保留一条记录。
- `task_id` 的非空值全局唯一，包含逻辑删除记录，避免同一上游任务归属多个业务记录。
- 待同步记录按 `(next_poll_time, id)` 建部分索引，查询、更新均限定 `is_delete = 0`。
- `STORED` 状态必须同时具备生成成功状态、七牛对象键和七牛 URL。
- 不增加用户外键：现有认证也支持可信后端签发的数字用户令牌，视频模块可以独立于邮箱注册模块启用；所有权由服务认证和 SQL 用户条件约束。审计用户 ID 直接沿用现有 BIGINT 用户约定。

生成枚举：`SUBMITTING`（本地已预留）、`SUBMISSION_UNKNOWN`（提交结果待核实）、`PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELED`、`UNKNOWN`（上游任务不存在或已过期）。转存枚举独立为 `NOT_READY`、`PENDING`、`STORED`、`FAILED`；因此生成成功但转存失败仍保留 `SUCCEEDED`，不会重新生成视频。

## 配置和启用

所有配置经 `Settings` 进入服务。环境变量示例见根目录 `.env.example`。可配置：

```dotenv
VIDEO_ENABLED=true
VIDEO_API_BASE_URL=https://your-workspace-id.cn-beijing.maas.aliyuncs.com/api/v1
DASHSCOPE_API_KEY=replace-with-matching-region-api-key
AUTH_SECRET=replace-with-trusted-service-secret
APP_TOKEN_SECRET=replace-with-at-least-32-random-characters
AUTH_SERVICE_ACCOUNT_ID=0
QINIU_ACCESS_KEY=replace-with-qiniu-access-key
QINIU_SECRET_KEY=replace-with-qiniu-secret-key
QINIU_BUCKET_NAME=replace-with-bucket-name
QINIU_PUBLIC_BASE_URL=https://cdn.example.com
LOG_LEVEL=INFO
```

`VIDEO_API_BASE_URL` 与聊天的 OpenAI 兼容地址分开，必须使用阿里云原生 `/api/v1` 地址。默认保留可用的北京旧域名 `https://dashscope.aliyuncs.com/api/v1`；推荐配置业务空间专属域名。API Key、模型和 Endpoint 必须同地域。启用时缺少认证、阿里云或七牛配置会导致启动失败，不回退到内存或其他数据库。

七牛配置与已有头像功能共用。Bucket 需要允许服务账号执行 `stat` 和 `fetch`，并允许七牛访问阿里云签名链接。`QINIU_PUBLIC_BASE_URL` 应指向能公开访问该 Bucket 的持久域名；私有 Bucket 的签名下载链接不是当前接口支持的配置。对象存储生命周期应保留 `videos/happyhorse/`，不要配置自动过期删除。需要区分头像和视频生命周期时可在七牛按前缀配置规则。

Nacos 支持以下视频配置，密钥仍使用已有的模型、认证和 `storage.qiniu` 配置：

```yaml
video:
  enabled: true
  api_base_url: https://your-workspace-id.cn-beijing.maas.aliyuncs.com/api/v1
```

Compose 已接入视频模块环境变量、开发同步目录；服务 Dockerfile 已包含模块及 DDL。部署后需重建服务镜像并重启，启用的服务会创建轮询任务。

## 生成视频

`POST /videos`，返回 HTTP 202 和统一 `ApiResponse[VideoTask]`。必须携带应用用户 Bearer 令牌和 `Idempotency-Key`。可信 `AUTH_SECRET` 只用于签发应用令牌，不能直接作为视频接口用户身份；本模块要求令牌 `sub` 为有效的正 BIGINT 用户 ID。无需在 body 中传 `user_id`。

```sh
curl -X POST "$AGENT_URL/videos" \
  -H "Authorization: Bearer $APP_ACCESS_TOKEN" \
  -H 'Idempotency-Key: video-order-20261022-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "夕阳下，一匹马沿海边缓慢奔跑，镜头平稳跟随",
    "model": "happyhorse-1.1-t2v",
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5,
    "watermark": false,
    "seed": 42
  }'
```

支持 `happyhorse-1.1-t2v` 和 `happyhorse-1.0-t2v`；分辨率支持 480P/720P/1080P，1.0 不接受 480P。时长为 3–15 秒整数；seed 可省略，范围 0–2147483647。宽高比、提示词长度等按 schema 验证。默认 1.1、1080P、16:9、5 秒、`watermark=false`（不添加水印）。已有记录保留原参数；显式传入的水印参数仍按请求执行。

成功接收后 `data.id` 是查询使用的本地记录 ID，`data.task_id` 是阿里云任务 ID。响应同时提供 `status`、`storage_status`、两个独立 URL 字段和安全错误分类；不暴露幂等键、用户提示词、供应商完整响应或鉴权信息。HTTP 202 表示已处理异步提交请求，业务是否已被上游接受应检查 `data.status`。

## 分页查询视频列表

新增 `GET /videos`，沿用应用用户 Bearer 令牌和统一 `ApiResponse`。不需要 `Idempotency-Key`，用户身份从令牌绑定，不接受 `user_id` 查询参数。只返回当前用户未逻辑删除的记录。

| 查询参数 | 默认值 | 说明 |
| --- | --- | --- |
| `page` | `1` | 页码，从 1 开始，最大 2147483647 |
| `page_size` | `20` | 每页条数，范围 1–100 |
| `status` | 不筛选 | 视频生成状态，如 `SUCCEEDED`、`RUNNING`、`PENDING`、`FAILED` |
| `storage_status` | 不筛选 | 七牛转存状态：`NOT_READY`、`PENDING`、`STORED`、`FAILED` |

不传状态返回本人全部有效任务；查看已经生成的视频传 `status=SUCCEEDED`，查看已转存至七牛的视频可同时传 `storage_status=STORED`。两个状态参数同时提供时按 AND 筛选；允许的完整生成状态见前面的表设计。非法枚举、分页范围或未知参数返回 422。

```sh
curl "$AGENT_URL/videos?page=1&page_size=20&status=SUCCEEDED&storage_status=STORED" \
  -H "Authorization: Bearer $APP_ACCESS_TOKEN"
```

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "page": 1,
    "page_size": 20,
    "total_pages": 0
  }
}
```

有结果时 `items` 中每条数据沿用原有 `VideoTask` 结构，包含本地 ID、阿里云任务 ID、生成状态、转存状态、两个独立视频 URL 和创建、更新时间，不返回提示词和幂等键。列表固定按 `create_time DESC, id DESC` 排序，创建时间相同的记录再按 ID 倒序。`total` 为符合当前用户和状态条件的总条数，`total_pages` 为向上取整的总页数；请求超过最后一页时仍返回 200、空 `items` 和实际总条数。

列表只读取数据库，不触发阿里云查询、七牛转存或修改记录。最新状态由现有单条查询接口和定时同步回写；需要立即刷新某个任务时继续调用 `GET /videos/{record_id}`。总数和分页数据使用同一只读事务快照，避免单次响应内因并发更新造成统计不一致。此接口无需新增或修改数据库字段，原有生成和单条查询接口保持原行为。

## 查询和自动回写

```sh
curl "$AGENT_URL/videos/123" \
  -H "Authorization: Bearer $APP_ACCESS_TOKEN"
```

`GET /videos/{record_id}` 只允许查询本人记录。不存在、逻辑删除或他人记录统一返回 404。任务正在被另一个查询或定时同步处理时返回 409，可稍后重查。

对于排队或生成中的任务，服务按数据库保存的 `task_id` 请求阿里云查询接口，回写任务状态及临时 URL。拿到成功 URL 后，先保存源地址，再调用七牛抓取；七牛确认对象存在后，写入对象键、七牛 URL 和 `STORED`。已转存完成或上游已终止的记录直接返回数据库结果。查询上游失败时保留原生成状态，保存 `QUERY_FAILED`，供后续重试。

后台任务启动后立即检查一次，此后按 300 秒周期运行；每批最多 10 条，按最早到期记录优先。同步排队、生成中、提交中断以及生成成功但未完成转存的任务。每次尝试将下次处理时间推迟 5 分钟，避免前 10 条失败任务持续阻塞队列。调度领导者、批处理和单任务分别使用 PostgreSQL session advisory lock，多个 worker 只有一个定时领导者，查询与定时任务不会同时转存同一记录。锁不持有数据库事务，外部 HTTP 请求不会延长数据库事务。关闭服务时取消后台任务并关闭客户端和连接池；重启后从数据库恢复。

七牛抓取采用现有同步 SDK，通过 `asyncio.to_thread` 执行。应用不阻塞事件循环，也不下载整个视频到进程内存。已成功抓取但数据库更新中断时，下次同步先 `stat` 固定对象键，确认存在后补写数据库；重复查询不会创建随机副本。服务器中断不会丢失已有任务 ID 或成功源地址。超出有效期才恢复服务的任务可能得到 `UNKNOWN`；若源链接已过期且七牛无副本，则转存可能无法恢复，需人工处理。

## 幂等、错误和运维

相同用户、相同幂等键、相同参数返回同一记录；同键不同参数返回 409。客户端网络重试必须使用原幂等键，seed 相同并不等于任务幂等。幂等键只接受 1–128 位字母、数字、下划线和连字符，按记录生命周期保留。

阿里云当前接口文档没有承诺创建请求的幂等 Header。本模块先提交 PostgreSQL 幂等预留，再调用一次创建接口；不自动重试创建 POST。若请求超时、连接中断、上游 5xx、无法解析成功响应，或进程在提交期间退出，记录进入 `SUBMISSION_UNKNOWN`。该状态阻止同幂等键再次提交，防止重复计费；不能声称在两个独立系统之间提供绝对 exactly-once 保证。

待核实记录需要运维在阿里云控制台确认是否已创建任务：先读取并核对本地记录、用户、请求时间和参数，再在受控事务中绑定经过确认的任务 ID、恢复 `PENDING` 并同步审计字段。不要把未经核实的其他用户任务 ID 写入记录。只有确认上游没有创建任务后，业务方才能有意识地用新幂等键发起新任务。原记录应保留供审计；普通失败记录也不会自动重新计费生成。

错误分类包括 `SUBMISSION_UNCERTAIN`、`SUBMISSION_REJECTED`、`QUERY_FAILED`、`GENERATION_FAILED`、`GENERATION_CANCELED`、`GENERATION_UNKNOWN`、`VIDEO_URL_MISSING` 和 `ARCHIVE_FAILED`。生成与转存状态分别判断，只有 `storage_status=STORED` 才表示七牛持久链接可用。转存失败会继续按固定对象键重试，不触发重新生成。

日志使用中文参数化消息，包含记录 ID、任务 ID、状态、生成参数摘要及是否返回 URL。原始提示词、幂等键、密钥、Authorization 和签名 URL 不打印；错误记录安全分类及异常类型。`LOG_LEVEL=INFO` 可查看正常入参、出参日志。

## 验证

```sh
uv run pytest tests/video -q
uv run ruff format --check .
uv run ruff check .
uv run pyrefly check
./scripts/smoke_test.sh postgres
```

PostgreSQL 目标已包含 `tests/smoke/test_video_postgres.py`，在独立测试 schema 中检查真实唯一约束、状态约束、审计字段、跨连接锁、分页轮询公平性及逻辑删除。它不会提交真实生成任务，也不会调用七牛。实际阿里云生成和七牛转存需要匹配地域的有效凭据，会产生费用，应在部署环境做一次真实验证。

## Nacos 视频 Skill

`manage-user-videos` 为独立运行时技能，源码位于 [SKILL.md](../skills/manage-user-videos/SKILL.md)，受控实现位于 `src/skills/video.py`。它使用现有 `VideoService`，保留三个 HTTP 接口、既有表结构、任务同步和七牛转存流程。HTTP 创建请求默认水印改为 `false`；显式参数仍受现有接口支持。

在当前 Nacos 应用配置中合并以下内容，保留其他配置：

```yaml
video:
  enabled: true
skills:
  video:
    enabled: true
    version: "1.0.0"
    label: latest
    timeout_seconds: 20
```

映射为 `VIDEO_SKILL_ENABLED`、`VIDEO_SKILL_VERSION`、`VIDEO_SKILL_LABEL` 和 `VIDEO_SKILL_TIMEOUT_SECONDS`。默认禁用，独立于视频 HTTP 服务。指定版本优先，否则通过标签下载。启用时必须从当前 Namespace 的 Nacos Skill 管理中心下载有效包，不能回退到本地文件。配置和技能在启动时加载，修改后重启服务。语音还需现有 `VOICE_ENABLED` 和 ASR/TTS 配置。

### 发布与校验

CLI 的连接地址和 Namespace 必须与应用一致；密码留在受保护的 CLI Profile 或既有引导配置，不写入命令行或技能包。

```sh
nacos-cli skill-upload skills/manage-user-videos
nacos-cli skill-describe manage-user-videos --output json
nacos-cli skill-review manage-user-videos --version 1.0.0
nacos-cli skill-describe manage-user-videos --output json
```

待目标版本通过异步评审后发布；如果已经在线则跳过发布。后续更新使用新版本，不覆盖已发布版本。

```sh
nacos-cli skill-release manage-user-videos --version 1.0.0
nacos-cli skill-get manage-user-videos --version 1.0.0 -o /path/to/skill-review
```

最终核对目标版本为 `online`，并对比下载后的 `SKILL.md` 摘要与仓库文件一致。仅上传草稿不代表已经可被运行时加载。

### 对话流程与能力

`chatbot` 和 `mem0-chatbot` 在邮件、记忆指令处理后调用视频 Skill；语音转写与文字共用入口。没有视频关键词正则门槛，启用后每轮通过受控的结构化识别决定是否处理，普通对话返回 `none` 后沿用原聊天流程。识别调用带 `skip_stream`，结构化 JSON 不进入播报。

技能接收当前输入、最近十二条对话（总计最多 12000 字符）、本人最近任务引用、最近列表编号以及十分钟有效的草稿。动态输入和远程技能均为参考数据，不能覆盖授权规则。仅本轮明确生成、编辑或补充待提交需求才允许提交；历史、记忆和助手建议不能独立授权。不会自动检索影视素材、原声或长期记忆。

| 可以说的话 | 执行行为 |
| --- | --- |
| “生成五秒海边日落的视频” | 创建无水印视频任务，返回本地编号 |
| “生成十秒视频” → “海边日落” | 收集内容，保留十秒要求后提交 |
| “按上面描述生成三十秒” → “改成十五秒” | 解释时长限制，保留场景，用户修正后提交 |
| “刚才的视频好了吗”“查询编号三” | 查询本人单条任务并回写状态 |
| “我有哪些视频”“列出已生成的视频” | 查询本人分页列表，成功条件为 `SUCCEEDED` |
| “下一页”“查询第二条” | 保留上一页过滤条件，或从最近真实列表选记录 |
| “我现在有几个视频” | 查询数据库总数，不从聊天推断 |
| “把编号三改成水墨画风，保留原声” | 对已完成并转存的视频新建编辑任务 |
| “取消” | 只取消未提交草稿，不撤回已有任务 |

语音列表先播报至多五条，完整当前页通过事件返回。列表序号是当前页从一开始的顺序，不等于记录 ID。空列表、零总数、超出末页和错误编号均明确说明。分页排序、状态枚举和用户隔离沿用现有服务；不支持按日期或主题过滤，不忽略这些条件后查询全部。

### 编辑边界与持久化

编辑使用 [HappyHorse 视频编辑 API](https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference) 的 `happyhorse-1.0-video-edit`，支持文字指令下的风格变换和局部替换。来源限定为本人已生成且转存至七牛的视频，URL 由服务查表获得，不采用模型或用户提供的任意素材地址。暂不支持参考图、外部视频、精确裁剪、拼接、字幕、声纹克隆或改变源视频时长与比例。

输出支持 720P/1080P，默认 1080P；`audio_setting=auto` 由模型控制，`origin` 保留原声。来源为项目生成的 3–15 秒视频，宽高比需在 1:2.5 至 2.5:1；素材格式、大小、尺寸和帧率仍由供应商检查，不保证所有编辑均成功。新视频显式 `watermark=false`，不承诺移除源画面已有标识。

编辑新建任务，不覆盖源记录。现有 JSONB `request_payload` 保存编辑模型、源记录编号、源七牛 URL、编辑要求和参数，业务表不新增列。解析兼容旧的文生视频载荷；查询、列表、幂等、轮询和转存共用原流程。旧记录中的水印值不回填或改写。

### 状态、失败和客户端事件

技能只播报实际返回的任务状态。排队或生成中不能说已完成；生成成功和转存成功分别表达；提交不确定、超时、插话或断线后先查询原任务，不自动重新提交。缺少场景、编号或编辑要求时追问；三十秒等超范围参数解释原因并保留草稿，不静默缩短时长。未返回供应商具体失败原因时明确说明原因未知，不猜测审核或额度问题。

语音稳定消息 ID 与认证用户、线程生成内部幂等键，相同输入重放复用同一任务，重复 ASR 仍由现有输入去重拦截。用户主动重说属于新请求。身份不可用、服务失败和找不到本人记录分别给出登录、稍后查询或核对编号的提示。

结果沿用通用 `agent.custom` 通道，`data.type` 为 `video.task`、`video.list` 或 `video.count`：

```json
{
  "type": "video.list",
  "items": [
    {"record_id": 123, "status": "SUCCEEDED", "storage_status": "STORED", "qiniu_video_url": "https://cdn.example/video.mp4", "error_code": null}
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1,
  "query": {"page": 1, "page_size": 20, "status": null, "storage_status": null}
}
```

单条结果继续使用 `{"type":"video.task","video":{...}}`，`response_metadata.video` 保留相同单条摘要。数量事件包含 `total` 与过滤条件。列表、数量事件是通用通道的新增载荷，不改变语音事件顺序或音频协议。客户端根据事件展示七牛链接和分页控件，语音不朗读 URL，也不自动启动播放器。

日志用中文记录技能摘要、输入字数、操作、状态及安全错误类型，不打印完整场景、对话、凭据或签名 URL。识别模型仍具有误判可能；单元和模拟音频测试不能替代部署后对实际 ASR、模型权限和素材质量的验收。
